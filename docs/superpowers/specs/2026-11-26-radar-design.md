# Radar de licitações: desenho da fundação e da etapa 1

Escrito em 2026-11-26 a partir da conversa com o Augusto. Complementa `radar-licitacoes.md` (propósito, usuário, escopo da v1) e `radar-pesquisa-repos.md` (pesquisa de repositórios). Quando o repositório existir, este conteúdo vira os arquivos em `docs/decisoes/`.

## Intenção

Projeto de estudo público com um usuário real (FR Saneamento, empresa do tio do Augusto). Cada tema da trilha (dados → eventos → observabilidade → IA) vira uma etapa com três saídas: a mudança no repositório, uma nota do que aprendeu e das trocas, e material para um vídeo ou post. **O estudo manda**: quando utilidade e aprendizado divergirem, vale o aprendizado. O Augusto precisa saber explicar cada mudança; o Claude explica, propõe, e ele decide.

## Metas, não metas e o que seria fracasso

- **Metas da v1:** (1) filtrar editais do PNCP pelas quatro linhas de serviço da empresa e pelos estados onde ela atua; (2) mais tarde, analisar a aderência do edital ao perfil da empresa. Uma tela para mostrar nas festas de fim de ano de 2026, com o que existir até lá.
- **Não metas agora:** portais próprios das companhias estaduais, histórico de preços e vencedores, radar de concessões, alertas, multiusuário, autenticação.
- **Seria fracasso mesmo funcionando:** código que o Augusto não sabe defender (exceto o front, ver D7); virar prestação de serviço sem pagamento; corte de aprendizado para caber na data; qualquer dado privado da empresa no repositório.

## Restrições

- Repositório público em `github.com/augusto-spati/radar-licitacoes`. Só dado público.
- Fonte: API pública do PNCP, sem chave. Lenta (~20 s por página), no máximo 50 registros por página em `contratacoes/*`, HTTP 500 intermitente, 429 em HTML sem `Retry-After` e limite por IP (este último documentado pela Base dos Dados, não testado).
- Roda no notebook Windows do Augusto, com Docker. Nada pago até a cl. 10.1 do contrato com a Intertechne ser checada.
- Tempo: blocos de noites, em ritmo de estudo.

## Decisões tomadas (pelo Augusto)

| # | Decisão | Alternativas consideradas | Por quê |
|---|---|---|---|
| D1 | Python | Go, .NET | Aparece nas vagas, ele já usou, é a linguagem mais forte para a parte de IA. .NET não fecha lacuna; Go somaria a linguagem aos conceitos. |
| D2 | Postgres | SQLite, DynamoDB, OpenSearch | Lacuna de SQL; transações, locks e índices reais (DDIA); busca de texto em português, upsert, JSONB e pgvector num banco só; existe gerenciado na AWS. |
| D3 | Monolito modular | Processos separados desde o início; microsserviços | *Monolith First*. As fronteiras entre módulos são onde as filas entram no tema 2 (em memória → pgmq → outbox + SQS). Confirmado pela pesquisa (python-ddd, cosmicpython). |
| D4 | Tecnologias (tabela abaixo) | Prefect/Dagster, pip/poetry | Ferramentas atuais; um orquestrador esconderia a lógica incremental que é o conteúdo do tema 1. |
| D5 | SQLAlchemy 2.0 com ORM | SQL à mão com psycopg 3; SQLAlchemy Core | Escolha do Augusto contra a recomendação do Claude (SQL à mão). Motivo não registrado. Compensações combinadas: log do SQL gerado em testes e desenvolvimento, upsert com `insert().on_conflict_do_update` do dialeto Postgres, `EXPLAIN ANALYZE` antes e depois de cada índice, registrado na nota da etapa. |
| D6 | Repositório na conta `augusto-spati` | Outra conta; renomear antes | Escolha do Augusto. |
| D7 | Front: React + Vite + TypeScript + Astryx (github.com/meta/astryx, beta, MIT); backend expõe API JSON com FastAPI | FastAPI + Jinja + HTMX; Streamlit; Next.js | Front não é foco e a IA o gera rápido; o Astryx foi feito para agentes. **Exceção registrada:** o front fica fora da trilha, é feito majoritariamente com IA e não conta como argumento de competência. O que o Augusto precisa defender é o contrato da API. Vite e não Next: build estático, que o FastAPI serve agora e o S3 serve depois. Versão do Astryx travada por estar em beta. |

### Tecnologias (D4)

| Parte | Escolha |
|---|---|
| Python e dependências | Python 3.13, uv (o Python da máquina é o 3.10.1, já sem suporte) |
| Qualidade | ruff (lint e formatação), mypy |
| Testes | pytest; testcontainers com Postgres real nos testes de integração |
| Banco local | Postgres 17 no docker compose |
| Acesso ao banco | SQLAlchemy 2.0 ORM (D5), driver psycopg 3 |
| Migrações | Alembic |
| HTTP | httpx + tenacity, com retry explícito em 500, 502, 503, 504, 429 e timeout |
| Modelos de entrada | Pydantic v2 |
| Execução | comandos de terminal com typer, agendados pelo Agendador de Tarefas do Windows |
| Logs | JSON estruturado desde o início; OpenTelemetry no tema 3 |
| API | FastAPI (JSON, OpenAPI) |
| Front | React + Vite + TypeScript + Astryx |
| CI | GitHub Actions: ruff, mypy, import-linter, pytest; build do front |
| Fronteiras | import-linter |

Fica para os temas seguintes: pgmq e depois SQS (tema 2); OpenTelemetry (tema 3); extração de PDF (docling ou marker, escolhido testando editais reais), instructor e pgvector (tema 4); onde roda na AWS e quanto custa (tema 2).

## Organização do repositório

```
radar-licitacoes/
├─ src/radar/
│  ├─ coleta/       # cliente PNCP, janelas de datas, retry, registro de janelas
│  ├─ editais/      # modelo dos editais, repositório, filtro por serviço e UF
│  ├─ analise/      # vazio até o tema 4
│  ├─ comum/        # configuração, sessão do banco, logs
│  ├─ api/          # porta de entrada HTTP (FastAPI)
│  └─ cli.py        # porta de entrada terminal (typer)
├─ frontend/        # React + Vite + Astryx
├─ migrations/      # Alembic
├─ tests/{unit,integration}/
├─ docs/
│  ├─ decisoes/     # 0001-python.md ... uma decisão por arquivo
│  ├─ etapas/       # nota de cada etapa
│  └─ videos/       # roteiro e material de cada etapa
├─ docker-compose.yml, pyproject.toml, uv.lock
└─ .github/workflows/ci.yml
```

Regras de fronteira (verificadas pelo import-linter no CI):
- Cada módulo (`coleta`, `editais`, `analise`) expõe só o que está no seu `__init__.py`; os outros não importam o que é interno dele.
- `api` e `cli` usam os módulos; nenhum módulo importa `api` ou `cli`.
- `comum` não importa nenhum módulo.

Uma tag git por etapa concluída (`etapa-01-carga-incremental`, ...), para cada vídeo apontar para o estado exato do código.

## Etapa 1: carga incremental do PNCP (tema 1, sistemas de dados)

**Desenho proposto pelo Claude a partir da pesquisa e confirmado pelo Augusto em 2026-11-26.**

1. **Carga inicial:** endpoint `/v1/contratacoes/publicacao` por dia e por modalidade, para os últimos 30 dias. Guarda tudo, do Brasil inteiro; o filtro roda no banco.
2. **Atualização diária:** endpoint de atualizações (`/v1/contratacoes/atualizacao`, usado pela Base dos Dados; **não testado por nós**), com 10 dias de lookback para pegar retificações e mudanças de situação.
3. **Janelas de coleta:** tabela `coleta_janela(endpoint, data_ini, data_fim, modalidade, status, tentativas, erro, paginas, registros)`. Janela que falha fica marcada como falha, nunca como vazia; uma nova rodada refaz só o que falta. Um 500 na página 1 divide a janela ao meio; um 500 em página profunda marca a janela como falha (padrão da Base dos Dados).
4. **Bruto e tratado:** tabela com o JSON bruto de cada página (JSONB), e a tabela `edital` derivada dela, com `numeroControlePNCP` como chave natural, upsert, e a data de atualização mais recente vencendo.
5. **Filtro:** por padrão mostra só os editais ainda abertos para proposta (`dataEncerramentoProposta` no futuro e situação ativa), com opção de mostrar todos. O banco guarda tudo. A tabela `edital` inclui data de abertura e de encerramento da proposta e situação, indexadas. Escolha do Claude. Busca de texto em português (`tsvector` com dicionário `portuguese`, índice GIN) sobre `objetoCompra`, com uma lista de termos por linha de serviço e uma lista de UFs, ambas em arquivo de configuração. Medir com `EXPLAIN ANALYZE` antes e depois do índice.
6. **Auditoria de cobertura:** comando separado da coleta que compara janelas esperadas com janelas concluídas.
7. **Saídas da etapa:** código e testes, `docs/etapas/01-carga-incremental.md`, material para o vídeo em `docs/videos/`.

Escolhas do Claude dentro da etapa 1, a confirmar:
- Modalidades coletadas: 4 (concorrência eletrônica), 5 (concorrência presencial), 6 (pregão eletrônico), 7 (pregão presencial). Dispensa fica fora (deu 500 no teste e raramente cobre esse tipo de serviço).
- UFs iniciais, tiradas dos casos públicos da empresa: SP, PR, PE, MG.
- Termos por linha de serviço: rascunho a partir da apresentação da empresa (hidrômetro, leitura, vazamento, perdas, setorização, rede de água, rede de esgoto, ligação, corte e religação, fraude); o Augusto revisa.
- Carga inicial de 30 dias, e não 60: pelas medidas, ~12 min por dia de pregões, então 30 dias ≈ 6 h de coleta, que pode ser retomada.

## Modo de trabalho e prazo

- **O Claude entrega cada módulo inteiro e o Augusto estuda depois** (escolha do Augusto, 2026-11-26). A regra antiga, "o Claude não entrega pronto", foi aposentada. Continua valendo entender e saber defender cada mudança. Por isso cada entrega vem com:
  - uma explicação curta de cada conceito e de cada troca feita;
  - de três a cinco perguntas no estilo de entrevista ("por que a janela é dividida no 500 da página 1 e não nas seguintes?"), que o Augusto responde na nota da etapa.

  A etapa só fecha (tag) quando as perguntas estão respondidas.
- **Prazo:** o Augusto avalia que a etapa 1 e a tela cabem até as festas. Se não couberem, a etapa 1 sai completa primeiro.

## Etapas seguintes (ordem, sem desenho ainda)

2. API JSON + tela com Astryx mostrando os editais filtrados (pequena, antes das festas). Revisão do front gerado por IA com um recorte do Front-End-Checklist (github.com/thedaviddias/Front-End-Checklist): acessibilidade, segurança e performance. SEO, privacidade e i18n ficam fora, por ser ferramenta interna.
3. Tema 2: coleta → fila → processamento; pgmq, depois outbox, depois SQS.
4. Tema 3: observabilidade da coleta (falhas, latência, atraso).
5. Tema 4: extração das exigências do edital com IA, comparação com o perfil, evals com gabarito feito à mão.

## O que fica para quem construir

- Nomes de tabelas, colunas e funções, além dos citados.
- Estrutura interna de cada módulo, desde que respeite as regras de fronteira.
- Configuração exata do ruff, do mypy e do retry (tempos, número de tentativas).
- Formato do log JSON.
