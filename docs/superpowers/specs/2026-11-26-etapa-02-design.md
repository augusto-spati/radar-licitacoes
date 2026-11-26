# Radar de licitações: desenho da etapa 2 (tela e AWS)

Escrito em 2026-11-26, a partir da conversa com o Augusto. Complementa o desenho da etapa 1 (`2026-11-26-radar-design.md`).

## Intenção

O tio do Augusto (FR Saneamento) recebe um link que abre quando quiser, no celular ou no escritório, e vê os editais abertos que batem com as linhas de serviço da empresa, atualizados todo dia sem ninguém mexer. A etapa coloca o radar na AWS. Com isso ela adianta a parte "AWS" do tema 2; as filas ficam para depois, sobre esta infraestrutura.

O front fica fora da trilha de estudo e é feito majoritariamente com IA (decisão D7). O que o Augusto precisa saber defender nesta etapa:
- o contrato da API;
- a infraestrutura (Terraform, IAM, rede, custos);
- a separação entre dado bruto (S3) e dado tratado (Neon);
- os mecanismos que impedem a IA de desmontar o front.

## Metas, não metas e o que seria fracasso

- **Metas:**
  - um link com login, de pé até as festas de fim de ano de 2026;
  - a coleta diária rodando na nuvem;
  - um protótipo navegável antes do backend na nuvem;
  - um contrato do front que a IA não consegue quebrar sem o CI acusar.
- **Não metas:**
  - favoritos;
  - avisos por e-mail ou WhatsApp;
  - editar os termos do filtro pela tela;
  - multiempresa;
  - filas (tema 2);
  - OpenTelemetry (tema 3);
  - a análise de aderência (tema 4, aqui só um esboço).
- **Seria fracasso:**
  - conta da AWS gerando custo sem alerta;
  - NAT Gateway ou recurso sempre ligado sem necessidade;
  - um front em que a IA duplica componentes ou mexe na navegação sem que algum teste quebre;
  - credenciais no repositório público.

## Restrições

- Conta AWS nova, no plano grátis: US$ 200 em crédito, por 6 meses ou até acabar o crédito.
- Região us-east-1. Dado público; a latência de ~120 ms para o Brasil é aceitável.
- O banco tem que caber no plano grátis do Neon: 1 GB por projeto. Medida da etapa 1: 1 dia de editais ocupa ~10 MB, contando páginas brutas, editais e índices.
- cl. 10.1 do contrato com a Intertechne: a leitura registrada é que ela trata de prestar serviço pago. A hospedagem paga pelo Augusto para um projeto mostrado de graça ao tio não está nesse caso. Qualquer cobrança ao tio exige checar antes.
- O repositório é público: segredos só no AWS Secrets Manager/SSM ou nos secrets do GitHub.

## Decisões (do Augusto)

| # | Decisão | Alternativas | Por quê |
|---|---|---|---|
| E2-1 | Link acessível de qualquer lugar (não só no notebook) | Só local nas festas; notebook servindo por túnel | O tio precisa usar depois, por conta própria, para sabermos se o radar serve |
| E2-2 | Tudo na AWS | Cloudflare (padrão do Yudi); notebook + túnel | Fecha a lacuna de AWS vista nas vagas |
| E2-3 | Banco: Neon (Postgres gerenciado, grátis) e páginas brutas no S3 | RDS t4g.micro (~US$ 12/mês depois do plano grátis); Aurora Serverless v2 com pausa (~15 s para acordar); Cloudflare D1 (SQLite) | Custo zero permanente, continua Postgres (`unaccent`, busca de texto, jsonb), e separar bruto de tratado é conteúdo de sistemas de dados. RDS/VPC fica como estudo futuro (migração Neon → RDS) |
| E2-4 | Protótipo em código real (React + Astryx com dados de exemplo), publicado como link, antes do backend | Canvas de design com componentes imitados; canvas com Astryx empacotado | Nada é jogado fora: o protótipo vira o front. Shell e navegação ficam definidos antes, e servem de base para o teste de screenshot |
| E2-5 | Sem mapa das telas em canvas | Capturas num canvas | Dispensado pelo Augusto |

## Decisões propostas pelo Claude, aceitas sem objeção

| # | Decisão | Por quê |
|---|---|---|
| E2-6 | Arquitetura serverless (abaixo) | O App Runner não aceita clientes novos desde 30/04/2026. ECS sempre ligado com load balancer custa ~US$ 16/mês só no balanceador. EC2 único ensina pouco do que as vagas pedem |
| E2-7 | Terraform | Aparece em 6 dos arquivos de vagas salvos, contra 1 de CDK |
| E2-8 | Alerta de orçamento no AWS Budgets desde o primeiro `apply` | Conta nova, primeiro contato com custos da AWS |

## Arquitetura

```
EventBridge Scheduler (06:00 BRT)
        │
        ▼
ECS Fargate task (sub-rede pública, IP público, sem NAT)
  radar atualizar --lookback 10
        │  páginas brutas ──► S3 (bucket radar-brutos)
        │  editais ─────────► Neon (Postgres, us-east-1, TLS)
        ▼
Neon ◄── Lambda (FastAPI + Mangum, fora de VPC) ◄── API Gateway HTTP API
                                                        ▲
CloudFront ──► S3 (build do front) ─── navegador ───────┘
                       login: código por e-mail
```

- **Coleta:** a mesma imagem Docker do monolito, com o comando `radar atualizar`. Antes, a carga inicial dos 30 dias é feita do notebook para o Neon e o S3 (ou repetida na nuvem; escolha de quem construir).
- **API:** só leitura nesta etapa. Usa apenas o que os módulos expõem publicamente (`filtrar`, `EditalResumo` e o que for preciso para o detalhe).
- **Login:** código por e-mail, só para o Augusto e o tio. O mecanismo (Cognito com senha descartável por e-mail ou outro) **fica para pesquisa de quem construir**, com estas condições:
  - custo zero para 2 usuários;
  - sem senha para o tio decorar;
  - funcionar no celular.
- **Segredos:** a URL do Neon fica no SSM Parameter Store, como SecureString. Lambda e task leem pelo IAM.

## Mudanças no código da etapa 1

1. **Páginas brutas vão para um armazém.**
   - Cria-se uma interface de armazenamento de brutos no módulo `coleta`, com duas implementações: arquivo local (desenvolvimento e testes) e S3.
   - A tabela `pagina_bruta` deixa de guardar `conteudo` e passa a guardar a chave do objeto e o tamanho. As regras de janela da etapa 1 não mudam.
   - A coluna `edital.bruto` sai, ou passa a guardar só os campos necessários. Escolha de quem construir, com uma condição: o banco tem que caber em 1 GB com 12 meses de editais.
2. **`radar.api` entra nos contratos do import-linter** (a pendência da Task 1 da etapa 1).
3. **Configuração** do armazém de brutos e da URL do banco por variáveis `RADAR_*`.

## Front

### Telas da v1

| Tela | Conteúdo |
|---|---|
| Login | e-mail → código |
| Lista | editais abertos, do prazo mais próximo ao mais distante. Filtros de linha de serviço e UF. No topo, "atualizado às HH:MM" |
| Detalhe | objeto completo, órgão, município, modalidade, valor estimado, datas, linhas que bateram, botão para o sistema de origem |
| Vazio | nenhum edital aberto com os filtros atuais |
| Erro | a API falhou: mensagem e botão de tentar de novo |
| Desatualizado | aviso quando a última atualização tem mais de 36 h |
| Aderência (esboço) | um espaço reservado no detalhe ("em breve: comparação com o perfil da empresa"), sem lógica |

Esboço no celular (390 px):

```
┌──────────────────────────────┐
│ Radar FR · atualizado 06:12  │
│ [Todas▾] [SP PR PE MG▾]      │
├──────────────────────────────┤
│ encerra em 3 dias · 07/12    │
│ Embu-Guaçu/SP · Pregão       │
│ Rede de água e esgoto — re…  │
│ R$ 1,2 mi        [redes]  ›  │
└──────────────────────────────┘
```

### Contrato do front (impede a IA de desmontar)

1. **Skill oficial do Astryx** instalada no projeto (`astryx init`, versão travada). Toda página parte de um template do Astryx, e não há `<div>`, CSS ou cor solta no código.
2. **`frontend/CLAUDE.md`** com:
   - as rotas da v1, numa lista fechada;
   - o shell (layout, cabeçalho, navegação) declarado fixo;
   - a regra de procurar no Astryx e nos componentes próprios antes de criar um novo.
3. **Rotas num arquivo único**, que alimenta também a navegação.
4. **ESLint no CI:**
   - `eslint-plugin-boundaries` (página não importa página; componente não importa página);
   - proibição de bibliotecas de UI além do Astryx;
   - proibição de `<button>`, `<input>`, `<table>` e similares soltos fora dos componentes base.
5. **`jscpd`** no CI, para acusar código duplicado.
6. **Playwright:** screenshot do shell em 390 px e 1280 px, comparado com o aprovado, e um teste que confere a lista de rotas.
7. **Storybook MCP:** só quando houver uns 5 componentes próprios.
8. **Revisão pelo Front-End-Checklist,** só nas partes de acessibilidade, segurança e performance.

### Protótipo primeiro

- O front é construído com dados de exemplo tirados de editais reais da etapa 1, por uma camada de dados trocável (exemplo → API).
- O build é publicado como link de artefato para o Augusto revisar no celular. Ainda não foi testado se um build do Vite funciona como artefato; quem construir confere.
- Depois que o Augusto aprovar, as screenshots do shell viram a base dos testes.

## Ordem sugerida

1. Protótipo do front com dados de exemplo e o contrato do front.
2. API JSON local (FastAPI), e o front ligado a ela.
3. Armazém de brutos (local e S3) e a mudança em `pagina_bruta`.
4. Terraform: S3, CloudFront, Lambda, API Gateway, ECS/Fargate, EventBridge, SSM, Budgets. Neon criado à mão, ou via provider se existir um estável.
5. Carga dos dados, coleta agendada na nuvem e login.

Como são duas metades independentes (1 e 2 rodam localmente; 3 a 5 são nuvem), cabem **dois planos**: "2a: protótipo + API" e "2b: AWS".

- **2b, Decisão:** CloudFront roteia `/api/*` para o API Gateway (mesma origem, sem CORS); o front usa `/api` relativo.

## Saídas da etapa

- Código.
- Nota em `docs/etapas/02-...md`, com perguntas de entrevista sobre IAM, custos, bruto vs tratado e o contrato do front.
- Roteiro do vídeo.
- Itens no checklist: "Eventos e AWS" (a parte de infraestrutura) e uma seção nova, "Front com IA".

## Respostas à revisão de quem vai construir

- **Astryx:** é o design system da Meta (`github.com/meta/astryx`), licença MIT, em beta. Pacotes npm `@astryxdesign/core`, `@astryxdesign/theme-*` e `@astryxdesign/cli`. O CLI tem `init`, `build`, `template`, `component` e `search`, e há uma skill oficial do Claude Code em `plugins/astryx/skills/astryx/SKILL.md`. Requer React 19. Versão travada (sem ^ nem ~). Se faltar template para alguma tela, monta-se com os componentes do Astryx sem trocar de biblioteca. *(Escolha do Claude.)*
- **Plano B do protótipo:** se o build do Vite não funcionar como artefato, o protótipo vai para S3 + CloudFront já no começo da 2b, só com essa parte do Terraform. *(Escolha do Claude.)*
- **Deploy:** GitHub Actions com OIDC (um papel IAM sem chave fixa), publicando a imagem no ECR, a Lambda e o front. O ECR entra na lista do Terraform. O `terraform apply` continua manual, feito pelo Augusto. *(Escolha do Claude: OIDC é conteúdo de IAM para as perguntas de entrevista.)*
- **Prazo:** o Augusto avalia que tudo cabe até as festas e não quer definir ordem de corte. O escopo inteiro fica, inclusive o contrato do front completo e o deploy automático.
- **E-mail do tio:** no começo só o Augusto tem acesso ao login. O tio é incluído nas festas. *(Escolha do Claude, já que a pergunta ficou sem resposta.)*
- **Screenshots de referência** são gerados no CI (Linux), nunca no Windows. *(Escolha de quem construir.)*
- **Passos manuais do Augusto:** pontos de parada explícitos no plano. Criar a conta AWS (exige cartão), MFA no usuário raiz, criar o projeto Neon, informar o e-mail do Budgets e os e-mails com acesso ao login.

## O que fica para quem construir

- Mecanismo de login, dentro das condições acima.
- Nomes de recursos, tags e estrutura dos módulos Terraform.
- Estado do Terraform: S3 com lock nativo ou outro.
- Se a carga inicial é migrada do notebook ou repetida na nuvem.
- O que sobra de `edital.bruto`.
- O layout fino das telas, dentro dos templates do Astryx.
