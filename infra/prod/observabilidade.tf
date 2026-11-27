# Observabilidade (etapa 5): traces OTLP direto para o X-Ray (sem sidecar, sem collector),
# metricas customizadas no namespace Radar (EMF nas Lambdas, PutMetricData no Fargate), dois
# alarmes de SLO, um dashboard e consultas salvas do Logs Insights. Tudo na regiao do projeto.
#
# Orcamento do plano gratis: 10 metricas customizadas (3 filtros de log + 6 do catalogo em
# src/radar/comum/metricas.py = 9) e 10 alarmes (9; a analise da etapa 6 nao criou nenhum). Os
# testes estaticos contam.
#
# Transaction Search e configuracao da conta, ligada uma vez, a mao, DEPOIS do apply (o log
# group aws/spans abaixo precisa existir antes):
#   aws xray update-trace-segment-destination --destination CloudWatchLogs --region us-east-2
#   aws xray update-indexing-rule --name Default --rule '{"Probabilistic":{"DesiredSamplingPercentage":1}}' --region us-east-2
#   aws xray get-trace-segment-destination --region us-east-2   # -> ACTIVE
# Application Signals nao e ligado (cobra por sinal e exige agente).

locals {
  roles_com_traces = {
    coletor     = aws_iam_role.coletor_task.name
    processador = aws_iam_role.processador.name
    coletora    = aws_iam_role.coletora.name
    planejador  = aws_iam_role.planejador.name
    analisador  = aws_iam_role.analisador_task.name # `radar analisar` (analise.tf)
  }
  grupos_de_log_do_radar = [
    aws_cloudwatch_log_group.coletor.name,
    aws_cloudwatch_log_group.lambda_processador.name,
    aws_cloudwatch_log_group.lambda_coletora.name,
    aws_cloudwatch_log_group.lambda_planejador.name,
  ]
}

# ---------- Permissoes ----------

# O endpoint OTLP de traces autoriza por xray:PutTraceSegments (assinatura SigV4).
resource "aws_iam_role_policy_attachment" "traces" {
  for_each   = local.roles_com_traces
  role       = each.value
  policy_arn = "arn:aws:iam::aws:policy/AWSXrayWriteOnlyAccess"
}

# So as tasks Fargate (coletor e analisador) chamam PutMetricData; as Lambdas usam EMF.
data "aws_iam_policy_document" "coletor_metricas" {
  statement {
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"] # PutMetricData nao aceita ARN de recurso; a condicao restringe

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["Radar"]
    }
  }
}

resource "aws_iam_role_policy" "coletor_metricas" {
  name   = "publicar-metricas-radar"
  role   = aws_iam_role.coletor_task.id
  policy = data.aws_iam_policy_document.coletor_metricas.json
}

# Mesma task do coletor com outra role (override na agenda radar-analisar-diario).
resource "aws_iam_role_policy" "analisador_metricas" {
  name   = "publicar-metricas-radar"
  role   = aws_iam_role.analisador_task.id
  policy = data.aws_iam_policy_document.coletor_metricas.json
}

# ---------- Spans (Transaction Search) ----------

# Criado antes de ligar o Transaction Search: o X-Ray aceita o log group existente e a
# retencao fica curta (a ingestao e cobrada por GB).
resource "aws_cloudwatch_log_group" "spans" {
  name              = "aws/spans"
  retention_in_days = 7
}

# ---------- Alarmes de SLO ----------

# Frescor: Maximum (a pior fonte) acima de 36 h em 6 periodos de 6 h. As medicoes saem no
# comeco e no fim de cada `atualizar`; periodo sem medicao conta como violacao, entao 36 h
# sem medir nada tambem e frescor estourado.
resource "aws_cloudwatch_metric_alarm" "frescor_estourado" {
  alarm_name          = "radar-frescor-estourado"
  alarm_description   = "Alguma fonte sem dado novo em edital ha mais de 36 h (ou sem medicao em 36 h)."
  namespace           = "Radar"
  metric_name         = "FrescorHoras"
  statistic           = "Maximum"
  period              = 21600
  evaluation_periods  = 6
  datapoints_to_alarm = 6
  threshold           = 36
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alertas.arn]
}

# Taxa de paginas com erro (depois das retentativas) acima de 10% num dia; o SLO e 5% em
# 28 dias. Dia sem pagina nenhuma nao e erro (o frescor cobre a coleta parada).
resource "aws_cloudwatch_metric_alarm" "paginas_com_erro" {
  alarm_name          = "radar-paginas-com-erro"
  alarm_description   = "Mais de 10% das paginas falharam depois das retentativas nas ultimas 24 h."
  namespace           = "Radar"
  metric_name         = "PaginaComErro"
  statistic           = "Average"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 0.1
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alertas.arn]
}

# ---------- Dashboard (1 de 3 gratis) ----------

locals {
  periodo_painel = 3600
  consulta_frescor_por_fonte = join(" | ", [
    "SOURCE '${aws_cloudwatch_log_group.coletor.name}'",
    "filter evento = 'frescor_medido'",
    "stats latest(horas) as horas_agora, max(horas) as pior by fonte",
  ])
}

resource "aws_cloudwatch_dashboard" "radar" {
  dashboard_name = "radar"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title   = "Frescor (pior fonte, h) - SLO 36 h"
          region  = var.regiao
          view    = "timeSeries"
          stat    = "Maximum"
          period  = local.periodo_painel
          metrics = [["Radar", "FrescorHoras"]]
          annotations = {
            horizontal = [{ label = "SLO 36 h", value = 36 }]
          }
        }
      },
      {
        type = "log", x = 12, y = 0, width = 12, height = 6
        properties = {
          title  = "Frescor por fonte (Logs Insights)"
          region = var.regiao
          view   = "table"
          query  = local.consulta_frescor_por_fonte
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title  = "Latencia p95 por fonte (s) - SLO 40 s PNCP, 10 s retratos"
          region = var.regiao
          view   = "timeSeries"
          stat   = "p95"
          period = 86400
          metrics = [
            ["Radar", "LatenciaRequisicao", "fonte", "pncp"],
            ["Radar", "LatenciaRequisicao", "fonte", "sanepar"],
            ["Radar", "LatenciaRequisicao", "fonte", "compesa"],
          ]
          annotations = {
            horizontal = [
              { label = "PNCP 40 s", value = 40 },
              { label = "retratos 10 s", value = 10 },
            ]
          }
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title  = "Paginas com erro (taxa) e paginas tentadas"
          region = var.regiao
          view   = "timeSeries"
          period = 86400
          metrics = [
            ["Radar", "PaginaComErro", { stat = "Average", label = "taxa de erro" }],
            ["Radar", "PaginaComErro", { stat = "SampleCount", label = "paginas", yAxis = "right" }],
          ]
          annotations = {
            horizontal = [{ label = "SLO 5%", value = 0.05 }]
          }
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 8, height = 6
        properties = {
          title   = "Execucao agendada ok (1/0)"
          region  = var.regiao
          view    = "timeSeries"
          stat    = "Minimum"
          period  = 86400
          metrics = [["Radar", "ExecucaoOk"]]
        }
      },
      {
        type = "metric", x = 8, y = 12, width = 8, height = 6
        properties = {
          title  = "Fila radar-editais: idade da mais velha (s) e DLQs"
          region = var.regiao
          view   = "timeSeries"
          stat   = "Maximum"
          period = 300
          metrics = [
            ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", aws_sqs_queue.editais.name],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.editais_falhas.name, { yAxis = "right" }],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.janelas_falhas.name, { yAxis = "right" }],
          ]
          annotations = {
            horizontal = [{ label = "SLO 900 s", value = 900 }]
          }
        }
      },
      {
        type = "metric", x = 16, y = 12, width = 8, height = 6
        properties = {
          title  = "Lambdas: erros, throttles e duracao"
          region = var.regiao
          view   = "timeSeries"
          period = 3600
          metrics = [
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.processador.function_name, { stat = "Sum" }],
            ["AWS/Lambda", "Throttles", "FunctionName", aws_lambda_function.processador.function_name, { stat = "Sum" }],
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.coletora.function_name, { stat = "Sum" }],
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.processador.function_name, { stat = "p95", yAxis = "right" }],
          ]
        }
      },
      {
        type = "alarm", x = 0, y = 18, width = 24, height = 4
        properties = {
          title = "Alarmes"
          alarms = [
            aws_cloudwatch_metric_alarm.frescor_estourado.arn,
            aws_cloudwatch_metric_alarm.paginas_com_erro.arn,
            aws_cloudwatch_metric_alarm.coletor_nao_rodou.arn,
            aws_cloudwatch_metric_alarm.pncp_nao_concluiu.arn,
            aws_cloudwatch_metric_alarm.processamento_falhou.arn,
            aws_cloudwatch_metric_alarm.dlq_com_mensagens.arn,
            aws_cloudwatch_metric_alarm.fila_parada.arn,
            aws_cloudwatch_metric_alarm.janelas_dlq_com_mensagens.arn,
            aws_cloudwatch_metric_alarm.janelas_fila_parada.arn,
          ]
        }
      },
    ]
  })
}

# ---------- Consultas salvas do Logs Insights (gratis para guardar) ----------

resource "aws_cloudwatch_query_definition" "frescor_por_fonte" {
  name            = "radar/frescor-por-fonte"
  log_group_names = [aws_cloudwatch_log_group.coletor.name]
  query_string    = <<-EOT
    filter evento = "frescor_medido"
    | stats latest(horas) as horas_agora, max(horas) as pior, count() as medicoes by fonte, bin(1d)
  EOT
}

resource "aws_cloudwatch_query_definition" "latencia_por_fonte" {
  name            = "radar/latencia-p95-por-fonte"
  log_group_names = [aws_cloudwatch_log_group.coletor.name, aws_cloudwatch_log_group.lambda_coletora.name]
  query_string    = <<-EOT
    filter evento = "requisicao_http"
    | stats pct(duracao_s, 95) as p95, pct(duracao_s, 50) as p50, count() as tentativas by fonte, bin(1d)
  EOT
}

resource "aws_cloudwatch_query_definition" "erro_por_execucao" {
  name            = "radar/paginas-com-erro-por-execucao"
  log_group_names = [aws_cloudwatch_log_group.coletor.name, aws_cloudwatch_log_group.lambda_coletora.name]
  query_string    = <<-EOT
    filter evento = "metrica_registrada" and metrica = "PaginaComErro"
    | stats avg(valor) as taxa_de_erro, count() as paginas by @logStream
    | sort taxa_de_erro desc
  EOT
}

resource "aws_cloudwatch_query_definition" "tudo_de_um_trace" {
  name            = "radar/tudo-de-um-trace"
  log_group_names = local.grupos_de_log_do_radar
  query_string    = <<-EOT
    fields @timestamp, @log, nivel, evento, span_id
    | filter trace_id = "COLE_O_TRACE_ID_AQUI"
    | sort @timestamp asc
  EOT
}
