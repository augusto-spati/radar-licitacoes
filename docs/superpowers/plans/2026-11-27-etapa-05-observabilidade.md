# Etapa 5: observabilidade — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** traces OpenTelemetry das unidades de trabalho (janela, retrato, lote do relay, mensagem) ligados por span link através da outbox e do SQS, 6 métricas customizadas novas para 5 SLIs, 2 alarmes, 1 dashboard, sem sidecar e dentro do plano grátis.

**Architecture:** tudo que é transversal fica em `radar.comum` (`telemetria.py`, `metricas.py`), sem domínio; as funções de coleta (`coleta.janelas`, `coleta.pncp`, `coleta.retratos`) abrem os spans e registram as métricas, então a instrumentação vale para o Fargate e para a Lambda coletora da 4b. O `traceparent` nasce na linha da outbox (mesma transação da coleta), viaja no atributo SQS e vira link no span do consumidor. Fargate emite métricas por PutMetricData agregado; Lambdas por EMF.

**Tech Stack:** Python 3.13, `opentelemetry-api`/`opentelemetry-sdk`/`opentelemetry-exporter-otlp-proto-http` (novas), botocore (SigV4), SQLAlchemy 2.0, Alembic, Typer, pytest + testcontainers (Postgres 17), Terraform (provider AWS 5.x).

**Spec:** `docs/superpowers/specs/2026-11-27-etapa-05-observabilidade-design.md` (ler inteiro antes de qualquer tarefa).

## Global Constraints

- Worktree `C:\Users\aschi\radar-wt-obs`, branch `etapa-obs`, **rebaseado sobre a main com a etapa 4b** antes da Task 1. Verificação geral: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run lint-imports && uv run pytest -q` (Docker ligado).
- Recursos AWS só em us-east-2; nada fora do plano grátis; `tests/unit/test_infra_estatico.py` continua passando.
- **Nenhum arquivo em `.github/workflows/*` nem outro arquivo guardado** (`frontend/scripts/contrato-guardado.mjs`) é tocado; nenhum commit usa `Contrato-Front: aprovado`.
- Toda mensagem de commit termina com:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` e `Claude-Session: https://claude.ai/code/session_012v6r8uz1aiPHNQoDZ66AVf`.
- Testes sem rede: nada chama X-Ray, CloudWatch ou SQS de verdade; nunca `opentelemetry.trace.set_tracer_provider`.
- Variáveis: `RADAR_TELEMETRIA` (`desligada` | `xray`, padrão `desligada`), `RADAR_METRICAS` (`desligada` | `emf` | `cloudwatch`, padrão `desligada`), `RADAR_TRACES_FRACAO` (float 0–1, padrão `1.0` no código; `0.25` no Terraform).
- Valores fixos do spec: namespace `Radar`; endpoint `https://xray.<região>.amazonaws.com/v1/traces`, serviço SigV4 `xray`; exportador com timeout 10 s e gzip; `descarregar` limitado a 2 s (Lambda) e encerramento a 5 s (CLI); frescor alarma > 36 h (período 21600 s, 6 de 6, ausente = violação); taxa de erro alarma `Average` > 0,10 em 86400 s (ausente = ok); `aws/spans` retido 7 dias; fonte sem dado = 9999 h.
- Catálogo de métricas exatamente: `FrescorHoras` (sem dimensão, unidade `None`), `PaginaComErro` (sem dimensão, `None`), `LatenciaRequisicao` (dimensão `fonte` ∈ {`pncp`, `sanepar`, `compesa`}, `Seconds`), `ExecucaoOk` (sem dimensão, `None`).
- Nomes de span e atributos: os da tabela "Spans" do spec. Logs novos: `requisicao_http`, `frescor_medido`, `metrica_registrada`, `telemetria_erro`, `metricas_erro`.
- Telemetria nunca levanta exceção para quem chama nem muda código de saída.

## Review Focus

1. Exportador ou PutMetricData lento/quebrado no meio da coleta: `atualizar` termina no mesmo tempo (± o limite de 5 s) e com o mesmo código de saída (teste na Task 6: exportador que levanta e cliente CloudWatch que levanta).
2. Mensagem SQS sem atributo `traceparent` (em voo durante o deploy) ou com `traceparent` malformado: processa normalmente, sem link, sem ir para a DLQ (teste na Task 5).
3. Retrato `concluido` cuja mensagem ainda não foi processada no momento da medição de frescor: não conta como fresco (teste na Task 4).
4. Contexto vazando para as fontes: nenhuma requisição ao PNCP/Sanepar/Compesa leva `traceparent` (teste na Task 4 com `httpx.MockTransport` conferindo cabeçalhos).
5. Lambda congelada antes de exportar: `descarregar` roda no `finally` do handler mesmo quando o processamento levanta (teste na Task 5).

---

### Task 1: Núcleo de telemetria (`radar.comum.telemetria`)

Sem dependências. Paralela às Tasks 2 e 7.

**Files:**
- Create: `src/radar/comum/telemetria.py`, `tests/unit/test_telemetria.py`, `tests/telemetria_memoria.py` (fixture compartilhada, importada pelo `tests/conftest.py`)
- Modify: `pyproject.toml` (`opentelemetry-api>=1.27`, `opentelemetry-sdk>=1.27`, `opentelemetry-exporter-otlp-proto-http>=1.27`; `uv lock`), `src/radar/comum/config.py`, `src/radar/comum/logs.py`, `src/radar/comum/__init__.py`, `tests/conftest.py`, `tests/unit/test_logs.py`

**Interfaces:**
- Produces:
  - `Config.telemetria: Literal["desligada", "xray"] = "desligada"`, `Config.metricas: Literal["desligada", "emf", "cloudwatch"] = "desligada"`, `Config.traces_fracao: float = 1.0` (validado 0–1).
  - `tracer() -> opentelemetry.trace.Tracer` (no-op sem configuração).
  - `configurar_telemetria(config: Config, servico: str, exportador: SpanExporter | None = None) -> None` (`exportador=None` e `xray` → OTLP assinado; `desligada` → no-op; idempotente).
  - `usar_provedor(provedor: TracerProvider | None) -> None` (só para testes e para `configurar_telemetria`).
  - `class AmostradorDoRadar(Sampler)` com `__init__(self, fracao: float)`: regras 1–3 do spec.
  - `class AssinaturaSigV4(requests.auth.AuthBase)` com `__init__(self, regiao: str, servico: str = "xray", credenciais: Callable[[], botocore.credentials.Credentials] | None = None)`.
  - `traceparent_atual() -> str | None`; `link_de(traceparent: str | None) -> Link | None` (malformado → `None`); `atributos_sqs(traceparent: str | None) -> dict[str, dict[str, str]]` (vazio se `None`); `traceparent_do_registro(registro: Mapping[str, Any]) -> str | None`.
  - `descarregar(timeout_s: float = 2.0) -> None`; `encerrar(timeout_s: float = 5.0) -> None` (ambos engolem e logam `telemetria_erro`).
  - `com_telemetria(servico: str) -> Callable[[Handler], Handler]` (decorador de handler Lambda: configura na 1ª chamada, chama `descarregar()` e `metricas.descarregar()` no `finally`; o import de `metricas` fica dentro da função para a Task 2 ser independente).
  - Fixture `spans` (em `tests/telemetria_memoria.py`): `TracerProvider(sampler=AmostradorDoRadar(1.0))` + `SimpleSpanProcessor(InMemorySpanExporter())`, ligado com `usar_provedor`, desligado no teardown; devolve o exporter.
  - `FormatadorJson` acrescenta `trace_id`, `span_id`, `xray_trace_id` quando o span corrente é válido.

- [ ] **Step 0:** medir a linha de base **antes** de mexer em dependências: `docker build -f docker/api-lambda.Dockerfile -t radar-api:antes .` e `docker build -f docker/coletor.Dockerfile -t radar-coletor:antes .`; anotar `docker image inspect -f "{{.Size}}"` de cada uma e `docker run --rm --entrypoint python radar-api:antes -X importtime -c "import radar.processador.lambda_handler" 2>&1 | tail -1` em `docs/etapas/05-medicoes.md` (arquivo de rascunho que a Task 8 incorpora à nota).
- [ ] **Step 1:** testes unitários: `test_sem_configuracao_e_noop` (span de `tracer()` não grava nada e `traceparent_atual()` é `None`); `test_amostrador_segue_pai`; `test_amostrador_link_amostrado_forca` (fração 0.0, link com flag `01` → `RECORD_AND_SAMPLE`); `test_amostrador_fracao_zero_sem_link_descarta`; `test_traceparent_ida_e_volta` (dentro de span: `link_de(traceparent_atual()).context.span_id == span.get_span_context().span_id`); `test_link_de_malformado_e_none` (`"x"`, `""`, `None`); `test_atributos_sqs` (`{"traceparent": {"DataType": "String", "StringValue": tp}}`; `None` → `{}`); `test_traceparent_do_registro` (evento com `messageAttributes.traceparent.stringValue`; sem atributo → `None`); `test_assinatura_sigv4_assina_sem_enviar` (credenciais estáticas, `requests.Request("POST", "https://xray.us-east-2.amazonaws.com/v1/traces", data=b"x").prepare()` passa pela auth → `Authorization` começa com `AWS4-HMAC-SHA256` e contém `/us-east-2/xray/aws4_request`); `test_descarregar_engole_erro` (exportador cujo `force_flush` levanta → nada propaga, log `telemetria_erro`); `test_com_telemetria_descarrega_no_finally` (handler que levanta → `descarregar` chamado uma vez, exceção propaga). Em `test_logs.py`: `test_log_dentro_de_span_tem_trace_id` (32 hex, 16 hex, `xray_trace_id` casa `^1-[0-9a-f]{8}-[0-9a-f]{24}$`) e `test_log_fora_de_span_sem_trace_id`.
- [ ] **Step 2:** rodar `uv run pytest tests/unit/test_telemetria.py tests/unit/test_logs.py -v` → FAIL (import).
- [ ] **Step 3:** implementar. O exportador real: `OTLPSpanExporter(endpoint=..., session=<requests.Session com AssinaturaSigV4>, compression=Compression.Gzip, timeout=10)`; `BatchSpanProcessor`. Recurso com `service.name`, `service.version`, `deployment.environment="prod"`, `cloud.region`. Imports do SDK/exportador só dentro de `configurar_telemetria` (o handler não paga o import ao ser importado).
- [ ] **Step 4:** testes passam; `uv run mypy src` e `uv run lint-imports` limpos.
- [ ] **Step 5:** commit `feat(comum): telemetria OpenTelemetry com amostragem e exportador SigV4`.

### Task 2: Métricas (`radar.comum.metricas`)

Sem dependências. Paralela às Tasks 1 e 7.

**Files:**
- Create: `src/radar/comum/metricas.py`, `tests/unit/test_metricas.py`
- Modify: `src/radar/comum/__init__.py`, `tests/telemetria_memoria.py` (fixture `metricas`; se a Task 1 ainda não criou o arquivo, criar só com esta fixture)

**Interfaces:**
- Consumes: `Config.metricas` (Task 1; se a Task 1 não chegou, adicionar o campo aqui com o mesmo nome e tipo).
- Produces:
  - `@dataclass(frozen=True) DefinicaoMetrica(nome: str, unidade: str, dimensoes: frozenset[str], valores_dimensao: Mapping[str, frozenset[str]])`; `CATALOGO: Mapping[str, DefinicaoMetrica]` com as 4 do Global Constraints; `NAMESPACE = "Radar"`.
  - `class Emissor(Protocol)`: `registrar(self, nome: str, valor: float, dimensoes: Mapping[str, str]) -> None`; `descarregar(self) -> None`.
  - `EmissorEMF` (loga em `radar.metricas`, nível INFO, evento `metrica_registrada`, `extra` com `_aws` + valor + dimensões + propriedades), `EmissorCloudWatch(cliente: Any | None = None, regiao: str | None = None)` (agrega por (nome, dimensões) em `Values`/`Counts`; `descarregar` envia `PutMetricData` em lotes de até 1000 datums e esvazia; erro → log `metricas_erro`), `EmissorEmMemoria` (lista `registros`).
  - `configurar_metricas(config: Config, emissor: Emissor | None = None) -> None`; `registrar(nome: str, valor: float, dimensoes: Mapping[str, str] | None = None, propriedades: Mapping[str, str | float] | None = None) -> None` (valida contra o catálogo **antes** de checar se está desligado; sempre loga `metrica_registrada` com as propriedades, exceto no modo EMF, em que a linha EMF já é esse log); `descarregar() -> None`.
  - Fixture `metricas`: liga `EmissorEmMemoria` e devolve-o.

- [ ] **Step 1:** testes: `test_metrica_fora_do_catalogo_levanta` (`ValueError` mesmo com `desligada`); `test_dimensao_invalida_levanta` (`LatenciaRequisicao` com `fonte="x"` e `FrescorHoras` com `fonte` como dimensão); `test_emf_valido` (capturar a linha: `_aws.CloudWatchMetrics[0].Namespace == "Radar"`, `Dimensions == [["fonte"]]` para latência e `[[]]` para `FrescorHoras`, `Metrics[0] == {"Name": ..., "Unit": ...}`, valor no topo, `Timestamp` inteiro em ms); `test_emf_propriedade_nao_vira_dimensao` (`FrescorHoras` com `propriedades={"fonte": "pncp"}` → `fonte` no topo, `Dimensions == [[]]`); `test_cloudwatch_agrega_valores_repetidos` (registrar 1.0, 1.0, 2.0 → um datum `Values=[1.0, 2.0]`, `Counts=[2, 1]`); `test_cloudwatch_descarregar_esvazia` (2ª chamada não envia nada); `test_cloudwatch_erro_vira_log` (cliente falso levanta → sem exceção, log `metricas_erro`); `test_desligada_nao_emite`.
- [ ] **Step 2:** rodar `uv run pytest tests/unit/test_metricas.py -v` → FAIL.
- [ ] **Step 3:** implementar. `import boto3` só dentro de `EmissorCloudWatch` quando `cliente is None`; região de `AWS_REGION`/`AWS_DEFAULT_REGION`.
- [ ] **Step 4:** testes passam; mypy limpo.
- [ ] **Step 5:** commit `feat(comum): catálogo de métricas com emissores EMF e PutMetricData`.

### Task 3: Outbox leva o contexto (migração 0006, relay, FilaSQS)

Depende da Task 1. Paralela à Task 4.

**Files:**
- Create: `migrations/versions/0006_outbox_traceparent.py`
- Modify: `src/radar/comum/outbox.py` (`Outbox.traceparent`, `enfileirar`), `src/radar/comum/fila.py` (`Mensagem.traceparent`, `FilaSQS.enviar_lote`), `src/radar/comum/relay.py` (span `relay.lote`, copia `traceparent`)
- Test: `tests/integration/test_outbox.py`, `tests/integration/test_relay.py`, `tests/unit/test_fila.py`, `tests/integration/test_esquema.py`

**Interfaces:**
- Consumes: `tracer`, `traceparent_atual`, `link_de`, `atributos_sqs` (Task 1).
- Produces:
  - `Outbox.traceparent: Mapped[str | None]` (`Text`, nulo); `enfileirar` grava `traceparent_atual()` (assinatura inalterada).
  - `Mensagem(outbox_id, tipo, carga, traceparent: str | None = None)`; `para_corpo`/`de_corpo` inalterados (o corpo não leva `traceparent`).
  - `FilaSQS.enviar_lote`: entrada com `MessageAttributes=atributos_sqs(m.traceparent)` quando não vazio.
  - `publicar_outbox`: um span `relay.lote` (PRODUCER) por lote, com atributos `messaging.system="aws_sqs"`, `messaging.batch.message_count`, `radar.recusadas` e links para os `traceparent` não nulos das linhas.

- [ ] **Step 1:** testes: `test_enfileirar_grava_traceparent_do_span` (fixture `spans`; dentro de `tracer().start_as_current_span("x")` → linha com `traceparent` cujo span id é o do span); `test_enfileirar_sem_span_grava_nulo`; `test_relay_copia_traceparent_para_mensagem` (fila falsa recebe `Mensagem.traceparent` igual ao da linha); `test_relay_lote_tem_links` (span `relay.lote` com `len(links) == 2` para 2 linhas com contexto e 1 sem); `test_fila_sqs_manda_atributo` (cliente falso: entrada com `MessageAttributes["traceparent"]["StringValue"] == tp`; mensagem sem `traceparent` → entrada sem `MessageAttributes`); `test_corpo_nao_muda` (`para_corpo(Mensagem(1, "pagina_coletada", {"pagina_id": 7}, "00-...-01"))` igual ao corpo sem `traceparent`). `alembic check` sem diferenças.
- [ ] **Step 2:** rodar → FAIL.
- [ ] **Step 3:** migração 0006: `ADD COLUMN traceparent text NULL` (sem preenchimento); downgrade remove. Implementar o resto.
- [ ] **Step 4:** `uv run pytest tests/integration/test_outbox.py tests/integration/test_relay.py tests/unit/test_fila.py tests/integration/test_esquema.py -v` → PASS.
- [ ] **Step 5:** commit `feat(comum): traceparent nasce na outbox e viaja no atributo SQS`.

### Task 4: Instrumentação da coleta e frescor

Depende das Tasks 1 e 2. Paralela à Task 3.

**Files:**
- Create: `src/radar/coleta/frescor.py`
- Modify: `src/radar/coleta/pncp.py` (`buscar`, `_requisitar`), `src/radar/coleta/retratos.py` (`ClienteEducado`, `_executar`), `src/radar/coleta/janelas.py` (`_coletar_janela`, e o ponto de entrada da 4b que continua janela a partir do checkpoint, se for outra função), `src/radar/coleta/__init__.py`, `src/radar/cli.py` (só `_criar_cliente_retrato` passa `fonte=fonte.nome`)
- Test: `tests/unit/test_pncp.py`, `tests/integration/test_janelas.py`, `tests/integration/test_retratos.py`, `tests/integration/test_frescor.py`

**Interfaces:**
- Consumes: `tracer` (Task 1); `registrar`, `descarregar` de `radar.comum.metricas` (Task 2).
- Produces:
  - `ClienteEducado(..., fonte: str = "desconhecida")` (keyword; `fonte` vai para spans e para a dimensão da latência só se estiver no catálogo; fora dele, não registra latência e loga).
  - Por tentativa HTTP (PNCP e retratos): span `GET` (CLIENT) com os atributos do spec; log `requisicao_http` (`fonte`, `status`, `duracao_s`, `tentativa`, `host`); `LatenciaRequisicao{fonte}` com a duração medida em volta de `http.get` (sem a pausa de cortesia e sem a espera do tenacity).
  - Por página: `PaginaComErro` 1 quando `buscar` termina em `ErroPNCP` ou `ClienteEducado.get` em `ErroFonte`; 0 quando dá certo.
  - Spans `pncp.buscar`, `coleta.janela` (raiz: `start_as_current_span(..., context=Context())`; na continuação da 4b, atributo `radar.janela.pagina_inicial` e link para o `traceparent` da mensagem da janela, se houver), `coleta.retrato` (raiz), com `radar.desfecho` e status `ERROR` + `record_exception` nas falhas. `metricas.descarregar()` no fim de cada janela e de cada retrato (num `finally`).
  - `frescor_por_fonte(sessao: Session, agora: datetime, fontes: Iterable[str]) -> dict[str, float]` (horas; definições exatas do spec; fonte sem dado → `9999.0`), exportada por `radar.coleta`.

- [ ] **Step 1:** testes: `test_tentativas_viram_spans_get` (PNCP falso com `MockTransport`: 503 e depois 200 → 2 spans `GET`, filhos de 1 `pncp.buscar` com `radar.tentativas == 2`, 2 valores de `LatenciaRequisicao{fonte="pncp"}` e um `PaginaComErro == 0`); `test_erro_definitivo_conta_pagina_com_erro` (4×503 → `PaginaComErro == 1`, span `pncp.buscar` com status ERROR); `test_nenhum_traceparent_nas_fontes` (o `MockTransport` registra cabeçalhos de todas as requisições do PNCP e de `ClienteEducado`, dentro de um span amostrado → nenhum `traceparent`/`tracestate`); `test_latencia_nao_inclui_pausa` (`ClienteEducado` com `dormir` falso que avança um relógio falso em 3 s → latência registrada < 1 s); `test_janela_e_raiz_e_pagina_herda` (coleta de uma janela de 2 páginas: span `coleta.janela` sem pai e as 2 linhas da outbox com `traceparent` do mesmo trace); `test_janela_com_falha_marca_erro`; `test_retrato_e_raiz_com_desfecho` (Sanepar falsa: `coleta.retrato` com `radar.fonte="sanepar"` e `radar.desfecho="concluido"`); `test_metricas_descarregadas_por_janela` (`EmissorEmMemoria` conta 1 `descarregar` por janela). Em `test_frescor.py`: `test_frescor_retrato_processado` (retrato iniciado há 10 h com `processado_em` → 10.0); `test_retrato_concluido_nao_processado_nao_conta` (mais novo sem `processado_em`, anterior processado há 30 h → 30.0); `test_frescor_pncp_ignora_janela_com_pagina_pendente`; `test_fonte_sem_dado_e_9999`.
- [ ] **Step 2:** rodar → FAIL.
- [ ] **Step 3:** implementar. Medir com `time.monotonic()`. `ClientePNCP` usa `fonte="pncp"`.
- [ ] **Step 4:** `uv run pytest tests/unit/test_pncp.py tests/integration/test_janelas.py tests/integration/test_retratos.py tests/integration/test_frescor.py -v` → PASS; `uv run lint-imports` limpo.
- [ ] **Step 5:** commit `feat(coleta): spans, latência e páginas com erro nas funções de coleta; frescor por fonte`.

### Task 5: Consumidor com link e handlers Lambda

Depende das Tasks 1, 2 e 3.

**Files:**
- Modify: `src/radar/processador/lambda_handler.py`, `src/radar/processador/consumidor.py`, `src/radar/processador/fila_local.py`, handlers Lambda criados pela 4b (só para aplicar `com_telemetria`)
- Test: `tests/unit/test_lambda_consumidor.py`, `tests/integration/test_consumidor.py`, `tests/integration/test_cli.py` (só o caso da fila local)

**Interfaces:**
- Consumes: `tracer`, `link_de`, `traceparent_do_registro`, `com_telemetria` (Task 1); `Mensagem.traceparent` (Task 3).
- Produces:
  - Função `processar_com_span(sessao: Session, mensagem: Mensagem, fontes: Mapping[str, FonteRetrato], agora: datetime, message_id: str | None = None) -> Desfecho` em `consumidor.py`: span raiz `processar mensagem` (CONSUMER, `context=Context()`, `links=[link_de(mensagem.traceparent)]` se não `None`), atributos `messaging.system`, `messaging.message.id`, `radar.outbox_id`, `radar.tipo`, `radar.desfecho`; `MensagemAdiada` não marca ERROR (é fluxo normal); outra exceção marca. Chama `processar_mensagem` sem mudar sua assinatura.
  - Spans `editais.registrar` e `editais.reconciliar` em volta das chamadas a `registrar`/`registrar_normalizados` e `reconciliar_retrato`.
  - `handler` decorado com `@com_telemetria("radar-processador")`; monta a `Mensagem` com `traceparent=traceparent_do_registro(registro)`; chama `processar_com_span`.
  - `FilaLocal.enviar_lote` chama `processar_com_span`.
  - Handlers da 4b: `@com_telemetria(<nome da função>)` e, se recebem mensagem SQS, o mesmo padrão de link.

- [ ] **Step 1:** testes: `test_consumo_e_outro_trace_com_link` (integração: coletar 1 página dentro de `coleta.janela`, publicar com `FilaLocal` → span `processar mensagem` com `trace_id` diferente do da janela e `links[0].context.span_id` igual ao span corrente quando a página foi gravada); `test_handler_sem_atributo_processa_sem_link` (evento sem `messageAttributes` → `batchItemFailures == []`, span sem links); `test_handler_traceparent_malformado_processa` (`stringValue="lixo"` → idem); `test_handler_descarrega_mesmo_com_erro` (`processar_mensagem` falso que levanta → `descarregar` chamado, id na lista de falhas); `test_adiada_nao_e_erro_no_span`; `test_registrar_e_reconciliar_viram_spans_filhos` (retrato Sanepar: 2 filhos do `processar mensagem`).
- [ ] **Step 2:** rodar → FAIL.
- [ ] **Step 3:** implementar.
- [ ] **Step 4:** `uv run pytest tests/unit/test_lambda_consumidor.py tests/integration/test_consumidor.py tests/integration/test_cli.py -v` → PASS.
- [ ] **Step 5:** commit `feat(processador): span do consumidor ligado ao produtor por link`.

### Task 6: CLI — ciclo de vida, frescor, `ExecucaoOk` e a linha `resultado:`

Depende das Tasks 2, 4 e 5.

**Files:**
- Modify: `src/radar/cli.py`, `src/radar/comum/telemetria.py` (só `telemetria_do_processo`)
- Test: `tests/integration/test_cli.py`

**Interfaces:**
- Consumes: `configurar_telemetria`, `encerrar` (Task 1); `configurar_metricas`, `registrar`, `descarregar` (Task 2); `frescor_por_fonte` (Task 4).
- Produces:
  - `telemetria_do_processo(servico: str) -> AbstractContextManager[None]` em `radar.comum.telemetria`: configura telemetria e métricas pelo `Config`; na saída, `metricas.descarregar()` e `encerrar(5.0)`, nunca levanta.
  - `atualizar`, `carga-inicial`, `processar` rodam dentro de `telemetria_do_processo("radar-coletor")`.
  - `_medir_frescor(sessao: Session) -> None`: chama `frescor_por_fonte(sessao, _agora(), ["pncp", *fontes_retrato()])`, registra `FrescorHoras` por fonte com `propriedades={"fonte": nome}` e loga `frescor_medido`; erro vira log, nunca falha. Chamado no começo e no fim do `atualizar` (antes do primeiro relay e depois do último) e no `auditar`.
  - `ExecucaoOk` (1 sem falhas, 0 com) no fim do `atualizar`.
  - `_encerrar(falhas)` imprime sempre `resultado: sem falhas` ou `resultado: <n> falhas` antes de sair, em `atualizar`, `carga-inicial` e `processar`.

- [ ] **Step 1:** testes: `test_atualizar_imprime_resultado` (com e sem falha; a linha começa com `resultado:` — comentário no teste citando `infra/prod/coletor.tf`, filtro `radar-coletor-execucoes`); `test_atualizar_mede_frescor_no_comeco_e_no_fim` (`EmissorEmMemoria`: 2 × (1 + número de fontes de retrato) valores de `FrescorHoras`); `test_atualizar_registra_execucao_ok` (1 sem falhas; 0 com uma fonte falhando); `test_telemetria_quebrada_nao_muda_nada` (exportador que levanta em `export` e cliente CloudWatch que levanta em `put_metric_data`, ligados por `RADAR_TELEMETRIA=xray`/`RADAR_METRICAS=cloudwatch` com fábricas trocadas por monkeypatch → mesmo código de saída e mesma contagem de `edital` que com tudo desligado); `test_auditar_mede_frescor`.
- [ ] **Step 2:** rodar → FAIL.
- [ ] **Step 3:** implementar.
- [ ] **Step 4:** verificação geral (Global Constraints) inteira → PASS.
- [ ] **Step 5:** commit `feat(cli): ciclo de vida da telemetria, frescor, ExecucaoOk e a linha resultado de volta`.

### Task 7: Infra — permissões, variáveis, alarmes, dashboard

Depende só dos nomes do Global Constraints. Paralela às Tasks 1–6.

**Files:**
- Create: `infra/prod/observabilidade.tf`
- Modify: `infra/prod/coletor.tf` (env do container; lista de ARNs da política `alertas_publicar`), `infra/prod/fila.tf` (env da `radar-processador`), `infra/prod/outputs.tf` (nome do dashboard)
- Test: `tests/unit/test_infra_estatico.py`

**Interfaces:**
- Produces:
  - Env do coletor: `RADAR_TELEMETRIA=xray`, `RADAR_METRICAS=cloudwatch`, `RADAR_TRACES_FRACAO=0.25`. Env da `radar-processador` (e das Lambdas da 4b): `RADAR_TELEMETRIA=xray`, `RADAR_METRICAS=emf`, `RADAR_TRACES_FRACAO=0.25`.
  - `aws_iam_role_policy_attachment` de `arn:aws:iam::aws:policy/AWSXrayWriteOnlyAccess` nas roles `coletor_task` e `processador` (e nas da 4b); `aws_iam_role_policy` `coletor_metricas` com `cloudwatch:PutMetricData` em `*` e condição `StringEquals cloudwatch:namespace = "Radar"`.
  - `aws_cloudwatch_log_group` `spans` (`name = "aws/spans"`, `retention_in_days = 7`) com comentário trazendo os dois comandos manuais do Transaction Search.
  - `aws_cloudwatch_metric_alarm` `frescor_estourado` (`radar-frescor-estourado`) e `paginas_com_erro` (`radar-paginas-com-erro`) com os valores do Global Constraints, ações no `radar-alertas`; os dois ARNs entram na política `alertas_publicar`.
  - `aws_cloudwatch_dashboard` `radar` com os widgets do spec (anotação horizontal de 36 h no frescor; 40 s e 10 s na latência; 0,05 na taxa de erro; 900 s na idade).
  - `aws_cloudwatch_query_definition` × 4 (frescor por fonte, p95 por fonte, taxa de erro por execução, tudo de um trace), nos log groups `/radar/coletor` e `/aws/lambda/radar-processador` (e da 4b).

- [ ] **Step 1:** testes estáticos: `test_no_maximo_dez_alarmes` (todos os `resource "aws_cloudwatch_metric_alarm"` de `infra/` ≤ 10); `test_no_maximo_dez_metricas_customizadas` (nomes de `metric_transformation` em `infra/` + soma, sobre `radar.comum.metricas.CATALOGO`, de `prod(len(valores) for cada dimensão)` ou 1 se sem dimensão ≤ 10 — se a Task 2 ainda não existe, o teste fica `xfail(reason="Task 2")`); `test_um_dashboard`; `test_spans_com_retencao` (`aws/spans` e `retention_in_days = 7`); `test_sem_application_signals` (nenhum `.tf` contém `application_signals`, `CloudWatchAgentServerPolicy` nem `aws-otel-collector`); `test_alarmes_novos_publicam_no_topico` (os dois ARNs na política `alertas_publicar`).
- [ ] **Step 2:** rodar → FAIL. Escrever o Terraform. `terraform fmt -check` e `terraform init -backend=false && terraform validate` em `infra/prod`.
- [ ] **Step 3:** testes passam (`test_fila_tem_dlq_e_alarmes` continua contando 3 em `fila.tf`).
- [ ] **Step 4:** commit `feat(infra): X-Ray, PutMetricData, alarmes de SLO e dashboard`.

### Task 8: Rodada real e fechamento (o coordenador, não um subagente)

- [ ] Medir **antes** em produção (main já com a 4b): `Init Duration` média e máxima da `radar-processador` e da `radar-api` em 7 dias (Logs Insights: `filter @type = "REPORT" and ispresent(@initDuration) | stats avg(@initDuration), max(@initDuration), count()`); anotar em `docs/etapas/05-medicoes.md`.
- [ ] Medir **depois** localmente: build das duas imagens do branch, tamanho e `-X importtime` como no Step 0 da Task 1; conferir o aceite do spec (+ ≤ 40 MB; import do handler sem o OTel, já que é preguiçoso).
- [ ] `terraform apply` em `infra/prod` (perfil `radar`, `-var tag_coletor=<sha atual>`) — cria `aws/spans` antes do passo seguinte; `alembic upgrade head` no Neon (pela `DATABASE_URL_UNPOOLED` do `.env.local`, sem imprimi-la).
- [ ] Ligar o Transaction Search: `aws xray update-trace-segment-destination --destination CloudWatchLogs --region us-east-2`; `aws xray update-indexing-rule --name Default --rule '{"Probabilistic":{"DesiredSamplingPercentage":1}}' --region us-east-2`; `aws xray get-trace-segment-destination` → `ACTIVE`. Se o plano FREE recusar, parar e registrar (fallback a decidir com o Augusto).
- [ ] PR para `main`, checks verdes, merge, deploy automático verde.
- [ ] `RunTask` manual do coletor com `RADAR_TRACES_FRACAO=1.0` por override; achar no X-Ray um trace `coleta.janela` e, pelo link, o `processar mensagem` da Lambda; conferir `trace_id` nos logs dos dois lados com a consulta "tudo de um trace"; ver as 6 métricas novas no namespace `Radar` (inclusive as EMF da Lambda) e o dashboard com dados.
- [ ] Demonstração de alarme: `aws cloudwatch set-alarm-state --alarm-name radar-paginas-com-erro --state-value ALARM --state-reason demo` → e-mail chega; volta a `OK`. Conferir que `radar-coletor-nao-rodou` está `OK` depois da rodada (a linha `resultado:` voltou).
- [ ] Depois de 10 cold starts da `radar-processador`, medir `Init Duration` de novo; depois de 7 dias, custo do X-Ray/CloudWatch no Cost Explorer e tamanho do `aws/spans`.
- [ ] `docs/etapas/05-observabilidade.md`: o que foi feito, números (imagem antes/depois, cold start antes/depois, spans/dia, custo em 7 dias, linha de base de cada SLI e se o alvo mudou), lições da rodada real, e **5 perguntas de entrevista** com "Resposta:" vazio:
  1. Por que o `traceparent` mora na linha da outbox e não é injetado pelo relay? O que se perderia?
  2. Qual a diferença entre ligar o consumo como filho e como link, e por que link aqui?
  3. Como você amostra 25% sem quebrar a cadeia produtor → consumidor, e o que a amostragem por cabeça não consegue fazer?
  4. Como você coube cinco SLIs em 6 métricas sem pagar por dimensão de fonte? Onde mora o recorte por fonte?
  5. Como o frescor é medido para não depender de quem acabou de coletar com sucesso, e por que o alarme trata dado ausente como violação?
- [ ] `docs/videos/05-observabilidade.md` (roteiro): abrir o dashboard; uma página do PNCP no X-Ray com as tentativas (503 → 200) como spans; seguir o link até a Lambda dias/minutos depois; colar o `trace_id` no Logs Insights; mostrar o orçamento de métricas e alarmes no teste estático; disparar o alarme; fechar com a tabela de SLOs e o custo medido.
- [ ] Apagar `docs/etapas/05-medicoes.md` (números já na nota); atualizar `docs/checklist.md`.
