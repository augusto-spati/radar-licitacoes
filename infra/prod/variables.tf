variable "regiao" {
  description = "Regiao AWS."
  type        = string
  default     = "us-east-1"
}

variable "email_alertas" {
  description = "E-mail que recebe os alertas do Budgets e do SNS."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.email_alertas))
    error_message = "email_alertas deve ser um e-mail valido."
  }
}

variable "limite_orcamento_usd" {
  description = "Limite mensal de custo, em dolares."
  type        = number
  default     = 5

  validation {
    condition     = var.limite_orcamento_usd > 0
    error_message = "limite_orcamento_usd deve ser maior que zero."
  }
}

variable "emails_com_acesso" {
  description = "E-mails autorizados a entrar no radar."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for e in var.emails_com_acesso : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", e))])
    error_message = "Todos os itens de emails_com_acesso devem ser e-mails validos."
  }
}

variable "imagem_api" {
  description = "URI da imagem da Lambda. Vazio usa <ecr radar-api>:bootstrap."
  type        = string
  default     = ""
}

variable "exigir_login" {
  description = "Exige JWT do Cognito em /api/* (exceto /api/status)."
  type        = bool
  default     = true
}

variable "cognito" {
  description = "Emissor e audiencia do JWT. Preenchido na Task 5; null desliga o autorizador."
  type = object({
    issuer   = string
    audience = list(string)
  })
  default = null
}

variable "tag_coletor" {
  description = "Tag da imagem do coletor na task definition. O deploy (Task 7) registra revisoes novas com a tag do SHA."
  type        = string
  default     = "bootstrap"
}
