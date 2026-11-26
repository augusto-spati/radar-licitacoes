output "bucket_estado" {
  description = "Nome do bucket para `terraform init -backend-config=\"bucket=...\"` em infra/prod."
  value       = aws_s3_bucket.estado.bucket
}
