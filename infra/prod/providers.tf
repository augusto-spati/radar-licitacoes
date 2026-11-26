provider "aws" {
  region = var.regiao

  default_tags {
    tags = {
      projeto  = "radar-licitacoes"
      ambiente = "prod"
    }
  }
}
