locals {
  imagem_api   = coalesce(var.imagem_api, "${aws_ecr_repository.api.repository_url}:bootstrap")
  com_cognito  = var.exigir_login && local.cognito != null
  parametro_db = aws_ssm_parameter.database_url.arn
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = "radar-api-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "api_basico" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}

data "aws_iam_policy_document" "api_banco" {
  statement {
    actions   = ["ssm:GetParameter"]
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

resource "aws_iam_role_policy" "api_banco" {
  name   = "ler-url-do-banco"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api_banco.json
}

resource "aws_cloudwatch_log_group" "lambda_api" {
  name              = "/aws/lambda/radar-api"
  retention_in_days = 14
}

resource "aws_lambda_function" "api" {
  function_name = "radar-api"
  role          = aws_iam_role.api.arn
  package_type  = "Image"
  image_uri     = local.imagem_api
  architectures = ["x86_64"]
  memory_size   = 512
  timeout       = 15

  environment {
    variables = {
      RADAR_DB_NULLPOOL   = "true"
      RADAR_CONFIG_FILTRO = "/var/task/config/filtro.toml"
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda_api.name
  }

  # O deploy (workflow) troca a imagem depois da criacao; o Terraform nao desfaz.
  lifecycle {
    ignore_changes = [image_uri]
  }

  depends_on = [aws_iam_role_policy_attachment.api_basico]
}

# Avisa no plan quando a API ficaria aberta.
check "login_configurado" {
  assert {
    condition     = !(var.exigir_login && local.cognito == null)
    error_message = "exigir_login=true mas cognito=null: /api/{proxy+} ficara SEM autorizador."
  }
}

resource "aws_apigatewayv2_api" "api" {
  name          = "radar-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_authorizer" "jwt" {
  count            = local.com_cognito ? 1 : 0
  api_id           = aws_apigatewayv2_api.api.id
  name             = "cognito-jwt"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    issuer   = local.cognito.issuer
    audience = local.cognito.audience
  }
}

resource "aws_apigatewayv2_route" "status" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "GET /api/status"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_route" "api" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "ANY /api/{proxy+}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = local.com_cognito ? "JWT" : "NONE"
  authorizer_id      = local.com_cognito ? aws_apigatewayv2_authorizer.jwt[0].id : null
}

resource "aws_cloudwatch_log_group" "api_acesso" {
  name              = "/aws/apigateway/radar-api"
  retention_in_days = 14
}

resource "aws_apigatewayv2_stage" "padrao" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 10
    throttling_rate_limit  = 5
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_acesso.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp" # IP do CloudFront, nao do usuario
      requestTime    = "$context.requestTime"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      responseLength = "$context.responseLength"
      latency        = "$context.responseLatency"
      error          = "$context.error.message"
      authorizerErr  = "$context.authorizer.error"
      integrationErr = "$context.integrationErrorMessage"
    })
  }
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}
