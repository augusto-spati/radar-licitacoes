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
