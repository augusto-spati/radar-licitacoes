# Etapa 4a: outbox + SQS — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a coleta só coleta e grava mensagens numa outbox na mesma transação; um relay publica no SQS; uma Lambda consome de forma idempotente (página do PNCP e retrato inteiro).

**Architecture:** outbox e fila ficam em `radar.comum` (sem domínio). O consumidor orquestra `coleta` e `editais`, que são independentes entre si (import-linter), então mora num pacote de porta de entrada novo, `radar.processador`, ao lado de `cli` e `api`. Localmente `FilaLocal` chama o consumidor na hora; na AWS `FilaSQS` publica e a Lambda `radar-processador` consome.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0, Alembic, psycopg 3, Typer, boto3 (nova dependência), pytest + testcontainers (Postgres 17), Terraform (provider AWS 5.x), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-26-etapa-04-filas-design.md` (ler inteiro antes de qualquer tarefa).

## Global Constraints

- Worktree `C:\Users\aschi\radar-wt-filas`, branch `etapa-04-filas`. Verificação geral: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run lint-imports && uv run pytest -q` (Docker ligado para os testes de integração).
- Recursos AWS só em us-east-2; nada fora do plano grátis; `tests/unit/test_infra_estatico.py` continua passando.
- Trailer `Contrato-Front: aprovado` **só** nos commits que mudam `.github/workflows/deploy.yml` e `.github/CODEOWNERS` (autorização registrada no spec). Nenhum outro arquivo guardado (lista em `frontend/scripts/contrato-guardado.mjs`) é tocado.
- Toda mensagem de commit termina com:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` e `Claude-Session: https://claude.ai/code/session_012v6r8uz1aiPHNQoDZ66AVf`.
- Valores fixos do spec: `maxReceiveCount = 5`; DLQ retém 14 dias; visibility timeout = 6 × 120 s = 720 s; Lambda timeout 120 s, 512 MB, fora de VPC; batch size 10; `MaximumConcurrency = 2`; lote do relay 10; retenção da outbox 7 dias; alarme de idade > 3600 s; alarme DLQ > 0; alarme `processamento_falhou` > 0 em 1 dia; retenção de brutos = 3 retratos processados mais novos.
- Variáveis: `RADAR_FILA` (`local` | `sqs`, padrão `local`), `RADAR_FILA_URL`, `RADAR_FILA_FALHAS_URL`.
- Logs estruturados com os nomes do spec: `retrato_obsoleto`, `processamento_falhou`.

## Review Focus

1. Duas mensagens `retrato_coletado` da mesma fonte chegando fora de ordem: a mais velha nunca reconcilia (teste na Task 4).
2. A mesma `pagina_coletada` entregue duas vezes em paralelo (duas Lambdas): o segundo não duplica nem falha de forma que vá para a DLQ (teste na Task 4: segunda chamada com a página já processada devolve `ok`).
3. Container do Fargate morre entre gravar a outbox e publicar: a próxima execução publica no começo (teste na Task 5: `atualizar` publica pendências antigas antes de coletar).
4. `SendMessageBatch` aceita parte do lote: só as aceitas ficam com `publicada_em`; as recusadas ganham `tentativas` e `erro` (teste na Task 2).
5. Retrato `concluido` ainda não processado nunca perde as páginas para `limpar_brutos` (teste na Task 3).

---

### Task 1: Migração 0005 e modelo da outbox

**Files:**
- Create: `migrations/versions/0005_outbox.py`, `src/radar/comum/outbox.py`
- Modify: `src/radar/coleta/modelos.py` (`ColetaRetrato.processado_em`), `src/radar/comum/__init__.py`
- Test: `tests/integration/test_outbox.py`, `tests/integration/test_esquema.py`

**Interfaces:**
- Produces: `radar.comum.Outbox` (ORM, tabela `outbox`); `TipoMensagem = Literal["pagina_coletada", "retrato_coletado"]`; `enfileirar(sessao: Session, tipo: TipoMensagem, carga: dict[str, int]) -> None` (só `sessao.add`, **sem commit**); `ColetaRetrato.processado_em: Mapped[datetime | None]`.

- [ ] **Step 1:** testes: `test_enfileirar_sem_commit_some_no_rollback` (enfileirar + `sessao.rollback()` → 0 linhas); `test_enfileirar_grava_tipo_e_carga` (`carga == {"pagina_id": 7}`, `publicada_em is None`, `tentativas == 0`); `test_tipo_invalido_viola_check` (insert com `tipo="x"` → `IntegrityError`). Em `test_esquema.py`, o `alembic check` existente continua sem diferenças.
- [ ] **Step 2:** rodar `uv run pytest tests/integration/test_outbox.py -v` → FAIL (import).
- [ ] **Step 3:** migração 0005 conforme o spec ("Dados"): tabela `outbox` com CHECK de `tipo`, índice parcial `ix_outbox_pendente (id) WHERE publicada_em IS NULL`, coluna `coleta_retrato.processado_em`. **Preenchimento:** retratos `concluido` existentes recebem `processado_em = concluido_em` (já foram processados pelo fluxo síncrono). Downgrade remove ambos.
- [ ] **Step 4:** testes passam; `alembic check` sem diferenças.
- [ ] **Step 5:** commit `feat(comum): outbox e processado_em do retrato (migração 0005)`.

### Task 2: Fila, SQS e relay

Depende da Task 1. Paralela às Tasks 3 e 6.

**Files:**
- Create: `src/radar/comum/fila.py`, `src/radar/comum/relay.py`
- Modify: `pyproject.toml` (`boto3>=1.35` em dependencies; `uv lock`), `src/radar/comum/config.py`, `src/radar/comum/__init__.py`
- Test: `tests/unit/test_fila.py`, `tests/integration/test_relay.py`

**Interfaces:**
- Consumes: `Outbox`, `TipoMensagem` (Task 1).
- Produces:
  - `@dataclass(frozen=True) Mensagem(outbox_id: int, tipo: TipoMensagem, carga: dict[str, int])`; `para_corpo(m: Mensagem) -> str` (JSON `{"tipo","carga","outbox_id"}`); `de_corpo(corpo: str) -> Mensagem` (lança `ValueError` em corpo inválido).
  - `@dataclass ResultadoEnvio(aceitas: set[int], recusadas: dict[int, str])` (chaves = `outbox_id`).
  - `class Fila(Protocol): def enviar_lote(self, mensagens: list[Mensagem]) -> ResultadoEnvio`.
  - `FilaSQS(url: str, cliente: Any | None = None)`: `SendMessageBatch` com `Id = str(outbox_id)`; `Failed` → `recusadas`. `mover_falhas(origem_arn: str, destino_arn: str) -> str` (chama `StartMessageMoveTask`, devolve o task handle).
  - `publicar_outbox(sessao: Session, fila: Fila, lote: int = 10, agora: Callable[[], datetime] | None = None) -> ResumoPublicacao(publicadas: int, recusadas: int)`: laço do spec ("Relay"), commit por lote, para quando não há pendentes **ou** quando um lote inteiro foi recusado (evita laço infinito).
  - `limpar_outbox(sessao: Session, agora: datetime, dias: int = 7) -> int` (com commit).
  - `Config.fila: Literal["local", "sqs"] = "local"`, `Config.fila_url: str | None = None`, `Config.fila_falhas_url: str | None = None`.

- [ ] **Step 1:** testes unitários: ida e volta `de_corpo(para_corpo(m)) == m`; `de_corpo("x")` → `ValueError`; `FilaSQS` com cliente falso que recusa o id `"2"` → `ResultadoEnvio(aceitas={1,3}, recusadas={2: "<mensagem do Failed>"})`; lote enviado com no máximo 10 entradas.
- [ ] **Step 2:** testes de integração: `test_publica_e_marca` (3 pendentes → fila falsa recebe 3, todas com `publicada_em`); `test_recusada_fica_pendente_com_erro` (`tentativas == 1`, `erro` preenchido, `publicada_em is None`); `test_skip_locked_duas_sessoes` (sessão A trava o lote com `FOR UPDATE SKIP LOCKED` sem commit; `publicar_outbox` na sessão B não publica as linhas travadas — use duas conexões do `engine`, não a fixture `sessao`); `test_envio_ok_commit_falha_reenvia` (fila falsa aceita, commit forçado a falhar → próxima chamada envia de novo os mesmos `outbox_id`); `test_limpar_outbox_apaga_so_publicadas_antigas`.
- [ ] **Step 3:** rodar → FAIL. Implementar. `import boto3` só dentro de `FilaSQS` quando `cliente is None`.
- [ ] **Step 4:** testes passam; `uv run mypy src` limpo.
- [ ] **Step 5:** commit `feat(comum): fila SQS e relay da outbox`.

### Task 3: Produtores gravam na outbox

Depende da Task 1. Paralela às Tasks 2 e 6.

**Files:**
- Modify: `src/radar/coleta/janelas.py` (`_gravar_pagina`), `src/radar/coleta/retratos.py` (`_executar`, `limpar_brutos`, `ResumoRetrato`), `src/radar/coleta/__init__.py`
- Test: `tests/integration/test_janelas.py`, `tests/integration/test_retratos.py`

**Interfaces:**
- Consumes: `enfileirar` (Task 1).
- Produces:
  - Página do PNCP gravada ⇒ uma linha `outbox(pagina_coletada, {"pagina_id": id})` no mesmo commit. Regravação da mesma página (upsert por `uq_pagina_bruta_janela_numero`) também enfileira (a página voltou a `processada_em = NULL`).
  - Retrato `concluido` ⇒ `outbox(retrato_coletado, {"retrato_id": id})` no commit que o conclui. As páginas do retrato **não** recebem mais `processada_em` na coleta. Retrato em falha não enfileira.
  - `ResumoRetrato` perde `normalizados` e `ids_listados` (o consumidor recalcula); `ids_do_retrato` sai de `coleta`.
  - `paginas_do_retrato(sessao: Session, retrato_id: int) -> list[PaginaColetada]` em `retratos.py` (ordem de `numero_pagina`; reconstrói `formato`, `texto` = `conteudo_texto` ou `json.dumps(conteudo)`, `url`), exportada por `radar.coleta`.
  - `limpar_brutos` conta só retratos com `processado_em IS NOT NULL` para os 3 mantidos e nunca apaga páginas de `concluido` com `processado_em IS NULL`.

- [ ] **Step 1:** testes: `test_pagina_gravada_enfileira_mensagem`; `test_retrato_concluido_enfileira_uma_mensagem` (e páginas com `processada_em is None`); `test_retrato_em_falha_nao_enfileira`; `test_paginas_do_retrato_reconstroi_json_e_html` (ida e volta com uma página `json` e uma `html`: `fonte.extrair` dá o mesmo resultado sobre as originais e as reconstruídas); `test_limpar_brutos_preserva_concluido_nao_processado` (5 retratos processados + 1 concluído sem `processado_em` mais velho que todos → as páginas dele ficam).
- [ ] **Step 2:** rodar → FAIL. Implementar. Ajustar testes existentes que esperavam `normalizados` ou páginas processadas.
- [ ] **Step 3:** `uv run pytest tests/integration/test_janelas.py tests/integration/test_retratos.py -v` → PASS. (O `cli` fica temporariamente quebrado: é consertado na Task 5. Marque com `pytest.mark.xfail(reason="Task 5")` só os testes de `test_cli.py` que dependem do registro síncrono dos retratos.)
- [ ] **Step 4:** commit `feat(coleta): página e retrato enfileiram na outbox`.

### Task 4: Consumidor (`radar.processador`)

Depende das Tasks 1 e 3.

**Files:**
- Create: `src/radar/processador/__init__.py`, `src/radar/processador/consumidor.py`, `src/radar/processador/fontes.py`, `src/radar/processador/lambda_handler.py`
- Modify: `pyproject.toml` (import-linter: `radar.processador` entra em `forbidden_modules` do contrato "Nenhum módulo importa a porta de entrada" e em `source_modules` do contrato "api e cli importam só a raiz"), `src/radar/cli.py` (passa a usar `fontes_retrato()` de `radar.processador`)
- Test: `tests/integration/test_consumidor.py`, `tests/unit/test_lambda_consumidor.py`

**Interfaces:**
- Consumes: `Mensagem`, `de_corpo` (Task 2); `paginas_do_retrato`, `trava_fonte`, `marcar_retrato_falha`, `limpar_brutos`, `marcar_processada` (coleta); `registrar`, `registrar_normalizados`, `reconciliar_retrato` (editais, pela raiz do pacote — exportar o que faltar em `radar/editais/__init__.py`).
- Produces:
  - `fontes_retrato() -> dict[str, FonteRetrato]` (movido de `cli._fontes_retrato`).
  - `Desfecho = Literal["ok", "falha_definitiva"]`; `class MensagemAdiada(Exception)` (trava da fonte ocupada: a mensagem deve voltar).
  - `processar_mensagem(sessao: Session, mensagem: Mensagem, fontes: Mapping[str, FonteRetrato], agora: datetime) -> Desfecho`. Regras exatamente as do spec ("Consumidor", "Falhas que não chegam à DLQ", "Retenção de brutos"): página já processada → `ok`; retrato já processado → `ok`; retrato mais velho que um já processado da mesma fonte → marca `processado_em`, log `retrato_obsoleto`, `ok`; páginas ausentes ou fração de inválidos acima de `FRACAO_INVALIDOS` → `marcar_retrato_falha`, log `processamento_falhou`, `falha_definitiva`; trava ocupada → `MensagemAdiada`. A gravação + reconciliação + `processado_em` + páginas marcadas é uma transação; `limpar_brutos` depois. Exceção inesperada propaga (vira nova tentativa).
  - `handler(evento: dict[str, Any], contexto: Any) -> dict[str, list[dict[str, str]]]` em `lambda_handler.py`: uma sessão por registro; devolve `{"batchItemFailures": [{"itemIdentifier": messageId}, ...]}` para registros que levantaram `MensagemAdiada`, `ValueError` de corpo ou qualquer exceção; `falha_definitiva` **não** entra na lista. URL do banco lida do SSM como em `radar/api/lambda_handler.py` (reutilize a função, movendo-a para `radar.comum` se precisar).

- [ ] **Step 1:** testes de integração (usar `FonteSanepar` com páginas de `tests/dados/fontes`): `test_pagina_processa_e_repetida_e_ok` (2ª chamada devolve `ok` sem mudar `edital`); `test_retrato_grava_e_reconcilia` (mesmo resultado de editais que o fluxo síncrono antigo para o mesmo conjunto de páginas: contagens de inseridos e encerrados); `test_retrato_repetido_e_ok`; `test_retrato_obsoleto_nao_reconcilia` (processa o retrato 2, depois o 1 → editais iguais aos do 2, retrato 1 com `processado_em`); `test_retrato_muitos_invalidos_vira_falha_definitiva` (nada gravado, `status == "falha"`); `test_retrato_sem_paginas_e_falha_definitiva`; `test_trava_ocupada_adia` (outra conexão segura `trava_fonte`).
- [ ] **Step 2:** testes unitários do handler com `processar_mensagem` falso: lista só os ids que falharam; `falha_definitiva` não entra; corpo inválido entra.
- [ ] **Step 3:** rodar → FAIL. Implementar. `uv run lint-imports` passa.
- [ ] **Step 4:** testes passam.
- [ ] **Step 5:** commit `feat(processador): consumidor idempotente das mensagens da outbox`.

### Task 5: CLI ligada à fila

Depende das Tasks 2 e 4.

**Files:**
- Create: `src/radar/processador/fila_local.py`
- Modify: `src/radar/cli.py`, `src/radar/processador/__init__.py`
- Test: `tests/integration/test_cli.py`

**Interfaces:**
- Consumes: Tasks 2 e 4.
- Produces:
  - `FilaLocal(fabrica_sessao: Callable[[], Session], fontes: Mapping[str, FonteRetrato], agora: Callable[[], datetime])` implementa `Fila`: processa cada mensagem na hora numa sessão própria; `ok` e `falha_definitiva` → aceita; exceção (inclusive `MensagemAdiada`) → recusada com o `repr` do erro. Atributo `falhas: int` (definitivas + recusadas) para o código de saída.
  - `criar_fila(config: Config, ...) -> Fila` escolhe `FilaLocal` ou `FilaSQS` por `config.fila`.
  - `atualizar`, `carga-inicial`, `processar`: `publicar_outbox` **no começo** e **no fim**; retratos só coletam (o bloco de registro/reconciliação de `_rodar_retrato` sai, ele vive no consumidor). Código de saída ≠ 0 se houver falha de coleta, recusas do relay ou `FilaLocal.falhas > 0`.
  - Comandos novos: `radar publicar` (relay + `limpar_outbox`); `radar reprocessar-falhas` (só com `RADAR_FILA=sqs`: `FilaSQS.mover_falhas` da DLQ para a principal, ARNs derivados das URLs via `GetQueueAttributes`; imprime o handle; com `local` sai com código 2 e mensagem clara).
  - `radar auditar` mostra: outbox pendente (quantas e a mais velha), páginas do PNCP publicadas e não processadas há mais de 1 h, retratos `concluido` sem `processado_em`; qualquer uma > 0 faz sair com 1.

- [ ] **Step 1:** testes: tirar os `xfail` da Task 3; `test_atualizar_local_equivale_ao_sincrono` (mesmas contagens de `edital` e `filtrar` idêntico ao do fluxo antigo, com os fakes de `tests/integration/fakes.py`); `test_atualizar_publica_pendencias_antigas_no_comeco` (linha de outbox de "ontem" é processada mesmo sem coleta nova); `test_falha_definitiva_local_sai_com_1`; `test_auditar_aponta_outbox_pendente`; `test_reprocessar_falhas_local_sai_com_2`.
- [ ] **Step 2:** rodar → FAIL. Implementar.
- [ ] **Step 3:** verificação geral (Global Constraints) inteira → PASS.
- [ ] **Step 4:** commit `feat(cli): coleta → outbox → fila, publicar e reprocessar-falhas`.

### Task 6: Infra e deploy

Depende só do nome do handler (`radar.processador.lambda_handler.handler`). Paralela às Tasks 2–5.

**Files:**
- Create: `infra/prod/fila.tf`
- Modify: `infra/prod/coletor.tf` (env `RADAR_FILA=sqs`, `RADAR_FILA_URL`; task role com `sqs:SendMessage` na fila principal), `infra/prod/github_oidc.tf` (deploy: `lambda:UpdateFunctionCode` e `lambda:GetFunction` em `radar-processador`), `infra/prod/outputs.tf`, `.github/workflows/deploy.yml`, `.github/CODEOWNERS`, `docker/api-lambda.Dockerfile` (só se o pacote `radar.processador` não entrar na imagem)
- Test: `tests/unit/test_infra_estatico.py`

**Interfaces:**
- Produces: filas `radar-editais` e `radar-editais-falhas`; Lambda `radar-processador` (imagem `radar-api`, `image_config.command = ["radar.processador.lambda_handler.handler"]`, env `RADAR_DB_NULLPOOL=true`, `RADAR_CONFIG_FILTRO` igual ao da API); event source mapping com `batch_size = 10`, `function_response_types = ["ReportBatchItemFailures"]`, `scaling_config { maximum_concurrency = 2 }`; log group `/aws/lambda/radar-processador` com retenção igual à da API; filtro de métrica `processamento_falhou` e três alarmes (DLQ > 0, idade > 3600 s, `processamento_falhou` > 0 em 86400 s) no tópico `radar-alertas`; role do processador com Receive/Delete/GetQueueAttributes na fila, leitura do parâmetro SSM e logs. Outputs `url_fila`, `url_fila_falhas`.

- [ ] **Step 1:** teste estático novo: `test_fila_tem_dlq_e_alarmes` (lê `fila.tf`: `maxReceiveCount` 5, `visibility_timeout_seconds = 720`, `ReportBatchItemFailures`, `maximum_concurrency = 2`, três `aws_cloudwatch_metric_alarm`).
- [ ] **Step 2:** rodar → FAIL. Escrever o Terraform. `terraform fmt -check` e `terraform validate` em `infra/prod` (sem credenciais: `terraform init -backend=false`).
- [ ] **Step 3:** `deploy.yml`: depois do `update-function-code` da `radar-api`, o mesmo para `radar-processador` com a mesma imagem, e `aws lambda wait function-updated-v2`. `CODEOWNERS`: o comentário passa a citar o job `guarda-base`. Rodar actionlint (`docker run --rm -v "C:/Users/aschi/radar-wt-filas:/repo" --workdir /repo rhysd/actionlint:latest -color`).
- [ ] **Step 4:** commits separados: (a) `feat(infra): fila SQS, DLQ e Lambda processadora` sem trailer de contrato; (b) `ci(deploy): atualiza também a radar-processador` com `Contrato-Front: aprovado` no mesmo bloco dos trailers de atribuição.

### Task 7: Rodada real e fechamento (o coordenador, não um subagente)

- [ ] `terraform apply` em `infra/prod` (credenciais do perfil `radar`); `alembic upgrade head` no Neon (pela URL `DATABASE_URL_UNPOOLED` do `.env.local`, sem imprimi-la).
- [ ] PR para `main`, checks verdes, merge, deploy automático verde.
- [ ] `RunTask` manual do coletor; conferir no log `publicadas`, na fila a idade caindo a 0, e no Neon `outbox` publicada e retratos com `processado_em`.
- [ ] Demonstração da DLQ: mensagem com corpo inválido enviada à mão → 5 tentativas → DLQ → alarme; `radar reprocessar-falhas` devolve (e ela cai de novo, por ser inválida; apagar da DLQ no fim).
- [ ] `docs/etapas/04a-filas.md` (números, lições, 5 perguntas de entrevista com "Resposta:" vazio) e `docs/videos/04a-filas.md`; atualizar `docs/checklist.md`.
