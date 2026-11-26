# Tags MUTABLE: o deploy publica tags por SHA e a tag "bootstrap" inicial
# (ver infra/README.md) precisa existir antes do primeiro apply.
resource "aws_ecr_repository" "api" {
  name                 = "radar-api"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "coletor" {
  name                 = "radar-coletor"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "manter_cinco" {
  for_each   = { api = aws_ecr_repository.api.name, coletor = aws_ecr_repository.coletor.name }
  repository = each.value

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Manter as 5 imagens mais recentes"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}
