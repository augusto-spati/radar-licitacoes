# Configuracao parcial: o nome do bucket leva o id da conta e fica fora do codigo.
#   terraform init -backend-config="bucket=radar-licitacoes-tfstate-<account_id>"
terraform {
  backend "s3" {
    key          = "prod/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
