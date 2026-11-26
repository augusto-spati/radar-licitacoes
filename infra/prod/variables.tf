variable "regiao" {
  description = "Regiao AWS."
  type        = string
  default     = "us-east-1"
}

variable "email_alertas" {
  description = "E-mail que recebe os alertas do Budgets e do SNS."
  type        = string
}

variable "limite_orcamento_usd" {
  description = "Limite mensal de custo, em dolares."
  type        = number
  default     = 5
}

variable "emails_com_acesso" {
  description = "E-mails autorizados a entrar no radar."
  type        = list(string)
  default     = []
}
