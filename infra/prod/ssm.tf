# O valor real (URL do Neon) e posto a mao, fora do Terraform.
resource "aws_ssm_parameter" "database_url" {
  name  = "/radar/prod/database_url"
  type  = "SecureString"
  value = "preencher"

  lifecycle {
    ignore_changes = [value]
  }
}
