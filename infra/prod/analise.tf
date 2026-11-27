# Analise dos editais com IA (etapa 6): `radar analisar` todo dia 09:00 de Brasilia.
# Mesma imagem e mesma familia de task do coletor, com override de comando, role e
# variaveis na agenda (o deploy, que registra revisoes da familia, cobre a analise).
# Falha = exitCode != 0, que a regra radar-coletor-falhou (qualquer task do cluster) ja
# manda ao SNS: nenhum alarme novo. A analise escreve no log group do coletor e nunca
# imprime "resultado:" (o alarme "coletor nao rodou" conta essa linha).

# ---------- Bucket dos documentos (dado bruto fora do banco) ----------

resource "aws_s3_bucket" "documentos" {
  bucket = "radar-documentos-${data.aws_caller_identity.atual.account_id}"
}

resource "aws_s3_bucket_ownership_controls" "documentos" {
  bucket = aws_s3_bucket.documentos.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "documentos" {
  bucket                  = aws_s3_bucket.documentos.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documentos" {
  bucket = aws_s3_bucket.documentos.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# PDFs e textos extraidos expiram em 180 dias (o edital ja foi julgado; refazer baixa de novo).
resource "aws_s3_bucket_lifecycle_configuration" "documentos" {
  bucket = aws_s3_bucket.documentos.id

  rule {
    id     = "expira-documentos"
    status = "Enabled"

    filter {}

    expiration {
      days = 180
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# So HTTPS: qualquer acesso sem TLS (aws:SecureTransport = false) e negado, para todos.
data "aws_iam_policy_document" "documentos_so_tls" {
  statement {
    sid     = "NegaSemTLS"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.documentos.arn,
      "${aws_s3_bucket.documentos.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "documentos" {
  bucket = aws_s3_bucket.documentos.id
  policy = data.aws_iam_policy_document.documentos_so_tls.json

  # A politica so entra depois do bloqueio de acesso publico (evita corrida no apply).
  depends_on = [aws_s3_bucket_public_access_block.documentos]
}

# ---------- Role da task de analise ----------

data "aws_iam_policy_document" "analisador_task" {
  # Converse usa a acao InvokeModel. So foundation models de us-east-2: nenhum perfil de
  # inferencia (us./global. sao cross-region), nenhum curinga.
  statement {
    sid       = "BedrockInRegion"
    actions   = ["bedrock:InvokeModel"]
    resources = [for m in var.modelos_ia : "arn:aws:bedrock:${var.regiao}::foundation-model/${m}"]
  }

  statement {
    sid       = "DocumentosObjetos"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.documentos.arn}/*"]
  }

  # Sem ListBucket, GetObject de chave ausente devolve AccessDenied em vez de NoSuchKey.
  statement {
    sid       = "DocumentosListar"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.documentos.arn]
  }
}

resource "aws_iam_role" "analisador_task" {
  name               = "radar-analisador-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "analisador_task" {
  name   = "bedrock-e-documentos"
  role   = aws_iam_role.analisador_task.id
  policy = data.aws_iam_policy_document.analisador_task.json
}

# ---------- Agenda: todo dia 09:00 (tres horas depois da coleta) ----------

# Alvo universal do Scheduler: chama ecs:RunTask com Overrides (TaskRoleArn e comando),
# o que o alvo templated (ecs_parameters) nao aceita. Usa a role do Scheduler do coletor,
# que tem ecs:RunTask na familia e iam:PassRole na role nova (coletor.tf).
resource "aws_scheduler_schedule" "analisar_diario" {
  name                         = "radar-analisar-diario"
  state                        = var.analise_agendada ? "ENABLED" : "DISABLED"
  schedule_expression          = "cron(0 9 * * ? *)"
  schedule_expression_timezone = "America/Sao_Paulo"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:runTask"
    role_arn = aws_iam_role.scheduler_coletor.arn

    input = jsonencode({
      Cluster         = aws_ecs_cluster.radar.arn
      TaskDefinition  = local.familia_coletor
      LaunchType      = "FARGATE"
      PlatformVersion = "LATEST"
      Count           = 1
      NetworkConfiguration = {
        AwsvpcConfiguration = {
          Subnets        = data.aws_subnets.padrao.ids
          SecurityGroups = [aws_security_group.coletor.id]
          AssignPublicIp = "ENABLED"
        }
      }
      Overrides = {
        TaskRoleArn = aws_iam_role.analisador_task.arn
        ContainerOverrides = [{
          Name = "coletor"
          # As tres fontes; o padrao do comando (pncp,sanepar) e o de quem roda sem dizer.
          Command = ["analisar", "--fontes", "pncp,sanepar,compesa"]
          Environment = [
            { Name = "RADAR_IA_PROVEDOR", Value = "bedrock" },
            { Name = "RADAR_DOCUMENTOS", Value = "s3://${aws_s3_bucket.documentos.bucket}/" },
            { Name = "RADAR_CONFIG_IA", Value = "/app/config/ia.toml" },
            # O perfil real e dado privado da empresa: fica no bucket privado, nunca no
            # repositorio nem na imagem. Ate ele subir, vale o de exemplo da imagem.
            {
              Name = "RADAR_CONFIG_PERFIL",
              Value = (var.perfil_no_s3
                ? "s3://${aws_s3_bucket.documentos.bucket}/perfil/perfil.toml"
              : "/app/config/perfil.toml")
            },
          ]
        }]
      }
    })

    retry_policy {
      maximum_retry_attempts       = 1
      maximum_event_age_in_seconds = 3600
    }
  }
}

# ---------- Agenda da 7b.2: planilhas orcamentarias, todo dia 10:00 ----------

# Depois do analisar das 09:00 (nao disputam o pncp.gov.br nem a trava): `radar planilhas
# atualizar`, ate 10 editais por rodada. Mesma familia, rede e role do analisar (Bedrock, para
# o texto picotado; os tetos da 7b.2 ficam em config/ia.toml). Nao usa o bucket de
# documentos. Falha = exitCode != 0 (regra radar-coletor-falhou); a ultima linha e
# "planilhas: ...", nunca "resultado:" (o log group e o do coletor).
resource "aws_scheduler_schedule" "planilhas_diario" {
  name                         = "radar-planilhas-diario"
  state                        = var.analise_agendada ? "ENABLED" : "DISABLED"
  schedule_expression          = "cron(0 10 * * ? *)"
  schedule_expression_timezone = "America/Sao_Paulo"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ecs:runTask"
    role_arn = aws_iam_role.scheduler_coletor.arn

    input = jsonencode({
      Cluster         = aws_ecs_cluster.radar.arn
      TaskDefinition  = local.familia_coletor
      LaunchType      = "FARGATE"
      PlatformVersion = "LATEST"
      Count           = 1
      NetworkConfiguration = {
        AwsvpcConfiguration = {
          Subnets        = data.aws_subnets.padrao.ids
          SecurityGroups = [aws_security_group.coletor.id]
          AssignPublicIp = "ENABLED"
        }
      }
      Overrides = {
        TaskRoleArn = aws_iam_role.analisador_task.arn
        ContainerOverrides = [{
          Name    = "coletor"
          Command = ["planilhas", "atualizar"]
          Environment = [
            { Name = "RADAR_IA_PROVEDOR", Value = "bedrock" },
            { Name = "RADAR_CONFIG_IA", Value = "/app/config/ia.toml" },
          ]
        }]
      }
    })

    retry_policy {
      maximum_retry_attempts       = 1
      maximum_event_age_in_seconds = 3600
    }
  }
}
