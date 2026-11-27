# Etapa 4b: coleta do PNCP por janela em Lambda — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** cada janela do PNCP vira uma mensagem na fila `radar-janelas`; a Lambda `radar-coletora` coleta fatias de até 540 s com checkpoint por página, lease com fencing token e mensagem de continuação; um planejador agendado enfileira as janelas e o Fargate fica só com os retratos.

**Architecture:** a lógica de fatia (reivindicar, coletar até o orçamento, concluir ou continuar) mora em `radar.coleta` e não conhece SQS: ela só grava na outbox. O roteamento por tipo (`FilaRoteada`) fica em `radar.comum`. Os handlers das duas Lambdas novas são portas de entrada em `radar.processador`, ao lado do consumidor da 4a. Localmente, `coletar_pendentes` chama a mesma fatia sem `lote` e sem orçamento.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0, Alembic, psycopg 3, Typer, boto3, pytest + testcontainers (Postgres 17), Terraform (provider AWS 5.x), EventBridge Scheduler, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-27-etapa-04b-coleta-por-janela-design.md` (ler inteiro antes de qualquer tarefa; a seção "Lease e fencing" é o contrato das Tasks 2, 3 e 5).

## Global Constraints

- Worktree `C:\Users\aschi\radar-wt-4b`, branch `etapa-4b`. Verificação geral: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run lint-imports && uv run pytest -q` (Docker ligado para os testes de integração).
- Recursos AWS só em us-east-2; nada fora do plano grátis; `tests/unit/test_infra_estatico.py` continua passando (inclusive `test_fila_tem_dlq_e_alarmes`, que conta 3 alarmes em `fila.tf`: os novos vão em `janelas.tf`).
- Trailer `Contrato-Front: aprovado` **só** no commit que muda `.github/workflows/deploy.yml` (autorização "atualizar funções Lambda novas no deploy", registrada no spec). Nenhum outro arquivo de `GUARDADOS` (`frontend/scripts/contrato-guardado.mjs`) é tocado.
- Toda mensagem de commit termina com:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` e `Claude-Session: https://claude.ai/code/session_012v6r8uz1aiPHNQoDZ66AVf`.
- Valores fixos do spec: orçamento 540 s; lease 960 s (`now()` do banco); Lambda coletora timeout 900 s, 256 MB, cliente PNCP com 3 tentativas; visibility timeout de `radar-janelas` 5.400 s; `maxReceiveCount = 5`; DLQ retém 14 dias (1.209.600 s); fila retém 4 dias; ESM `batch_size = 1`, `ReportBatchItemFailures`, `maximum_concurrency = 2`; planejador timeout 120 s, 256 MB, `cron(0 6 * * ? *)` em `America/Sao_Paulo`; recuo 10 dias; alarme de idade > 21.600 s; `radar-pncp-nao-concluiu` = Sum < 1 em 5 × 21.600 s com `treat_missing_data = "breaching"`; corte do `auditar` para janelas paradas = 6 h; total de alarmes no projeto ≤ 10.
- Nomes: fila `radar-janelas`, DLQ `radar-janelas-falhas`, Lambdas `radar-coletora` e `radar-planejador`, handlers `radar.processador.lambda_coletora.handler` e `radar.processador.lambda_planejador.handler`, tipo de outbox `coletar_janela` com carga `{"janela_id": n, "lote": n}`.
- Variáveis novas: `RADAR_FILA_JANELAS_URL`, `RADAR_FILA_JANELAS_FALHAS_URL`.
- Logs com os nomes do spec: `janela_concluida` (o filtro de métrica depende dele), e um log por desfecho (`janela_ocupada`, `janela_obsoleta`, `lease_perdida`, `janela_continua`).
- Nenhum advisory lock de sessão no caminho da Lambda.

## Review Focus

1. Duplicata de `coletar_janela` entregue enquanto a original ainda coleta (duas Lambdas): a segunda confirma como `ocupada` sem chamar o PNCP nem tocar `proxima_pagina` (teste na Task 2: `test_duplicata_concorrente_e_ocupada`).
2. Lambda morta por timeout no meio da fatia: a mesma mensagem reentregue depois do lease vencer retoma da página seguinte à última gravada, sem pedir as anteriores (teste na Task 2: `test_reentrega_retoma_do_checkpoint`).
3. Invocação que perdeu o lease (outra assumiu depois do vencimento) tenta gravar a página seguinte: não grava, o checkpoint não regride e o desfecho é `lease_perdida` (teste na Task 2: `test_fencing_bloqueia_quem_perdeu_o_lease`).
4. Janela coletada em várias fatias com o mesmo `numeroControlePNCP` em duas páginas: a completude conta distintos no banco, não por fatia (teste na Task 2: `test_completude_conta_distintos_entre_fatias`).
5. Relay do fim da invocação falha depois de a fatia commitar a continuação: a mensagem volta, a próxima entrega publica a continuação no começo e sai `obsoleta` (teste na Task 5: `test_relay_do_fim_falha_e_a_reentrega_sai_obsoleta`).

---

### Task 1: Migração 0006 e tipos novos

**Files:**
- Create: `migrations/versions/0006_janela_fatiada.py`
- Modify: `src/radar/coleta/modelos.py` (`ColetaJanela`), `src/radar/comum/outbox.py` (`TipoMensagem`, CHECK), `src/radar/comum/fila.py` (`de_corpo` valida a carga de `coletar_janela`)
- Test: `tests/integration/test_outbox.py`, `tests/integration/test_esquema.py`, `tests/unit/test_fila.py`

**Interfaces:**
- Produces: `ColetaJanela.proxima_pagina: Mapped[int]` (default 1), `total_paginas: Mapped[int | None]`, `esperados: Mapped[int | None]`, `lote: Mapped[int]` (default 0), `travada_por: Mapped[str | None]`, `travada_ate: Mapped[datetime | None]`; `TipoMensagem = Literal["pagina_coletada", "retrato_coletado", "coletar_janela"]`; `de_corpo` lança `ValueError` para `coletar_janela` sem `janela_id` ou `lote` inteiros.

- [ ] **Step 1:** testes: `test_coletar_janela_aceito_pelo_check` (insert com `tipo="coletar_janela"` não viola); `test_de_corpo_coletar_janela_sem_lote_e_invalido` (`ValueError`); `test_migracao_devolve_em_andamento_a_pendente` (linha `em_andamento` antes do upgrade → `pendente`, `proxima_pagina == 1`, `lote == 0`). O `alembic check` existente continua sem diferenças.
- [ ] **Step 2:** rodar `uv run pytest tests/integration/test_outbox.py tests/unit/test_fila.py -v` → FAIL.
- [ ] **Step 3:** migração 0006 conforme o spec ("Dados"): seis colunas, CHECK de `outbox.tipo` recriado com o terceiro valor, preenchimento de `em_andamento` → `pendente`. Downgrade apaga as linhas `coletar_janela` da outbox antes de recriar o CHECK antigo.
- [ ] **Step 4:** testes passam; `alembic check` sem diferenças.
- [ ] **Step 5:** commit `feat(coleta): checkpoint, lote e lease da janela (migração 0006)`.

### Task 2: Fatia com lease, fencing e continuação

Depende da Task 1. Paralela às Tasks 3 (mesmo pacote, arquivos diferentes), 4 e 7.

**Files:**
- Create: `src/radar/coleta/fatias.py`
- Modify: `src/radar/coleta/janelas.py` (`coletar_pendentes` e `_coletar_todas` passam a usar `coletar_fatia`; saem `CHAVE_COLETOR`, o reset geral de `em_andamento`, `_coletar_janela`, `_falhar`; `_gravar_pagina`, `_dividir` e `_sem_paginas` mudam para `fatias.py`), `src/radar/coleta/__init__.py`, `pyproject.toml` (`radar.coleta.fatias` entra em `forbidden_modules` do contrato "api e cli importam só a raiz")
- Test: `tests/integration/test_fatias.py`, `tests/integration/test_janelas.py`

**Interfaces:**
- Consumes: colunas e `TipoMensagem` da Task 1; `enfileirar` (4a); `ClientePNCP`, `ErroPNCP`.
- Produces:
  - `DesfechoFatia = Literal["concluida", "continua", "falha", "dividida", "ocupada", "obsoleta", "inexistente", "lease_perdida"]`.
  - `@dataclass(frozen=True) ResultadoFatia(desfecho: DesfechoFatia, paginas: int, registros: int, erro: str | None)` (`paginas`/`registros` = desta fatia).
  - `coletar_fatia(sessao: Session, cliente: ClientePNCP, janela_id: int, *, lote: int | None, token: str, orcamento_s: float | None, relogio: Callable[[], datetime] | None = None, cronometro: Callable[[], float] = time.monotonic) -> ResultadoFatia`. Regras exatamente as do spec ("Lease e fencing", "Fatia"). `lote=None` = modo laço (aceita qualquer `lote`, nunca enfileira). Exceção inesperada: marca falha, solta o lease, propaga.
  - `LEASE_S = 960`, `ORCAMENTO_LAMBDA_S = 540` em `fatias.py`, exportados por `radar.coleta`.
  - `coletar_pendentes(sessao, cliente, agora, relogio=None) -> ResumoColeta` mantém a assinatura; reivindica com `token = uuid4().hex`.

- [ ] **Step 1:** testes de integração com o `ClientePNCP` falso de `tests/integration/fakes.py` (conta chamadas por página) e cronômetro falso que avança 200 s por página:
  - `test_orcamento_corta_e_enfileira_continuacao` (5 páginas, orçamento 540 s → 3 páginas gravadas, `proxima_pagina == 4`, `lote == 1`, lease NULL, uma `coletar_janela` com `{"janela_id": id, "lote": 1}`, desfecho `continua`);
  - `test_continuacao_comeca_no_checkpoint` (fatia seguinte com `lote=1` só pede as páginas 4 e 5 e conclui);
  - `test_duplicata_concorrente_e_ocupada` (lease vivo de outro token → `ocupada`, zero chamadas ao PNCP, `proxima_pagina` intacta);
  - `test_reentrega_retoma_do_checkpoint` (páginas 1-2 gravadas, `travada_ate` no passado, mesmo `lote` → pede a partir da 3);
  - `test_lote_diferente_e_obsoleta`, `test_janela_concluida_confirma`, `test_janela_inexistente_confirma` (zero chamadas ao PNCP);
  - `test_fencing_bloqueia_quem_perdeu_o_lease` (o falso troca `travada_por` por outro token durante a página 2 → página 2 não gravada, `proxima_pagina == 2`, `lease_perdida`);
  - `test_completude_conta_distintos_entre_fatias` (3 fatias, `totalRegistros = 4`, um id repetido em duas páginas e só 3 distintos → `falha` "incompleta", `proxima_pagina == 1`, `total_paginas is None`);
  - `test_total_paginas_muda_entre_fatias_falha_e_zera` (página 1 diz 5, página 4 diz 6 → `falha`, checkpoint 1);
  - `test_erro_pncp_mantem_checkpoint` (`ErroPNCP` na página 3 → `falha`, `proxima_pagina == 3`, lease NULL, `erro` preenchido);
  - `test_divisao_no_modo_mensagem_enfileira_metades` (janela de 4 dias, erro na página 1 → duas janelas novas e duas `coletar_janela` com `lote == 0`, desfecho `dividida`);
  - `test_dia_sem_registros_conclui` (`total_paginas = 0` → `concluida`, `paginas == 0`);
  - `test_dois_coletar_pendentes_nao_repetem_janela` (dois processos com sessões próprias sobre 4 janelas → cada janela pedida uma vez).
- [ ] **Step 2:** rodar `uv run pytest tests/integration/test_fatias.py -v` → FAIL (import).
- [ ] **Step 3:** implementar `fatias.py`. Reivindicação num único `UPDATE ... RETURNING`; quando não pega linha, um `SELECT` decide entre `inexistente`, `concluida`, `obsoleta` e `ocupada`. Contagem de distintos com `func.jsonb_array_elements` sobre `PaginaBruta.conteudo["data"]`.
- [ ] **Step 4:** reescrever `coletar_pendentes` sobre `coletar_fatia`; os testes existentes de `test_janelas.py` passam sem mudar as asserções (ajustar só os que dependiam de `CHAVE_COLETOR` ou do reset geral, trocando por lease vencido).
- [ ] **Step 5:** `uv run pytest tests/integration/test_fatias.py tests/integration/test_janelas.py -v && uv run lint-imports` → PASS.
- [ ] **Step 6:** commit `feat(coleta): fatia de janela com lease, fencing e continuação`.

### Task 3: Planejador e auditoria no domínio

Depende da Task 1. Paralela às Tasks 2, 4 e 7 (toca `janelas.py`, longe das funções que a Task 2 muda; se rodar em paralelo, rebase antes de integrar).

**Files:**
- Modify: `src/radar/coleta/janelas.py`, `src/radar/coleta/__init__.py`
- Test: `tests/integration/test_janelas.py`

**Interfaces:**
- Consumes: colunas da Task 1; `enfileirar`.
- Produces:
  - `enfileirar_elegiveis(sessao: Session, endpoint: str) -> int` (reabre e enfileira conforme "Planejador" no spec, um commit; devolve quantas mensagens).
  - `@dataclass(frozen=True) JanelaParada(dia: date, modalidade: int, proxima_pagina: int, total_paginas: int | None)`; `janelas_paradas(sessao: Session, iniciadas_antes_de: datetime) -> list[JanelaParada]`.
  - `Auditoria.falhas` passa a `list[tuple[date, int, str, int, int | None]]` (acrescenta `proxima_pagina`, `total_paginas`).

- [ ] **Step 1:** testes: `test_enfileirar_reabre_concluida_cedo` (`concluida_em` antes do fim do dia em Brasília → `pendente`, `proxima_pagina == 1`, `lote` + 1, mensagem com o novo `lote`); `test_enfileirar_nao_reabre_concluida_depois_do_dia`; `test_enfileirar_pula_lease_vivo` (nem reabre nem enfileira); `test_enfileirar_cobre_pendente_falha_e_em_andamento_livre`; `test_janelas_paradas_so_em_andamento_antes_do_corte`; `test_auditar_mostra_onde_a_falha_parou`.
- [ ] **Step 2:** rodar → FAIL. Implementar reaproveitando a expressão de "concluída cedo demais" de `_proxima_janela` (extrair para uma função privada usada pelas duas).
- [ ] **Step 3:** testes passam.
- [ ] **Step 4:** commit `feat(coleta): planejar e enfileirar janelas elegíveis; janelas paradas na auditoria`.

### Task 4: Fila roteada e configuração

Depende da Task 1. Paralela às Tasks 2, 3 e 7.

**Files:**
- Modify: `src/radar/comum/fila.py`, `src/radar/comum/config.py`, `src/radar/comum/__init__.py`, `src/radar/processador/fila_local.py`
- Test: `tests/unit/test_fila.py`, `tests/unit/test_config.py`, `tests/integration/test_cli.py` (só o que quebrar pela assinatura de `criar_fila`)

**Interfaces:**
- Consumes: `TipoMensagem` (Task 1).
- Produces:
  - `FilaRoteada(rotas: Mapping[TipoMensagem, Fila], padrao: Fila)` implementa `Fila`: separa o lote por destino, junta os `ResultadoEnvio`.
  - `Config.fila_janelas_url: str | None = None`, `Config.fila_janelas_falhas_url: str | None = None`.
  - `criar_fila(config, fabrica_sessao, fontes, agora, cliente_sqs=None, coletora: Callable[[Session, Mensagem], str] | None = None) -> Fila`: com `sqs`, `FilaRoteada({"coletar_janela": FilaSQS(fila_janelas_url)}, FilaSQS(fila_url))` (exige as duas URLs, `ValueError` claro sem a de janelas); com `local`, `FilaLocal` que despacha `coletar_janela` para `coletora` ou a recusa com `"coletar_janela sem coletora local"` quando `coletora is None`.

- [ ] **Step 1:** testes: `test_fila_roteada_separa_por_tipo` (duas falsas recebem só os seus tipos; aceitas e recusadas somadas por `outbox_id`); `test_fila_roteada_recusa_de_um_destino_nao_afeta_o_outro`; `test_criar_fila_sqs_sem_url_de_janelas_falha`; `test_fila_local_sem_coletora_recusa_coletar_janela` (fica em `recusadas`, `falhas == 1`); `test_fila_local_com_coletora_despacha`.
- [ ] **Step 2:** rodar → FAIL. Implementar.
- [ ] **Step 3:** testes passam; `uv run mypy src` limpo.
- [ ] **Step 4:** commit `feat(comum): fila roteada por tipo e URLs da fila de janelas`.

### Task 5: Lambdas coletora e planejadora

Depende das Tasks 2, 3 e 4.

**Files:**
- Create: `src/radar/processador/coletora.py`, `src/radar/processador/lambda_coletora.py`, `src/radar/processador/lambda_planejador.py`
- Modify: `src/radar/processador/__init__.py`, `src/radar/processador/lambda_handler.py` (só se extrair o bootstrap de sessão/SSM/logs para um módulo comum aos três handlers)
- Test: `tests/integration/test_coletora.py`, `tests/unit/test_lambda_coletora.py`, `tests/unit/test_lambda_planejador.py`

**Interfaces:**
- Consumes: `coletar_fatia`, `ResultadoFatia`, `ORCAMENTO_LAMBDA_S`, `planejar_janelas`, `enfileirar_elegiveis` (`radar.coleta`); `FilaRoteada`, `criar_fila`, `publicar_outbox`, `de_corpo` (`radar.comum`); `carregar_criterios` (`radar.editais`).
- Produces:
  - `processar_janela(sessao: Session, mensagem: Mensagem, cliente: ClientePNCP, token: str, orcamento_s: float | None) -> DesfechoFatia` (lê `janela_id` e `lote` da carga; log por desfecho).
  - `planejar_e_enfileirar(sessao: Session, criterios: Criterios, hoje: date, recuo: int = 10) -> tuple[int, int]` (janelas novas, mensagens enfileiradas).
  - `lambda_coletora.handler(evento: dict[str, Any], contexto: Any) -> dict[str, list[dict[str, str]]]`: relay no começo; por registro, `processar_janela` com `token = contexto.aws_request_id` e `ORCAMENTO_LAMBDA_S`; relay no fim; `falha`, `ValueError` de corpo, exceção e erro do relay do fim entram em `batchItemFailures`; os outros desfechos não. Cliente PNCP criado com `tentativas=3`.
  - `lambda_planejador.handler(evento: dict[str, Any], contexto: Any) -> dict[str, int]`: `planejar_e_enfileirar` com `hoje` em `America/Sao_Paulo` e relay; devolve `{"janelas_novas": n, "enfileiradas": n, "publicadas": n}`; exceção propaga (vira `Errors` da Lambda).
  - Os três handlers continuam sem abrir conexão nem ler o SSM ao importar.

- [ ] **Step 1:** testes unitários com `processar_janela`, relay e sessão falsos: cada desfecho mapeado (confirma vs. volta); `test_relay_do_fim_falha_e_a_reentrega_sai_obsoleta` (relay do fim levanta → item volta; segunda chamada com o mesmo registro: relay do começo publica, `processar_janela` devolve `obsoleta`, lista vazia); corpo inválido volta; importar os módulos não chama `garantir_url_do_banco`.
- [ ] **Step 2:** teste de integração `test_cadeia_local_equivale_ao_laco` em `test_coletora.py`: `planejar_e_enfileirar` + `publicar_outbox` com `FilaLocal` cuja coletora usa orçamento mínimo (uma página por fatia) → mesmas contagens de `edital`, `coleta_janela` concluídas e `filtrar` idêntico ao de `coletar_pendentes` sobre o mesmo falso do PNCP.
- [ ] **Step 3:** rodar → FAIL. Implementar. `uv run lint-imports` passa.
- [ ] **Step 4:** testes passam.
- [ ] **Step 5:** commit `feat(processador): Lambdas coletora e planejadora`.

### Task 6: CLI

Depende das Tasks 3, 4 e 5.

**Files:**
- Modify: `src/radar/cli.py`
- Test: `tests/integration/test_cli.py`

**Interfaces:**
- Consumes: Tasks 3, 4 e 5.
- Produces:
  - `atualizar`, `processar`, `carga-inicial` imprimem por último `resultado: sem falhas` ou `resultado: N falhas` (o filtro do alarme `radar-coletor-nao-rodou` procura `"resultado:"`).
  - `radar planejar [--recuo 10]`: `planejar_e_enfileirar` + relay; local com a coletora sem orçamento ligada à `FilaLocal`.
  - `radar reprocessar-falhas --fila {editais,janelas}` (padrão `editais`); `janelas` usa `RADAR_FILA_JANELAS_URL`/`RADAR_FILA_JANELAS_FALHAS_URL`.
  - `radar auditar`: linha "janelas em andamento há mais de 6 h: N" seguida de uma linha por `JanelaParada`; falhas com `proxima_pagina/total_paginas`; N > 0 sai com 1.
  - `_criar_fila` passa as URLs de janelas.

- [ ] **Step 1:** testes: `test_atualizar_imprime_resultado` (com e sem falha; última linha começa com `resultado:`); `test_atualizar_so_retratos_nao_pede_o_pncp` (`--fontes sanepar,compesa` → zero chamadas ao falso do PNCP); `test_planejar_local_coleta_tudo`; `test_auditar_aponta_janela_parada`; `test_reprocessar_falhas_janelas_local_sai_com_2`; `test_reprocessar_falhas_fila_invalida_sai_com_2`.
- [ ] **Step 2:** rodar → FAIL. Implementar.
- [ ] **Step 3:** verificação geral (Global Constraints) inteira → PASS.
- [ ] **Step 4:** commit `feat(cli): planejar, auditoria das janelas e resultado de volta`.

### Task 7: Infra e deploy

Depende só dos nomes dos handlers. Paralela às Tasks 2-6.

**Files:**
- Create: `infra/prod/janelas.tf`
- Modify: `infra/prod/coletor.tf` (container: `command = ["atualizar", "--fontes", "sanepar,compesa"]`, env `RADAR_FILA_JANELAS_URL`; política `coletor_fila` cobre também `radar-janelas`; `aws_sns_topic_policy.alertas` aceita os alarmes novos), `infra/prod/github_oidc.tf` (`lambda:UpdateFunctionCode`/`lambda:GetFunction` nas duas Lambdas novas), `infra/prod/outputs.tf` (`url_fila_janelas`, `url_fila_janelas_falhas`), `.github/workflows/deploy.yml`
- Test: `tests/unit/test_infra_estatico.py`

**Interfaces:**
- Produces: filas `radar-janelas` (`visibility_timeout_seconds = 5400`, retenção 345.600 s, redrive `maxReceiveCount = 5`) e `radar-janelas-falhas` (1.209.600 s, `redrive_allow_policy` só da principal); Lambda `radar-coletora` (imagem `local.imagem_api`, `image_config.command = ["radar.processador.lambda_coletora.handler"]`, `timeout = 900`, `memory_size = 256`, env `RADAR_DB_NULLPOOL`, `RADAR_CONFIG_FILTRO`, `RADAR_FILA=sqs`, `RADAR_FILA_URL`, `RADAR_FILA_JANELAS_URL`, `lifecycle.ignore_changes = [image_uri]`); Lambda `radar-planejador` (handler do planejador, `timeout = 120`, `memory_size = 256`, mesmas envs); ESM em `radar-janelas` com `batch_size = 1`, `ReportBatchItemFailures`, `maximum_concurrency = 2`; log groups `/aws/lambda/radar-coletora` e `/aws/lambda/radar-planejador` com 14 dias; roles das duas com SSM (`api_banco`), logs e `sqs:SendMessage` nas duas filas, e a coletora com Receive/Delete/GetQueueAttributes em `radar-janelas`; `aws_scheduler_schedule` `radar-planejar-diario` (`cron(0 6 * * ? *)`, `America/Sao_Paulo`, `flexible_time_window OFF`, alvo a Lambda, role com `lambda:InvokeFunction` só nela, `aws:SourceAccount` na confiança); filtro de métrica `janela_concluida` e três `aws_cloudwatch_metric_alarm` (DLQ > 0, idade > 21.600 s, `radar-pncp-nao-concluiu`).

- [ ] **Step 1:** teste estático novo `test_janelas_tem_dlq_agenda_e_alarmes` (lê `janelas.tf`: `maxReceiveCount     = 5`, `visibility_timeout_seconds = 5400`, `timeout       = 900`, `batch_size              = 1`, `maximum_concurrency = 2`, `ReportBatchItemFailures`, `cron(0 6 * * ? *)`, três alarmes) e `test_alarmes_cabem_no_gratis` (soma de `aws_cloudwatch_metric_alarm` em `infra/prod/*.tf` ≤ 10); `test_coletor_so_retratos` (`coletor.tf` contém `"--fontes", "sanepar,compesa"`).
- [ ] **Step 2:** rodar → FAIL. Escrever o Terraform. `terraform fmt -check` e `terraform init -backend=false && terraform validate` em `infra/prod`.
- [ ] **Step 3:** `deploy.yml`: no passo "Atualizar a Lambda da API", depois da `radar-processador`, o mesmo par `update-function-code` + `wait function-updated-v2` para `radar-coletora` e `radar-planejador` com a imagem `radar-api`. Rodar actionlint (`docker run --rm -v "C:/Users/aschi/radar-wt-4b:/repo" --workdir /repo rhysd/actionlint:latest -color`).
- [ ] **Step 4:** commits separados: (a) `feat(infra): fila de janelas, Lambdas coletora e planejadora, agenda e alarmes`, sem trailer de contrato; (b) `ci(deploy): atualiza também a radar-coletora e a radar-planejador`, com `Contrato-Front: aprovado` no mesmo bloco dos trailers de atribuição.

### Task 8: Rodada real e fechamento (o coordenador, não um subagente)

- [ ] Conferir que a 4a está fechada (filas `radar-editais` no ar). `alembic upgrade head` no Neon (pela `DATABASE_URL_UNPOOLED` do `.env.local`, sem imprimi-la); `terraform apply` em `infra/prod` com o perfil `radar` e `-var tag_coletor=<sha>`.
- [ ] PR para `main`, checks verdes, merge, deploy automático verde (as quatro Lambdas com a imagem nova).
- [ ] Invocar `radar-planejador` à mão; acompanhar até `radar-janelas` esvaziar. Anotar: janelas, fatias por janela (máx. e média), duração total, duração p95 por invocação, `Max Memory Used` (se > 200 MB, subir a coletora para 512 MB e registrar o novo GB-s), GB-s do dia, páginas e editais. Rodar `RunTask` do coletor e conferir que só os retratos rodam e que a última linha é `resultado:`.
- [ ] `radar auditar --endpoint atualizacao` sai com 0.
- [ ] Demonstrações gravadas: (a) duplicata — enviar à mão uma cópia da mensagem em voo → log `janela_ocupada`; (b) Lambda morta — reduzir temporariamente o timeout da coletora para 60 s, ver a reentrega retomar do checkpoint, voltar a 900 s; (c) janela envenenada — mensagem para uma janela de modalidade inválida → 5 tentativas → DLQ → alarme; `radar reprocessar-falhas --fila janelas`; apagar da DLQ no fim.
- [ ] `docs/etapas/04b-coleta-por-janela.md` (o que foi feito, números da rodada real, o que a rodada ensinou, e 5 perguntas de entrevista com "Resposta:" vazio):
  1. Por que uma janela do PNCP não cabe numa Lambda, e como o orçamento de 540 s e o lease de 960 s saem do timeout de 900 s?
  2. Qual a diferença entre o lease (`travada_ate`) e o fencing token (`lote`, `travada_por`)? Que bug cada um impede?
  3. Por que a continuação passa pela outbox em vez de um `SendMessage` direto no fim da fatia?
  4. Uma mensagem duplicada chega enquanto a original ainda coleta. Por que é seguro confirmá-la sem fazer nada?
  5. O que `MaximumConcurrency = 2` protege, o que ele não protege, e por que o visibility timeout de 1,5 h também é cortesia?
- [ ] `docs/videos/04b-coleta-por-janela.md`, roteiro de ~8 min: o problema (4 h, 20 min por janela, teto de 15 min); desenho em uma tela (planejador → fila → coletora → outbox → fila de editais); a fatia no código (`coletar_fatia`: reivindicar, checkpoint, continuação); demo (a) duplicata `ocupada`; demo (b) Lambda morta retomando do checkpoint; demo (c) DLQ, alarme no e-mail e `reprocessar-falhas`; números da rodada (fatias, duração, GB-s); o que eu faria diferente.
- [ ] Atualizar `docs/checklist.md`.
