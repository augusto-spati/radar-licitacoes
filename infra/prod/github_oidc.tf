# Deploy pelo GitHub Actions sem chave de longa duracao: o workflow troca o token OIDC
# do GitHub por credenciais temporarias desta role (so no branch main).

variable "repositorio_github" {
  description = "Repositorio GitHub (dono/nome) autorizado a assumir a role de deploy."
  type        = string
  default     = "augusto-spati/radar-licitacoes"
}

# Repositorios novos do GitHub usam o "sub" imutavel, com os ids numericos do dono e do
# repositorio (nao reaproveitaveis se o nome for liberado). Conferir com:
#   gh api repos/<dono>/<repo>/actions/oidc/customization/sub   -> sub_claim_prefix
variable "prefixo_sub_github" {
  description = "Prefixo do claim sub do token OIDC do GitHub (sub_claim_prefix do repositorio)."
  type        = string
  default     = "repo:augusto-spati@248562387/radar-licitacoes@1473222870"
}

# A AWS valida o certificado do GitHub pela sua propria lista de CAs confiaveis, mas o
# provider 5.x ainda exige thumbprint_list. Valores publicados pelo GitHub:
# https://github.blog/changelog/2023-06-27-github-actions-update-on-oidc-integration-with-aws/
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1", "1c58a3a8518e8759bf075b76b750d4f2df264fcd"]
}

# Acesso do servico Lambda a imagem da API (mesma conta: basta um lado conceder).
data "aws_iam_policy_document" "api_lambda_ecr" {
  statement {
    sid     = "LambdaECRImageRetrievalPolicy"
    actions = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_ecr_repository_policy" "api_lambda" {
  repository = aws_ecr_repository.api.name
  policy     = data.aws_iam_policy_document.api_lambda_ecr.json
}

data "aws_iam_policy_document" "deploy_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${var.prefixo_sub_github}:ref:refs/heads/main"]
    }

    # Amarra a role ao workflow de deploy (chave suportada pelo IAM para o GitHub:
    # https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_iam-condition-keys.html).
    # Outro workflow na main nao assume a role.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      values   = ["${var.repositorio_github}/.github/workflows/deploy.yml@refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = "radar-deploy"
  assume_role_policy = data.aws_iam_policy_document.deploy_assume.json
}

data "aws_iam_policy_document" "deploy" {
  # GetAuthorizationToken so existe com resource "*".
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
    ]
    resources = [aws_ecr_repository.api.arn, aws_ecr_repository.coletor.arn]
  }

  # Quem atualiza a Lambda por imagem precisa ler a imagem no ECR:
  # https://docs.aws.amazon.com/lambda/latest/dg/images-create.html#gettingstarted-images-permissions
  # Get/SetRepositoryPolicy so seriam necessarios para o Lambda criar a politica do repo;
  # ela e criada abaixo pelo Terraform (aws_ecr_repository_policy.api_lambda), entao nao sao concedidos.
  # O docker push consulta o manifesto antes de enviar (HEAD/GET manifests), o que exige
  # BatchGetImage tambem no repositorio do coletor.
  statement {
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [aws_ecr_repository.api.arn, aws_ecr_repository.coletor.arn]
  }

  statement {
    actions   = ["lambda:UpdateFunctionCode", "lambda:GetFunction"]
    resources = [aws_lambda_function.api.arn, aws_lambda_function.processador.arn]
  }

  # DescribeTaskDefinition e RegisterTaskDefinition nao aceitam permissao por recurso:
  # https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonelasticcontainerservice.html
  statement {
    actions   = ["ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition"]
    resources = ["*"]
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

  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.front.arn]
  }

  statement {
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.front.arn}/*"]
  }

  statement {
    actions   = ["cloudfront:CreateInvalidation"]
    resources = [aws_cloudfront_distribution.site.arn]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

output "role_deploy_arn" {
  description = "ARN da role de deploy; vai na variavel de repositorio AWS_ROLE_ARN do GitHub."
  value       = aws_iam_role.deploy.arn
}
