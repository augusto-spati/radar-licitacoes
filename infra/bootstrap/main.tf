# Bootstrap: cria o bucket do estado do Terraform. Aplicado uma vez, com estado
# local (que nao vai para o repositorio). Ver infra/README.md.
terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }
}

provider "aws" {
  region = var.regiao

  default_tags {
    tags = {
      projeto  = "radar-licitacoes"
      ambiente = "prod"
    }
  }
}

data "aws_caller_identity" "atual" {}

resource "aws_s3_bucket" "estado" {
  bucket = "radar-licitacoes-tfstate-${data.aws_caller_identity.atual.account_id}"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "estado" {
  bucket = aws_s3_bucket.estado.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "estado" {
  bucket = aws_s3_bucket.estado.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "estado" {
  bucket = aws_s3_bucket.estado.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
