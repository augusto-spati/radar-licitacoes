# Coleta do PNCP por janela (etapa 4b): o planejador (EventBridge Scheduler -> Lambda) enfileira
# uma mensagem `coletar_janela` por janela em radar-janelas; a Lambda radar-coletora coleta uma
# fatia de ate 540 s por invocacao e, se faltar pagina, a continuacao volta pela outbox.
# No maximo 2 coletoras ao mesmo tempo (cortesia com o PNCP). Tudo na regiao do projeto.

locals {
  env_coleta_pncp = {
    RADAR_DB_NULLPOOL      = "true"
    RADAR_CONFIG_FILTRO    = "/var/task/config/filtro.toml"
    RADAR_FILA             = "sqs"
    RADAR_FILA_URL         = aws_sqs_queue.editais.url
    RADAR_FILA_JANELAS_URL = aws_sqs_queue.janelas.url
  }
}

# ---------- Filas ----------

resource "aws_sqs_queue" "janelas_falhas" {
  name                      = "radar-janelas-falhas"
  message_retention_seconds = 1209600 # 14 dias (maximo do SQS)
}

resource "aws_sqs_queue" "janelas" {
  name = "radar-janelas"
  # 6 x o timeout da coletora (900 s). E tambem a espera entre tentativas: 1,5 h de folga
  # para um PNCP instavel, em vez de retentativa em laco curto.
  visibility_timeout_seconds = 5400
  message_retention_seconds  = 345600 # 4 dias (padrao)

  # Cada continuacao e uma mensagem nova: as 5 tentativas valem por fatia.
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.janelas_falhas.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "janelas_falhas" {
  queue_url = aws_sqs_queue.janelas_falhas.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.janelas.arn]
  })
}

# Todo processo que roda o relay pode enviar para as duas filas (FilaRoteada).
data "aws_iam_policy_document" "enviar_duas_filas" {
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.editais.arn, aws_sqs_queue.janelas.arn]
  }
}

# ---------- Lambda coletora (mesma imagem da API, outro handler) ----------

resource "aws_iam_role" "coletora" {
  name               = "radar-coletora-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "coletora_basico" {
  role       = aws_iam_role.coletora.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "coletora_banco" {
  name   = "ler-url-do-banco"
  role   = aws_iam_role.coletora.id
  policy = data.aws_iam_policy_document.api_banco.json
}

data "aws_iam_policy_document" "coletora_fila" {
  statement {
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.janelas.arn]
  }
}

resource "aws_iam_role_policy" "coletora_fila" {
  name   = "consumir-fila-de-janelas"
  role   = aws_iam_role.coletora.id
  policy = data.aws_iam_policy_document.coletora_fila.json
}

resource "aws_iam_role_policy" "coletora_enviar" {
  name   = "enviar-para-as-filas"
  role   = aws_iam_role.coletora.id
  policy = data.aws_iam_policy_document.enviar_duas_filas.json
}

resource "aws_cloudwatch_log_group" "lambda_coletora" {
  name              = "/aws/lambda/radar-coletora"
  retention_in_days = 14
}

resource "aws_lambda_function" "coletora" {
  function_name = "radar-coletora"
  role          = aws_iam_role.coletora.arn
  package_type  = "Image"
  image_uri     = local.imagem_api
  architectures = ["x86_64"]
  memory_size   = 256 # ~27% do free tier com 4 h/dia; revista na rodada real
  timeout       = 900 # teto da Lambda; o orcamento da fatia (540 s) cabe com folga

  image_config {
    command = ["radar.processador.lambda_coletora.handler"]
  }

  environment {
    variables = local.env_coleta_pncp
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda_coletora.name
  }

  # O deploy (workflow) troca a imagem depois da criacao; o Terraform nao desfaz.
  lifecycle {
    ignore_changes = [image_uri]
  }

  depends_on = [
    aws_iam_role_policy_attachment.coletora_basico,
    aws_iam_role_policy.coletora_fila,
  ]
}

resource "aws_lambda_event_source_mapping" "coletora" {
  event_source_arn        = aws_sqs_queue.janelas.arn
  function_name           = aws_lambda_function.coletora.arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]

  # Cortesia com o PNCP: no maximo 2 janelas em andamento (o minimo que o SQS aceita).
  scaling_config {
    maximum_concurrency = 2
  }
}

# ---------- Lambda planejadora ----------

resource "aws_iam_role" "planejador" {
  name               = "radar-planejador-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "planejador_basico" {
  role       = aws_iam_role.planejador.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "planejador_banco" {
  name   = "ler-url-do-banco"
  role   = aws_iam_role.planejador.id
  policy = data.aws_iam_policy_document.api_banco.json
}

resource "aws_iam_role_policy" "planejador_enviar" {
  name   = "enviar-para-as-filas"
  role   = aws_iam_role.planejador.id
  policy = data.aws_iam_policy_document.enviar_duas_filas.json
}

resource "aws_cloudwatch_log_group" "lambda_planejador" {
  name              = "/aws/lambda/radar-planejador"
  retention_in_days = 14
}

resource "aws_lambda_function" "planejador" {
  function_name = "radar-planejador"
  role          = aws_iam_role.planejador.arn
  package_type  = "Image"
  image_uri     = local.imagem_api
  architectures = ["x86_64"]
  memory_size   = 256
  timeout       = 120

  image_config {
    command = ["radar.processador.lambda_planejador.handler"]
  }

  environment {
    variables = local.env_coleta_pncp
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda_planejador.name
  }

  lifecycle {
    ignore_changes = [image_uri]
  }

  depends_on = [
    aws_iam_role_policy_attachment.planejador_basico,
    aws_iam_role_policy.planejador_enviar,
  ]
}

# ---------- Agenda: todo dia 06:00 de Brasilia ----------

# Confianca: data.aws_iam_policy_document.scheduler_assume (coletor.tf), com aws:SourceAccount.
resource "aws_iam_role" "scheduler_planejador" {
  name               = "radar-scheduler-planejador"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler_planejador" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.planejador.arn]
  }
}

resource "aws_iam_role_policy" "scheduler_planejador" {
  name   = "invocar-planejador"
  role   = aws_iam_role.scheduler_planejador.id
  policy = data.aws_iam_policy_document.scheduler_planejador.json
}

resource "aws_scheduler_schedule" "planejar_diario" {
  name                         = "radar-planejar-diario"
  schedule_expression          = "cron(0 6 * * ? *)"
  schedule_expression_timezone = "America/Sao_Paulo"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.planejador.arn
    role_arn = aws_iam_role.scheduler_planejador.arn

    # O planejador e idempotente: uma mensagem a mais sai ocupada ou obsoleta.
    retry_policy {
      maximum_retry_attempts       = 1
      maximum_event_age_in_seconds = 3600
    }
  }
}

# ---------- Alarmes (nada de falha em silencio) ----------

resource "aws_cloudwatch_metric_alarm" "janelas_dlq_com_mensagens" {
  alarm_name          = "radar-janelas-falhas-com-mensagens"
  alarm_description   = "Ha mensagens na DLQ radar-janelas-falhas (uma fatia falhou 5 vezes)."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.janelas_falhas.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alertas.arn]
}

# A coleta inteira leva ~2 h com duas em paralelo; uma nova tentativa espera 1,5 h.
resource "aws_cloudwatch_metric_alarm" "janelas_fila_parada" {
  alarm_name          = "radar-janelas-mensagem-antiga"
  alarm_description   = "A mensagem mais antiga da fila radar-janelas espera ha mais de 6 h."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = aws_sqs_queue.janelas.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 21600
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alertas.arn]
}

# Todo dia ha ao menos a janela de hoje a concluir. Sem nenhum `janela_concluida` em ~30 h:
# planejador que nao rodou, permissao quebrada ou cadeia parada.
resource "aws_cloudwatch_log_metric_filter" "janela_concluida" {
  name           = "radar-janela-concluida"
  log_group_name = aws_cloudwatch_log_group.lambda_coletora.name
  pattern        = "\"janela_concluida\""

  metric_transformation {
    name          = "RadarJanelaConcluida"
    namespace     = "Radar"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "pncp_nao_concluiu" {
  alarm_name          = "radar-pncp-nao-concluiu"
  alarm_description   = "Nenhuma janela do PNCP concluida nas ultimas ~30 h."
  namespace           = "Radar"
  metric_name         = "RadarJanelaConcluida"
  statistic           = "Sum"
  period              = 21600
  evaluation_periods  = 5
  datapoints_to_alarm = 5
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alertas.arn]
}
