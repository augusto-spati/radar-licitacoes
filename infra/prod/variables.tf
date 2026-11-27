variable "regiao" {
  description = "Regiao AWS."
  type        = string
  default     = "us-east-2"
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

variable "email_remetente" {
  description = "Remetente (FROM) dos codigos de login, verificado no SES."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.email_remetente))
    error_message = "email_remetente deve ser um e-mail valido."
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
  description = "Sobrescreve emissor e audiencia do JWT. null (padrao) usa o user pool e o app client de auth.tf."
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

variable "modelos_ia" {
  description = "Ids de foundation model do Bedrock (us-east-2, on-demand) que a analise pode chamar."
  type        = list(string)
  default = [
    "amazon.nova-lite-v1:0",
    "openai.gpt-oss-120b-1:0",
    "qwen.qwen3-32b-v1:0",
    "mistral.ministral-3-14b-instruct",
  ]

  validation {
    condition     = alltrue([for m in var.modelos_ia : !can(regex("^(us|eu|apac|global)\\.", m))])
    error_message = "modelos_ia aceita so ids de foundation model; perfis de inferencia (us., eu., apac., global.) sao cross-region."
  }
}

variable "analise_agendada" {
  description = "Liga a agenda diaria do `radar analisar`. O primeiro apply vai com false, ate uma execucao manual provar a role e os overrides do alvo universal."
  type        = bool
  default     = true
}
