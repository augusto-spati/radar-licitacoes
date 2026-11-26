output "topico_alertas_arn" {
  description = "ARN do topico SNS radar-alertas."
  value       = aws_sns_topic.alertas.arn
}
