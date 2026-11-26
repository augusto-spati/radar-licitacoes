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

O user pool manda os codigos pelo SES e so pode ser criado com o remetente
(`email_remetente`) ja verificado. Por isso o primeiro apply tem dois passos:

    terraform apply -target=aws_sesv2_email_identity.email
    # cada e-mail de email_remetente e emails_com_acesso recebe "Amazon Web Services -
    # Email Address Verification Request": clicar no link (o remetente, antes do passo seguinte)
    terraform apply

Se o remetente ainda nao clicou no link, o segundo apply falha ao criar o user pool
com um erro do SES de identidade nao verificada ("identity not verified").
Basta clicar no link e rodar `terraform apply`
de novo. Os e-mails ficam em minusculas (`lower()`), e o login nao diferencia maiusculas.

## Login (codigo por e-mail, SES no sandbox)

- O login e sem senha: a pessoa digita o e-mail, recebe um codigo de 6 digitos e entra.
  So entra quem esta em `emails_com_acesso`; o cadastro aberto esta desligado.
- A conta SES fica no **sandbox**: so manda para enderecos verificados, ate 200 por dia.
  Por isso o Terraform cria uma identidade SES para o remetente **e para cada e-mail com
  acesso**. Depois do apply, **cada pessoa clica uma vez** no e-mail de verificacao da AWS.
  Sem esse clique o codigo nao chega. Pessoa nova: acrescentar em `emails_com_acesso`,
  `terraform apply`, a pessoa clica no link.
- O primeiro e-mail com codigo pode cair no **spam** (remetente pessoal enviado pelo SES).
  Marque como "nao e spam" uma vez.
- O build do front usa `VITE_FONTE=api`, `VITE_COGNITO_REGION=us-east-1` e
  `VITE_COGNITO_CLIENT_ID=<saida cognito_client_id>`.

### Sair do sandbox depois (sem mudar codigo)

1. Pedir acesso de producao: console do SES > Account dashboard > Request production access
   (tipo Transactional, URL = `url_site`), ou
   `aws sesv2 put-account-details --production-access-enabled --mail-type TRANSACTIONAL --website-url <url_site> --contact-language EN`.
   A AWS responde em ate 96 h.
2. Aprovado o pedido, as identidades dos destinatarios deixam de ser necessarias: em
   `auth.tf`, troque `local.identidades_ses` por `toset([var.email_remetente])` e rode
   `terraform apply` (remove as identidades dos destinatarios; o remetente continua).
   O front e o user pool nao mudam.

## 4. URL do banco e front

1. Ponha a URL do Neon no parametro (o Terraform nunca sobrescreve o valor):

       aws ssm put-parameter --name /radar/prod/database_url --type SecureString --overwrite --value "<url do Neon>"

2. Faca o deploy do front no bucket `radar-licitacoes-front-<account_id>` e invalide a
   distribuicao (`id_distribuicao`). O site fica em `url_site`.

`/api/*` (menos `/api/status`) exige o JWT do user pool de `auth.tf`. Com
`exigir_login = false` o autorizador sai e a saida `aviso_api_sem_login` avisa.

## Alertas por e-mail

O SNS envia um e-mail de confirmacao para `email_alertas`. O dono precisa clicar no
link de confirmacao; sem isso a assinatura fica pendente e nenhum alarme chega.
O Budget (US$ 5/mes) avisa em 50%, 80% e 100% do gasto real e em 100% do previsto.

## Validacao sem conta AWS

    terraform fmt -check -recursive infra
    terraform -chdir=infra/bootstrap init -backend=false && terraform -chdir=infra/bootstrap validate
    terraform -chdir=infra/prod init -backend=false && terraform -chdir=infra/prod validate
