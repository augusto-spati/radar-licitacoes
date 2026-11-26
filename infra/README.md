# Infraestrutura (Terraform)

Regiao `us-east-1`. Nada aqui cobra parado: o teste `tests/unit/test_infra_estatico.py`
reprova NAT Gateway, load balancer, RDS, EC2 e EIP.

## 1. Bootstrap (uma vez)

Cria o bucket do estado, `radar-licitacoes-tfstate-<account_id>`. O estado do
bootstrap e local e nao vai para o repositorio.

    cd infra/bootstrap
    terraform init && terraform apply

## 2. Ambiente `prod`

    cd infra/prod
    cp terraform.tfvars.example terraform.tfvars   # preencher; nao e versionado
    terraform init -backend-config="bucket=radar-licitacoes-tfstate-<account_id>"
    terraform plan

O estado fica no S3 com lock nativo (`use_lockfile`), chave `prod/terraform.tfstate`.

## Alertas por e-mail

O SNS envia um e-mail de confirmacao para `email_alertas`. O dono precisa clicar no
link de confirmacao; sem isso a assinatura fica pendente e nenhum alarme chega.
O Budget (US$ 5/mes) avisa em 50%, 80% e 100% do gasto real e em 100% do previsto.

## Validacao sem conta AWS

    terraform fmt -check -recursive infra
    terraform -chdir=infra/bootstrap init -backend=false && terraform -chdir=infra/bootstrap validate
    terraform -chdir=infra/prod init -backend=false && terraform -chdir=infra/prod validate
