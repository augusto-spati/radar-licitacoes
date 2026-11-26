# Infraestrutura (Terraform)

Regiao `us-east-1`. Nada aqui cobra parado: o teste `tests/unit/test_infra_estatico.py`
reprova NAT Gateway, load balancer, RDS, EC2 e EIP.

## 1. Bootstrap (uma vez)

Cria o bucket do estado, `radar-licitacoes-tfstate-<account_id>`. O estado do
bootstrap e local e nao vai para o repositorio.

    cd infra/bootstrap
    terraform init && terraform apply

## 2. Imagem inicial da API (antes do primeiro apply de `prod`)

A Lambda precisa de uma imagem existente no ECR. Como o repositorio so existe depois
do apply, o primeiro deploy tem tres passos: criar os repositorios, empurrar a tag
`bootstrap` e aplicar o resto.

    cd infra/prod
    cp terraform.tfvars.example terraform.tfvars   # preencher; nao e versionado
    terraform init -backend-config="bucket=radar-licitacoes-tfstate-<account_id>"
    terraform apply -target=aws_ecr_repository.api -target=aws_ecr_repository.coletor
    aws ecr get-login-password | docker login --username AWS --password-stdin <account_id>.dkr.ecr.us-east-1.amazonaws.com
    docker build --platform linux/amd64 --provenance=false -f docker/api-lambda.Dockerfile -t <account_id>.dkr.ecr.us-east-1.amazonaws.com/radar-api:bootstrap .
    docker push <account_id>.dkr.ecr.us-east-1.amazonaws.com/radar-api:bootstrap

Rode o `docker build` na raiz do repositorio. `--platform linux/amd64 --provenance=false`
e obrigatorio: o Lambda rejeita manifestos OCI index (o buildx gera um com proveniencia).
O workflow de deploy (Task 7) deve usar as mesmas flags.

As tags dos repositorios sao mutaveis: o deploy normal publica tags por SHA. A tag
`bootstrap` so e necessaria na criacao da Lambda; depois o deploy e dono da imagem
(`ignore_changes = [image_uri]`) e a politica de ciclo de vida pode expirar a `bootstrap`.

## 3. Ambiente `prod`

    terraform apply

## 4. URL do banco e front

1. Ponha a URL do Neon no parametro (o Terraform nunca sobrescreve o valor):

       aws ssm put-parameter --name /radar/prod/database_url --type SecureString --overwrite --value "<url do Neon>"

2. Faca o deploy do front no bucket `radar-licitacoes-front-<account_id>` e invalide a
   distribuicao (`id_distribuicao`). O site fica em `url_site`.

Ate o Cognito ser configurado (variavel `cognito`), `/api/*` fica sem autorizador e a
saida `aviso_api_sem_login` avisa.

## Alertas por e-mail

O SNS envia um e-mail de confirmacao para `email_alertas`. O dono precisa clicar no
link de confirmacao; sem isso a assinatura fica pendente e nenhum alarme chega.
O Budget (US$ 5/mes) avisa em 50%, 80% e 100% do gasto real e em 100% do previsto.

## Validacao sem conta AWS

    terraform fmt -check -recursive infra
    terraform -chdir=infra/bootstrap init -backend=false && terraform -chdir=infra/bootstrap validate
    terraform -chdir=infra/prod init -backend=false && terraform -chdir=infra/prod validate
