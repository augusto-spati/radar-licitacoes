# Etapa 2b: o radar na AWS (rascunho)

Segunda metade da etapa 2. Leva a API, o front e a coleta diária para a AWS com Terraform, sem custo parado. Rascunho: as respostas às perguntas e os custos reais ainda não foram preenchidos.

## O que foi feito

1. **Terraform em `infra/`.** `infra/bootstrap` cria o bucket do estado; `infra/prod` cria o resto. Região `us-east-2`.
2. **Front:** bucket S3 privado e CloudFront com origin access control (`infra/prod/front.tf`).
3. **API:** FastAPI numa Lambda em imagem de contêiner, atrás de um API Gateway HTTP com autorizador JWT do Cognito (`api.tf`, `auth.tf`). `/api/status` é público.
4. **Login por código** de 6 dígitos enviado pelo SES em sandbox, só para quem está em `emails_com_acesso` (`auth.tf`).
5. **Coleta:** task no ECS Fargate, sem service e sem NAT, disparada pelo EventBridge Scheduler às 6h de Brasília (`coletor.tf`).
6. **Banco:** Neon, com a URL num parâmetro SecureString do SSM (`ssm.tf`).
7. **Alertas:** alarme de task com erro, alarme "coleta não rodou" e orçamento de US$ 5 por mês, tudo por SNS e e-mail (`coletor.tf`, `orcamento.tf`).
8. **Deploy sem chave fixa:** OIDC do GitHub Actions e a role `radar-deploy` (`github_oidc.tf`, `.github/workflows/deploy.yml`).
9. **Custo como teste:** `tests/unit/test_infra_estatico.py` reprova NAT Gateway, load balancer, RDS, EC2 e EIP.

## Arquitetura

    navegador -> CloudFront -> S3 (front)
                      |
                      +-> /api/* -> API Gateway HTTP (JWT do Cognito) -> Lambda (imagem) -> Neon
    EventBridge Scheduler (06:00, America/Sao_Paulo) -> ECS Fargate (radar atualizar) -> Neon
    logs do coletor -> filtro de métrica -> alarme -> SNS -> e-mail

Por que `us-east-2`: os projetos da "nova experiência AWS" só criam recursos na região do próprio projeto, e a conta nasceu nela.

## Os três problemas que só apareceram na conta real

1. **Token do ECR grande demais para o cofre de credenciais do Windows.** `docker login` falhava com "The stub received bad data". Contornado com um `DOCKER_CONFIG` temporário, sem o helper de credenciais, só no push manual inicial. No CI, em Linux, não ocorre.
2. **GitHub OIDC com `sub` imutável.** Repositórios novos emitem `repo:dono@id/repo@id:ref:...`. A trust policy esperava `repo:dono/repo:ref:...` e o `AssumeRoleWithWebIdentity` era recusado. O prefixo real se confere com `gh api repos/<dono>/<repo>/actions/oidc/customization/sub` (campo `sub_claim_prefix`) e entra na variável `prefixo_sub_github` (`github_oidc.tf`).
3. **`docker push` exige `ecr:BatchGetImage` no repositório do coletor.** O push faz um HEAD do manifesto antes de enviar, e sem a permissão de leitura vinha 403. Correção nos commits `80ec1d9` e `c34b14f`.

## Custos da primeira semana

A medir em 2026-12-03 (Cost Explorer por serviço, mais o painel do Neon). Nenhum número foi medido até agora; a meta é custo próximo de zero, com o orçamento de US$ 5 por mês como teto de alarme.

## Perguntas de entrevista

**1. Por que OIDC e não uma chave de acesso guardada no GitHub?**

Resposta:

**2. O que o `sub` imutável protege?**

Resposta:

**3. Por que a Lambda fica fora de VPC e usa `NullPool` com o pooler do Neon?**

Resposta:

**4. Como o alarme "coleta não rodou" funciona, e o que o `treat_missing_data` muda?**

Resposta:

**5. Por que o front é buildado num job sem credenciais?**

Resposta:
