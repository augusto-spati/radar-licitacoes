# Coleta agendada: EventBridge Scheduler -> ECS RunTask (Fargate, sem service, sem NAT).
# A task sobe, roda `radar atualizar` e para; so cobra enquanto roda.

locals {
  familia_coletor = "radar-coletor"
  # ARN da FAMILIA (sem ":revisao"): o RunTask usa a ultima revisao ACTIVE, entao o
  # deploy (Task 7) registra revisoes novas sem precisar mexer no schedule.
  arn_familia_coletor = "arn:aws:ecs:${var.regiao}:${data.aws_caller_identity.atual.account_id}:task-definition/${local.familia_coletor}"
}

# ---------- Rede: VPC padrao, sub-redes publicas, so saida ----------

data "aws_vpc" "padrao" {
  default = true
}

data "aws_subnets" "padrao" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.padrao.id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_security_group" "coletor" {
  name        = "radar-coletor"
  description = "Coletor: somente saida (HTTPS para PNCP/ECR/SSM, 5432 para o Neon)."
  vpc_id      = data.aws_vpc.padrao.id
}

resource "aws_vpc_security_group_egress_rule" "coletor_https" {
  security_group_id = aws_security_group.coletor.id
  description       = "HTTPS"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "coletor_postgres" {
  security_group_id = aws_security_group.coletor.id
  description       = "Postgres sobre TLS (Neon)"
  ip_protocol       = "tcp"
  from_port         = 5432
  to_port           = 5432
  cidr_ipv4         = "0.0.0.0/0"
}

# ---------- Cluster, logs, task definition ----------

resource "aws_ecs_cluster" "radar" {
  name = "radar"

  # Container Insights cobra por metrica/log: fica desligado.
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_cloudwatch_log_group" "coletor" {
  name              = "/radar/coletor"
  retention_in_days = 30
}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: puxa a imagem do ECR, escreve logs e injeta o segredo do SSM.
resource "aws_iam_role" "coletor_execucao" {
  name               = "radar-coletor-execucao"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "coletor_execucao_basico" {
  role       = aws_iam_role.coletor_execucao.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "coletor_segredo" {
  statement {
    actions   = ["ssm:GetParameters"]
    resources = [local.parametro_db]
  }

  statement {
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.regiao}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "coletor_segredo" {
  name   = "ler-url-do-banco"
  role   = aws_iam_role.coletor_execucao.id
  policy = data.aws_iam_policy_document.coletor_segredo.json
}

# Task role: a URL do banco chega por `secrets` (execution role). Unica permissao do app:
# enviar para a fila radar-editais (fila.tf).
resource "aws_iam_role" "coletor_task" {
  name               = "radar-coletor-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

# O deploy (Task 7) registra revisoes novas; o schedule aponta para a familia.
# skip_destroy mantem as revisoes antigas (inativas nao custam nada).
resource "aws_ecs_task_definition" "coletor" {
  family                   = local.familia_coletor
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.coletor_execucao.arn
  task_role_arn            = aws_iam_role.coletor_task.arn
  skip_destroy             = true

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name      = "coletor"
    image     = "${aws_ecr_repository.coletor.repository_url}:${var.tag_coletor}"
    essential = true
    # ENTRYPOINT ["radar"] e CMD ["atualizar"] vem da imagem.
    stopTimeout = 30

    environment = [
      { name = "RADAR_DB_NULLPOOL", value = "true" },
      { name = "RADAR_CONFIG_FILTRO", value = "/app/config/filtro.toml" },
      { name = "RADAR_FILA", value = "sqs" },
      { name = "RADAR_FILA_URL", value = aws_sqs_queue.editais.url },
    ]

    secrets = [
      { name = "RADAR_DATABASE_URL", valueFrom = local.parametro_db },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.coletor.name
        "awslogs-region"        = var.regiao
        "awslogs-stream-prefix" = "coletor"
      }
    }
  }])
}

# ---------- Agenda: todo dia 06:00 de Brasilia ----------

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.atual.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler_coletor" {
  name               = "radar-scheduler-coletor"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler_coletor" {
  statement {
    actions = ["ecs:RunTask"]
    # Familia sem revisao e qualquer revisao dela.
    resources = [local.arn_familia_coletor, "${local.arn_familia_coletor}:*"]

    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.radar.arn]
    }
  }

  statement {
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.coletor_execucao.arn, aws_iam_role.coletor_task.arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "scheduler_coletor" {
  name   = "rodar-coletor"
  role   = aws_iam_role.scheduler_coletor.id
  policy = data.aws_iam_policy_document.scheduler_coletor.json
}

# Limite: o Scheduler nao encerra a task. O timeout de 2 h do desenho nao e nativo;
# a defesa e o timeout por requisicao do cliente HTTP da CLI. Se travar, o alarme
# "coletor nao rodou" avisa no dia seguinte.
resource "aws_scheduler_schedule" "atualizar_diario" {
  name                         = "radar-atualizar-diario"
  schedule_expression          = "cron(0 6 * * ? *)"
  schedule_expression_timezone = "America/Sao_Paulo"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.radar.arn
    role_arn = aws_iam_role.scheduler_coletor.arn

    # A Compesa fica fora da nuvem: o portal dela nao responde a partir da AWS
    # (ConnectTimeout em duas rodadas em us-east-2; responde em 0,6 s de uma conexao no
    # Brasil). Ela continua coletavel localmente com `radar atualizar --fontes compesa`.
    input = jsonencode({
      containerOverrides = [{
        name    = "coletor"
        command = ["atualizar", "--fontes", "pncp,sanepar"]
      }]
    })

    ecs_parameters {
      task_definition_arn = local.arn_familia_coletor
      launch_type         = "FARGATE"
      task_count          = 1
      platform_version    = "LATEST"

      network_configuration {
        subnets          = data.aws_subnets.padrao.ids
        security_groups  = [aws_security_group.coletor.id]
        assign_public_ip = true
      }
    }

    retry_policy {
      maximum_retry_attempts       = 1
      maximum_event_age_in_seconds = 3600
    }
  }
}

# ---------- Alarmes (nada de falha em silencio) ----------

# (a) task parou com erro: exitCode != 0, ou nao chegou a iniciar (imagem, segredo).
resource "aws_cloudwatch_event_rule" "coletor_falhou" {
  name        = "radar-coletor-falhou"
  description = "Task do coletor parou com erro."

  event_pattern = jsonencode({
    source        = ["aws.ecs"]
    "detail-type" = ["ECS Task State Change"]
    detail = {
      clusterArn = [aws_ecs_cluster.radar.arn]
      lastStatus = ["STOPPED"]
      "$or" = [
        { containers = { exitCode = [{ "anything-but" = [0] }] } },
        { stopCode = ["TaskFailedToStart"] },
      ]
    }
  })
}

resource "aws_cloudwatch_event_target" "coletor_falhou_sns" {
  rule = aws_cloudwatch_event_rule.coletor_falhou.name
  arn  = aws_sns_topic.alertas.arn
}

data "aws_iam_policy_document" "alertas_publicar" {
  statement {
    sid       = "EventBridgePublica"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alertas.arn]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.coletor_falhou.arn]
    }
  }

  statement {
    sid       = "CloudWatchAlarmePublica"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alertas.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values = [
        aws_cloudwatch_metric_alarm.coletor_nao_rodou.arn,
        aws_cloudwatch_metric_alarm.dlq_com_mensagens.arn,
        aws_cloudwatch_metric_alarm.fila_parada.arn,
        aws_cloudwatch_metric_alarm.processamento_falhou.arn,
      ]
    }
  }
}

# Substitui a politica padrao do topico: so EventBridge (regra) e CloudWatch (alarme)
# publicam. O Budgets usa assinante de e-mail proprio, sem passar pelo topico.
resource "aws_sns_topic_policy" "alertas" {
  arn    = aws_sns_topic.alertas.arn
  policy = data.aws_iam_policy_document.alertas_publicar.json
}

# (b) coletor nao rodou: a CLI imprime "resultado: ..." ao fim de toda execucao
# (com ou sem falhas). Cada linha vira 1 na metrica.
resource "aws_cloudwatch_log_metric_filter" "coletor_execucoes" {
  name           = "radar-coletor-execucoes"
  log_group_name = aws_cloudwatch_log_group.coletor.name
  pattern        = "\"resultado:\""

  metric_transformation {
    name          = "RadarColetorExecucoes"
    namespace     = "Radar"
    value         = "1"
    default_value = "0"
  }
}

# 6 h x 5 periodos = 30 h. Os 5 datapoints (Sum < 1) precisam estar todos em
# violacao; dado ausente conta como violacao. Dispara se passar ~30 h sem nenhuma
# linha "resultado:" (a execucao diaria deixa no maximo ~24 h de intervalo, mais
# folga para atraso e retry).
resource "aws_cloudwatch_metric_alarm" "coletor_nao_rodou" {
  alarm_name          = "radar-coletor-nao-rodou"
  alarm_description   = "Nenhuma execucao concluida do coletor nas ultimas ~30 h."
  namespace           = "Radar"
  metric_name         = "RadarColetorExecucoes"
  statistic           = "Sum"
  period              = 21600
  evaluation_periods  = 5
  datapoints_to_alarm = 5
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alertas.arn]
}
