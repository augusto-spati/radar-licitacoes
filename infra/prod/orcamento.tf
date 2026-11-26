resource "aws_sns_topic" "alertas" {
  name = "radar-alertas"
}

resource "aws_sns_topic_subscription" "alertas_email" {
  topic_arn = aws_sns_topic.alertas.arn
  protocol  = "email"
  endpoint  = var.email_alertas
}

resource "aws_budgets_budget" "mensal" {
  name         = "radar-licitacoes-mensal"
  budget_type  = "COST"
  limit_amount = tostring(var.limite_orcamento_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = {
      real_50      = { limite = 50, tipo = "ACTUAL" }
      real_80      = { limite = 80, tipo = "ACTUAL" }
      real_100     = { limite = 100, tipo = "ACTUAL" }
      previsto_100 = { limite = 100, tipo = "FORECASTED" }
    }

    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.limite
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.tipo
      subscriber_email_addresses = [var.email_alertas]
    }
  }
}
