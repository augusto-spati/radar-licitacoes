# Vídeo 2b: o radar na AWS com Terraform, a custo quase zero

Roteiro em tópicos. Material de apoio: `docs/etapas/02b-aws.md`, `infra/README.md`, `infra/prod/*.tf` e `.github/workflows/deploy.yml`. Os custos reais só existem depois de uma semana no ar: a parte de custo do vídeo depende da medição de 2026-12-03.

## Gancho (30 s)

- Pergunta: "Dá para ter front, API, login, coleta agendada e alarmes na AWS gastando quase nada?"
- Mostrar a regra no código: o teste `tests/unit/test_infra_estatico.py` reprova NAT Gateway, load balancer, RDS, EC2 e EIP. O custo é uma propriedade testada, não uma esperança.
- Promessa: no fim, os três problemas que só apareceram na conta real.

## A arquitetura (4 min)

Desenhar na tela e depois mostrar o Terraform correspondente.

- **Front:** bucket S3 privado atrás do CloudFront com origin access control (`front.tf`).
- **API:** FastAPI numa Lambda em imagem de contêiner, atrás de um API Gateway HTTP (`api.tf`). A Lambda fica fora de VPC e usa `NullPool` (`RADAR_DB_NULLPOOL=true`) com a URL do pooler do Neon, que vem do SSM Parameter Store.
- **Login:** Cognito com JWT no autorizador do API Gateway. `/api/*` exige token, menos `/api/status` (`auth.tf`).
- **Coleta:** task no ECS Fargate (256 de CPU, 512 MB), sem service e sem NAT, disparada pelo EventBridge Scheduler às 6h de Brasília. Sobe, roda `radar atualizar` e para (`coletor.tf`).
- **Banco:** Neon, fora da AWS. Sem RDS, que cobraria parado.
- **Alertas:** SNS por e-mail, dois alarmes e um orçamento de US$ 5 por mês (`orcamento.tf`).

## Por que us-east-2 (1 min)

- Os projetos da "nova experiência AWS" só criam recursos na região do próprio projeto. A conta nasceu nessa região, então o Terraform, os repositórios do ECR e o resto ficam em `us-east-2`.
- Ponto a fixar: a região veio de uma restrição da conta, não de uma escolha de latência. Dizer isso sem enfeite.

## Login por código com o SES em sandbox (3 min)

- Login sem senha: a pessoa digita o e-mail, recebe um código de 6 dígitos e entra. Só entra quem está em `emails_com_acesso`, e o cadastro aberto está desligado.
- O SES em sandbox só manda para endereços verificados. Por isso o Terraform cria uma identidade para o remetente e uma para cada e-mail com acesso, e cada pessoa clica uma vez no e-mail de verificação.
- A ordem importa: o user pool só é criado com o remetente já verificado, daí o primeiro apply em dois passos (`-target=aws_sesv2_email_identity.email`, clicar, aplicar o resto).
- Mostrar o caminho de saída do sandbox (pedido de acesso de produção) e que ele não muda código: troca `local.identidades_ses` por só o remetente.

## O deploy sem chave fixa (3 min)

- `github_oidc.tf` cria o provedor OIDC e a role `radar-deploy`. Nenhuma chave de acesso mora no GitHub.
- A role só é assumida pelo `sub` do branch `main` e pelo `job_workflow_ref` do `deploy.yml`. Outro workflow na `main` não assume.
- O `deploy.yml` separa o build do front (job sem credenciais e sem `id-token`, `npm ci --ignore-scripts`) do job de deploy. Os builds das imagens rodam antes de as credenciais existirem no ambiente.
- Deploy: imagens com tag do SHA, `update-function-code` na Lambda, nova revisão da task do coletor copiada da última com só a imagem trocada, `s3 sync` e invalidação do CloudFront.
- O CI nunca roda `terraform apply`. Consequência a mostrar: um apply manual depois precisa de `-var tag_coletor=<sha>`.

## Os alarmes (2 min)

- (a) Task que parou com erro, por regra do EventBridge no código de saída diferente de zero.
- (b) "Coleta não rodou": um filtro de métrica conta as linhas `resultado:` que a CLI imprime ao fim de toda execução. O alarme usa 5 períodos de 6 h, exige os 5 em violação e trata dado ausente como violação (`treat_missing_data = "breaching"`). Dispara se passarem cerca de 30 h sem nenhuma execução concluída.
- Por que `breaching`: se o coletor nunca roda, não há métrica nenhuma. Sem essa opção, o silêncio seria lido como "tudo bem".
- Avisar que o alarme nasce em ALARM e só sai dele na primeira execução agendada.

## Os três problemas que só apareceram na conta real (8 min)

Para cada um: o sintoma, a causa, a correção. Gravar com o terminal e o erro real à vista.

1. **O token do ECR não cabe no cofre de credenciais do Windows.**
   - Sintoma: `docker login` no ECR falha com "The stub received bad data".
   - Causa: o token de autorização do ECR é grande demais para o cofre de credenciais do Windows, que o Docker usa por padrão.
   - Contorno: um `DOCKER_CONFIG` temporário, sem o helper de credenciais, só para o push inicial da tag `bootstrap`. No CI (Linux) o problema não existe.
2. **O GitHub OIDC com `sub` imutável.**
   - Sintoma: `AssumeRoleWithWebIdentity` recusado, com a role e o repositório aparentemente certos.
   - Causa: repositórios novos usam o `sub` imutável, com os ids numéricos: `repo:dono@id/repo@id:ref:...`, e não `repo:dono/repo:ref:...`. A trust policy esperava o formato antigo.
   - Como se descobre: `gh api repos/<dono>/<repo>/actions/oidc/customization/sub`, campo `sub_claim_prefix`. Mostrar a saída real.
   - Correção: a variável `prefixo_sub_github` com o prefixo conferido, e a condição `sub` montada a partir dela.
3. **O `docker push` precisa ler o repositório do coletor.**
   - Sintoma: 403 no push, mesmo com todas as permissões de escrita concedidas.
   - Causa: o `docker push` faz um HEAD do manifesto antes de enviar e isso exige `ecr:BatchGetImage`, também no repositório do coletor, não só no da API.
   - Correção: `BatchGetImage` e `GetDownloadUrlForLayer` nos dois repositórios (commits `80ec1d9` e `c34b14f`).

Ponto a fixar: nenhum dos três aparece em `terraform validate` nem em teste. Só a conta real mostra.

## Custo (2 min)

- Hoje nada cobra parado: sem NAT, sem load balancer, sem RDS, sem EC2. Fargate cobra só enquanto roda e a Lambda só por requisição.
- Orçamento de US$ 5 por mês, com aviso em 50%, 80% e 100% do gasto real e 100% do previsto.
- **A medir em 2026-12-03:** o custo da primeira semana (Cost Explorer por serviço). Gravar esta parte só depois da medição e dizer o número real, qualquer que seja.

## Perguntas (1 min)

Deixar na tela e responder no vídeo (as de `docs/etapas/02b-aws.md`):

1. Por que OIDC e não uma chave de acesso no GitHub?
2. O que o `sub` imutável protege?
3. Por que a Lambda fica fora de VPC e usa `NullPool` com o pooler do Neon?
4. Como o alarme "coleta não rodou" funciona, e o que faz o `treat_missing_data`?
5. Por que o front é buildado num job sem credenciais?

## Fecho

- Entregue: o radar no ar, com login, coleta diária, alarmes e deploy sem chave fixa.
- Descartado: o armazenamento de brutos no S3 (etapa 2b-brutos). A retenção da etapa 3 mantém o banco em cerca de 120 MB. O S3 volta no tema 4, para os PDFs.
- Próximo: os coletores Sanepar e Compesa (etapa 3) e, depois, filas (tema 2).
