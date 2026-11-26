# O valor real (URL do Neon) e posto a mao, fora do Terraform.
resource "aws_ssm_parameter" "database_url" {
  name = "/radar/prod/database_url"
  type = "SecureString"
  # Write-only: o valor nao vai para o estado. O valor real e posto a mao.
  value_wo         = "preencher"
  value_wo_version = 1
}
