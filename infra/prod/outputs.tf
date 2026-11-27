output "topico_alertas_arn" {
  description = "ARN do topico SNS radar-alertas."
  value       = aws_sns_topic.alertas.arn
}

output "url_site" {
  description = "URL publica do site (CloudFront)."
  value       = "https://${aws_cloudfront_distribution.site.domain_name}"
}

output "id_distribuicao" {
  description = "Id da distribuicao CloudFront (para invalidacao no deploy do front)."
  value       = aws_cloudfront_distribution.site.id
}

output "url_api" {
  description = "Endpoint padrao do API Gateway."
  value       = aws_apigatewayv2_api.api.api_endpoint
}

output "repositorio_api" {
  description = "URL do repositorio ECR da API."
  value       = aws_ecr_repository.api.repository_url
}

output "repositorio_coletor" {
  description = "URL do repositorio ECR do coletor."
  value       = aws_ecr_repository.coletor.repository_url
}

output "aviso_api_sem_login" {
  description = "Aviso quando /api/* esta sem autorizador JWT."
  value       = local.com_cognito ? "" : "ATENCAO: /api/{proxy+} sem autorizador JWT (Cognito nao configurado ou exigir_login=false)."
}

output "cluster_ecs" {
  description = "Nome do cluster ECS do coletor."
  value       = aws_ecs_cluster.radar.name
}

output "task_definition_coletor" {
  description = "Familia da task definition do coletor."
  value       = aws_ecs_task_definition.coletor.family
}

output "agenda_coletor" {
  description = "Nome do schedule diario do coletor."
  value       = aws_scheduler_schedule.atualizar_diario.name
}

output "cognito_user_pool_id" {
  description = "Id do user pool (login por codigo no e-mail)."
  value       = aws_cognito_user_pool.radar.id
}

output "cognito_client_id" {
  description = "Id do app client publico; vai no build do front como VITE_COGNITO_CLIENT_ID."
  value       = aws_cognito_user_pool_client.front.id
}

output "url_fila" {
  description = "URL da fila SQS radar-editais."
  value       = aws_sqs_queue.editais.url
}

output "url_fila_falhas" {
  description = "URL da DLQ radar-editais-falhas."
  value       = aws_sqs_queue.editais_falhas.url
}

output "bucket_documentos" {
  description = "Bucket S3 dos PDFs e textos extraidos (RADAR_DOCUMENTOS da analise)."
  value       = aws_s3_bucket.documentos.bucket
}

output "agenda_analise" {
  description = "Nome do schedule diario da analise com IA."
  value       = aws_scheduler_schedule.analisar_diario.name
}

output "url_fila_janelas" {
  description = "URL da fila SQS radar-janelas (RADAR_FILA_JANELAS_URL)."
  value       = aws_sqs_queue.janelas.url
}

output "url_fila_janelas_falhas" {
  description = "URL da DLQ radar-janelas-falhas (RADAR_FILA_JANELAS_FALHAS_URL)."
  value       = aws_sqs_queue.janelas_falhas.url
}
