# Checklist da trilha

Cada item marcado aponta para o arquivo e o commit onde está implementado e para o vídeo em que será mostrado. Itens sem marca são futuros. Ao fechar o tema 2, decidir se este checklist vira repositório próprio.

## Sistemas de dados

- [x] Carga idempotente: planejar janelas, gravar página e registrar edital podem ser repetidos sem duplicar. `src/radar/coleta/janelas.py`, `src/radar/editais/registro.py` (commits `ef44208`, `1246d23`). Vídeo: a gravar (`docs/videos/01-carga-incremental.md`).
- [x] Versão mais nova vence: `ON CONFLICT DO UPDATE ... WHERE data_atualizacao < excluded.data_atualizacao`. `src/radar/editais/registro.py` (commit `1246d23`). Vídeo: a gravar.
- [x] Janelas com checkpoint e retomada: página gravada com commit, janela `em_andamento` órfã volta a `pendente`, um coletor por vez com advisory lock. `src/radar/coleta/janelas.py` (commits `ef44208`, `b44e49f`). Vídeo: a gravar.
- [x] Falha que não vira vazio: 200 malformado, total inconsistente e contagem incompleta derrubam a janela para `falha`. `src/radar/coleta/pncp.py`, `src/radar/coleta/janelas.py` (commits `469d4fb`, `b44e49f`). Vídeo: a gravar.
- [x] Retry com espera exponencial, jitter e espera maior no 429. `src/radar/coleta/pncp.py` (commits `4065ace`, `469d4fb`). Vídeo: a gravar.
- [x] Lookback na atualização diária: `radar atualizar --lookback 10`, com janelas de atualização reabertas quando concluídas antes do fim do dia. `src/radar/cli.py`, `src/radar/coleta/janelas.py` (commits `5dca21a`, `b44e49f`). Vídeo: a gravar.
- [x] Dado bruto separado do tratado: `pagina_bruta` (JSONB) e `edital` derivado. `migrations/versions/0001_esquema_inicial.py`, `src/radar/editais/registro.py` (commits `d895343`, `1246d23`). Vídeo: a gravar.
- [x] Busca de texto em português: coluna gerada, `unaccent` com função `IMMUTABLE` e índice GIN. `migrations/versions/0002_busca_texto.py`, `src/radar/editais/filtro.py` (commit `09ab90b`). Vídeo: a gravar.
- [x] Índice medido por `EXPLAIN (ANALYZE, BUFFERS)`, com e sem o GIN: 14 ms contra 81 ms e ~9 vezes menos blocos lidos, em 58.825 editais. `docs/etapas/01-carga-incremental.md`.
- [x] Filtro validado olhando os resultados: termo solto trocado por frases específicas (36 para 24 abertos). `config/filtro.toml` (commit `a2d5723`). Vídeo: a gravar.
- [x] Auditoria de cobertura, separada da coleta, com código de saída. `src/radar/coleta/janelas.py` (função `auditar`), `src/radar/cli.py` (commits `ef44208`, `5dca21a`, `24bc7a6`). Vídeo: a gravar.
- [x] Logs JSON estruturados na coleta. `src/radar/comum/logs.py` (commit `b80006d`). Vídeo: a gravar.
- [x] Contrato de API versionado e conferido por teste. `src/radar/api/`, `frontend/openapi.json`, `tests/unit/test_openapi_atualizado.py` (commits `7d380eb`, `72581ec`, `12a954b`). Vídeo: a gravar.
- [x] ~~Agendamento diário da atualização no Agendador de Tarefas (`scripts/agendar-atualizacao.ps1`).~~ Superado: a coleta diária roda na AWS (EventBridge Scheduler + Fargate/Lambda, `infra/prod/coletor.tf` e `janelas.tf`).

## Eventos e AWS

Filas ficam como futuro, no tema 2: outbox + SQS (etapa 4a; o pgmq não existe no Neon) e depois a coleta do PNCP por janela em Lambda (4b). A etapa "2b-brutos" (páginas brutas no S3) foi descartada: a retenção de brutos da etapa 3 (`limpar_brutos`) mantém o banco em cerca de 120 MB. O S3 volta no tema 4, para os PDFs dos editais.

- [x] Coleta, fila e processamento: outbox + SQS com consumidor idempotente (etapa 4a). `docs/etapas/04a-filas.md`. Vídeo: a gravar (`docs/videos/04a-filas.md`).
- [x] Padrão outbox e SQS (tema 2), com DLQ, alarme e `radar reprocessar-falhas`. `docs/etapas/04a-filas.md`. Vídeo: a gravar.
- [x] Coleta do PNCP por janela em Lambda, com fatias, lease com fencing e cortesia. `docs/etapas/04b-coleta-por-janela.md`. Vídeo: a gravar (`docs/videos/04b-coleta-por-janela.md`).
- [ ] Tempo total e custo da primeira rodada da 4b. A medir em 2026-12-03.
- [x] Infraestrutura como código (Terraform): S3, CloudFront, Lambda, API Gateway, ECS Fargate, EventBridge Scheduler, Cognito, SES, SSM, alarmes e orçamento. `infra/bootstrap/`, `infra/prod/*.tf`, `infra/README.md`. Vídeo: a gravar (`docs/videos/02b-aws.md`).
- [x] IAM com papel por função e deploy sem chave fixa (OIDC do GitHub Actions, trust com `sub` imutável e `job_workflow_ref`). `infra/prod/github_oidc.tf`, `.github/workflows/deploy.yml` (commits `80ec1d9`, `c34b14f`). Vídeo: a gravar.
- [x] Custos sob controle: sem NAT Gateway, load balancer, RDS, EC2 e EIP (teste `tests/unit/test_infra_estatico.py`) e orçamento de US$ 5 com alertas. `infra/prod/orcamento.tf`. Vídeo: a gravar.
- [ ] Custo real da primeira semana. A medir em 2026-12-03 (`docs/etapas/02b-aws.md`).
- [x] Coleta agendada na nuvem (Fargate pelo EventBridge Scheduler) e alarme "coleta não rodou". `infra/prod/coletor.tf`. Vídeo: a gravar.
- [x] Login por código (Cognito e SES em sandbox) com JWT no API Gateway. `infra/prod/auth.tf`, `infra/prod/api.tf`. Vídeo: a gravar.
- [x] Coletores Sanepar e Compesa no modelo multi-fonte. `docs/etapas/03-coletores.md`. Vídeo: a gravar (`docs/videos/03-coletores.md`).
- [x] Coletor da DAE S/A Jundiaí (Compra Eletrônica, postback de leitura do ASP.NET aprovado pelo Augusto). `src/radar/coleta/dae_jundiai.py`, migração `0012`, `docs/etapas/03-coletores.md`.
- [ ] DAE Jundiaí: data da sessão dos modos de disputa (só no PDF do edital, baixado por postback) para o edital aberto aparecer no filtro de abertos. Parser da capa pronto (`data_sessao_no_edital`); falta o Augusto aprovar o POST de download do anexo (nota da fonte, seção 7).
- [x] Etapa 2b-brutos (páginas brutas no S3): descartada, ver acima. Volta no tema 4.

## Observabilidade

- [x] Métricas da coleta: falhas, latência por página, atraso da atualização (frescor), com SLOs, dashboard e alarmes. `infra/prod/observabilidade.tf`, `docs/etapas/05-observabilidade.md`. Vídeo: a gravar (`docs/videos/05-observabilidade.md`).
- [x] OpenTelemetry: traços e logs correlacionados (X-Ray com Transaction Search, links pela fila). `docs/etapas/05-observabilidade.md`. Vídeo: a gravar.
- [x] Alerta quando a auditoria de cobertura falha: auditoria diária às 11:00, evento `auditoria_falhou` e alarme `radar-auditoria-falhou` (10º alarme). `infra/prod/coletor.tf`, `src/radar/cli.py`.

## IA aplicada

- [x] Extração de texto do PDF do edital com pypdf (sem torch; OCR fora do escopo), PDF e texto no armazém (S3). `src/radar/analise/`, `docs/etapas/06-ia-editais.md`. Vídeo: a gravar (`docs/videos/06-ia-editais.md`).
- [x] Extração das exigências com saída estruturada (Converse do Bedrock e Pydantic, sem instructor), ancoragem do trecho e junção entre trechos. `src/radar/analise/`, `docs/etapas/06-ia-editais.md`. Vídeo: a gravar.
- [x] Comparação com o perfil da empresa por regras determinísticas (sem pgvector; o LLM só extrai). `src/radar/analise/comparacao.py`, `config/perfil.toml`. Vídeo: a gravar.
- [x] Provedor de modelo trocável, cache por hash e teto de custo. `src/radar/analise/`, `config/ia.toml`. Vídeo: a gravar.
- [x] Harness de evals com precisão e recall por campo e replay sem rede no CI. `evals/`, `src/radar/analise/evals.py`. Vídeo: a gravar.
- [ ] Gabaritos conferidos pelo Augusto (12 rascunhos com `conferido: false` em `evals/gabarito/`), seguidos da rodada real comparando modelos.
- [x] Tela das exigências (etapa 6b).

## Histórico de resultados (etapa 7)

- [x] Carga histórica de 12 meses do PNCP por UF, retomável por checkpoint, gravando só o que casa com o filtro (Neon de 0,5 GB). `src/radar/coleta/historico.py`, `radar historico carregar`. `docs/etapas/07-historico.md`. Vídeo: a gravar (`docs/videos/07-historico.md`).
- [x] Resultados por item do PNCP (vencedor, valores homologados), desconto calculado e resposta instável que não apaga dado bom. `src/radar/coleta/resultados_pncp.py`, `src/radar/editais/resultados.py`, agenda às 05:00. Vídeo: a gravar.
- [x] Histórico do órgão, vencedores e régua de desconto por linha, UF e faixa de valor na ficha do edital. `src/radar/editais/historico.py`, `GET /api/historicos/{id}`, `frontend/src/componentes/HistoricoOrgao.tsx`, `ReguaDesconto.tsx`. Vídeo: a gravar.
- [ ] Carga histórica rodada em produção e números reais na doc da etapa.
- [x] Preço unitário pelo TCE-PE (7b.1): estimado e contratado por item, serviços-padrão, referências na ficha do edital. `src/radar/precos/`, `src/radar/coleta/tcepe.py`, `docs/etapas/07b-precos.md`.
- [x] Preço unitário estimado das planilhas dos editais do PNCP (7b.2): planilha achada pelo nome (dentro de ZIP) ou pelas páginas com R$, leitura determinística com guarda aritmética, IA só no texto picotado com tetos próprios, contexto da obra na classificação, republicação. `src/radar/precos/planilha_leitura.py`, `src/radar/precos/planilhas.py`, `src/radar/analise/planilhas.py`, `radar planilhas atualizar` (10:00), `docs/etapas/07b-precos.md`.
- [ ] 7b.2 em produção: primeira rodada real, recall por formato e gabaritos das 6 planilhas conferidos pelo Augusto.
- [ ] "Estimado com desconto" (planilha × homologado ÷ estimado da 7a) na consulta.
- [ ] Resultados da Compesa pelo TCE-PE.

## Front com IA

Exceção registrada em D7: o front é feito majoritariamente com IA e não conta como argumento de competência. O que se estuda aqui é como fazer a IA trabalhar dentro de limites que o CI faz valer.

- [x] Front em React, Vite e Astryx com camada de dados trocável (exemplo ou API). `frontend/src/dados/fonte.ts`, `frontend/src/paginas/` (commits `c2b71b4`, `44fb252`, `4caaa8e`). Vídeo: a gravar.
- [x] Rotas em lista fechada, num arquivo que também gera o menu. `frontend/src/rotas.tsx`, `frontend/src/rotas.test.ts` (commit `c2b71b4`). Vídeo: a gravar.
- [x] Fronteiras de importação e proibição de HTML cru, `style`/`className` e bibliotecas de UI paralelas. `frontend/eslint.config.js` (commits `c2b71b4`, `a66247a`). Vídeo: a gravar.
- [x] Regra que não se desliga por comentário e não escapa de `src/`. `frontend/eslint.config.js`, `frontend/scripts/verificar-contrato.mjs` (commit `3b13867`). Vídeo: a gravar.
- [x] Listas fechadas de componentes e de dependências, e teste das próprias regras do lint. `frontend/src/componentes.test.ts`, `frontend/src/dependencias.test.ts`, `frontend/src/eslint-contrato.test.ts` (commits `c2b71b4`, `a66247a`, `3b13867`). Vídeo: a gravar.
- [x] Detecção de código duplicado no CI (jscpd). `frontend/.jscpd.json` (commit `c2b71b4`). Vídeo: a gravar.
- [x] Tipos do front gerados do OpenAPI e conferidos no CI. `frontend/src/dados/api.d.ts`, `.github/workflows/ci.yml` (commits `44fb252`, `3ae340c`). Vídeo: a gravar.
- [x] Screenshot do shell em celular e desktop, gerado no CI Linux. `frontend/e2e/` (commits `b5ddb19`, `0198f00`). Vídeo: a gravar.
- [x] Guarda dos arquivos do contrato por trailer `Contrato-Front: aprovado` e `CODEOWNERS`. `frontend/scripts/contrato-guardado.mjs`, `.github/CODEOWNERS` (commit `3ae340c`). Vídeo: a gravar.
- [x] Guarda rodando o script da base via `pull_request_target` (job `guarda-base`). `.github/workflows/guarda-base.yml`. Vídeo: a gravar (`docs/videos/02a-prototipo.md`).
- [ ] Revisão pelo Front-End-Checklist (acessibilidade, segurança, performance).
- [ ] Storybook MCP, quando houver cerca de 5 componentes próprios.
