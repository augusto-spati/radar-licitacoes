# Login sem senha: codigo de uso unico por e-mail (USER_AUTH + EMAIL_OTP).
#
# O EMAIL_OTP exige o plano Essentials (gratis ate 10 mil MAU) e o envio pelo SES
# (DEVELOPER): o e-mail padrao do Cognito nao manda OTP. A conta SES fica no sandbox,
# entao remetente E destinatarios precisam ser identidades verificadas: cada pessoa
# clica uma vez no e-mail de verificacao da AWS (veja infra/README.md).

locals {
  # O remetente pode estar tambem na lista de acesso: um set evita identidade duplicada.
  identidades_ses = toset(concat([var.email_remetente], var.emails_com_acesso))

  cognito = var.cognito != null ? var.cognito : {
    issuer   = "https://${aws_cognito_user_pool.radar.endpoint}"
    audience = [aws_cognito_user_pool_client.front.id]
  }
}

resource "aws_sesv2_email_identity" "email" {
  for_each       = local.identidades_ses
  email_identity = each.value
}

resource "aws_cognito_user_pool" "radar" {
  name           = "radar-usuarios"
  user_pool_tier = "ESSENTIALS"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OFF"

  # A API exige PASSWORD na lista; os usuarios sao criados sem senha, entao so o
  # codigo por e-mail funciona (e o app client so permite USER_AUTH).
  sign_in_policy {
    allowed_first_auth_factors = ["EMAIL_OTP", "PASSWORD"]
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  # Sem senha nao ha o que recuperar: so o administrador (Terraform) mexe na conta.
  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }

  email_configuration {
    email_sending_account = "DEVELOPER"
    source_arn            = aws_sesv2_email_identity.email[var.email_remetente].arn
    from_email_address    = var.email_remetente
  }
}

resource "aws_cognito_user" "acesso" {
  for_each     = toset(var.emails_com_acesso)
  user_pool_id = aws_cognito_user_pool.radar.id
  username     = each.value

  attributes = {
    email          = each.value
    email_verified = true
  }

  # Sem convite: o primeiro contato e o codigo do primeiro login.
  message_action = "SUPPRESS"
}

# App client publico (o front roda no navegador): sem segredo, so USER_AUTH e refresh.
resource "aws_cognito_user_pool_client" "front" {
  name            = "radar-front"
  user_pool_id    = aws_cognito_user_pool.radar.id
  generate_secret = false

  explicit_auth_flows           = ["ALLOW_USER_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  # Janela para digitar o codigo depois de pedi-lo.
  auth_session_validity  = 5
  id_token_validity      = 1
  access_token_validity  = 1
  refresh_token_validity = 30

  token_validity_units {
    id_token      = "hours"
    access_token  = "hours"
    refresh_token = "days"
  }
}
