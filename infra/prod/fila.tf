# Fila entre a coleta e os editais: o coletor (Fargate) envia, a Lambda radar-processador consome.
# Standard SQS, sem custo parado. Falhas repetidas vao para a DLQ e disparam alarme no SNS radar-alertas.

locals {
  timeout_processador = 120
}

# ---------- Filas ----------

resource "aws_sqs_queue" "editais_falhas" {
  name                      = "radar-editais-falhas"
  message_retention_seconds = 1209600 # 14 dias (maximo do SQS)
}

resource "aws_sqs_queue" "editais" {
  name = "radar-editais"
  # 6 x o timeout do consumidor (120 s): a mensagem nao reaparece enquanto ainda processa.
  visibility_timeout_seconds = 720
  message_retention_seconds  = 345600 # 4 dias (padrao)

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.editais_falhas.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "editais_falhas" {
  queue_url = aws_sqs_queue.editais_falhas.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.editais.arn]
  })
}

# ---------- Lambda processadora (mesma imagem da API, outro handler) ----------

resource "aws_iam_role" "processador" {
  name               = "radar-processador-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "processador_basico" {
  role       = aws_iam_role.processador.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Mesma fonte da URL do banco que a API: SSM lido em runtime pela role.
resource "aws_iam_role_policy" "processador_banco" {
  name   = "ler-url-do-banco"
  role   = aws_iam_role.processador.id
  policy = data.aws_iam_policy_document.api_banco.json
}

data "aws_iam_policy_document" "processador_fila" {
  statement {
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.editais.arn]
  }
}

resource "aws_iam_role_policy" "processador_fila" {
  name   = "consumir-fila"
  role   = aws_iam_role.processador.id
  policy = data.aws_iam_policy_document.processador_fila.json
}

resource "aws_cloudwatch_log_group" "lambda_processador" {
  name              = "/aws/lambda/radar-processador"
  retention_in_days = 14
}

resource "aws_lambda_function" "processador" {
  function_name = "radar-processador"
  role          = aws_iam_role.processador.arn
  package_type  = "Image"
  image_uri     = local.imagem_api
  architectures = ["x86_64"]
  memory_size   = 512
  timeout       = local.timeout_processador

  image_config {
    command = ["radar.processador.lambda_handler.handler"]
  }

  environment {
    variables = {
      RADAR_DB_NULLPOOL   = "true"
      RADAR_CONFIG_FILTRO = "/var/task/config/filtro.toml"
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda_processador.name
  }

  # O deploy (workflow) troca a imagem depois da criacao; o Terraform nao desfaz.
  lifecycle {
    ignore_changes = [image_uri]
  }

  depends_on = [
    aws_iam_role_policy_attachment.processador_basico,
    aws_iam_role_policy.processador_fila,
  ]
}

resource "aws_lambda_event_source_mapping" "processador" {
  event_source_arn        = aws_sqs_queue.editais.arn
  function_name           = aws_lambda_function.processador.arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]

  # Protege o Neon: no maximo 2 invocacoes simultaneas.
  scaling_config {
    maximum_concurrency = 2
  }
}

# ---------- Coletor envia para a fila ----------

data "aws_iam_policy_document" "coletor_fila" {
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.editais.arn, aws_sqs_queue.janelas.arn]
  }
}

resource "aws_iam_role_policy" "coletor_fila" {
  name   = "enviar-para-fila"
  role   = aws_iam_role.coletor_task.id
  policy = data.aws_iam_policy_document.coletor_fila.json
}

# ---------- Alarmes (nada de falha em silencio) ----------

resource "aws_cloudwatch_metric_alarm" "dlq_com_mensagens" {
  alarm_name          = "radar-editais-falhas-com-mensagens"
  alarm_description   = "Ha mensagens na DLQ radar-editais-falhas (falharam 5 vezes)."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.editais_falhas.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alertas.arn]
}

resource "aws_cloudwatch_metric_alarm" "fila_parada" {
  alarm_name          = "radar-editais-mensagem-antiga"
  alarm_description   = "A mensagem mais antiga da fila radar-editais espera ha mais de 1 h."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = aws_sqs_queue.editais.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 3600
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alertas.arn]
}

# Falhas que confirmam a mensagem (retrato com muitos invalidos, paginas ausentes) nao chegam
# a DLQ: o consumidor grava o log `processamento_falhou` e este filtro vira metrica.
resource "aws_cloudwatch_log_metric_filter" "processamento_falhou" {
  name           = "radar-processamento-falhou"
  log_group_name = aws_cloudwatch_log_group.lambda_processador.name
  pattern        = "\"processamento_falhou\""

  metric_transformation {
    name          = "RadarProcessamentoFalhou"
    namespace     = "Radar"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "processamento_falhou" {
  alarm_name          = "radar-processamento-falhou"
  alarm_description   = "O processador registrou processamento_falhou nas ultimas 24 h."
  namespace           = "Radar"
  metric_name         = "RadarProcessamentoFalhou"
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alertas.arn]
}
