# Etapa 6: exigências dos editais com IA — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** para os editais do PNCP que casam com o filtro e estão abertos, baixar os PDFs, extrair as exigências de habilitação com um LLM do Bedrock em us-east-2, compará-las com um perfil de exemplo por regras determinísticas, expor pela API e medir o acerto com evals de gabarito feito à mão.

**Architecture:** tudo de domínio fica em `radar.analise` (independente de `coleta` e `editais`, como o import-linter já exige). O LLM só devolve uma lista plana de achados por trecho; ancoragem, normalização, junção e comparação são código puro. A porta de entrada (`radar.cli`) seleciona os editais com `radar.editais.filtrar` e chama `radar.analise.analisar_edital`; a API lê com `obter_analise`. Provedor e cache são interfaces: Bedrock Converse em produção, replay de arquivos no CI.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0, Alembic, Pydantic 2, httpx + tenacity, pypdf (nova dependência), boto3 (`bedrock-runtime`, `s3`), Typer, FastAPI, pytest + testcontainers (Postgres 17), Terraform (provider AWS 5.x).

**Spec:** `docs/superpowers/specs/2026-11-27-etapa-06-ia-editais-design.md` (ler inteiro antes de qualquer tarefa).

## Global Constraints

- Worktree `C:\Users\aschi\radar-wt-ia`, branch `etapa-ia`. Verificação geral: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run lint-imports && uv run pytest -q` (Docker ligado para os testes de integração).
- Nenhum teste chama rede ou AWS: HTTP por `httpx.MockTransport`, boto3 por cliente falso injetado. `import boto3` e `import pypdf` só dentro das funções/classes que os usam (a API não paga o custo de importar).
- Recursos AWS só em us-east-2; `tests/unit/test_infra_estatico.py` continua passando.
- **Nenhum arquivo guardado** (lista `GUARDADOS` em `frontend/scripts/contrato-guardado.mjs`) é tocado; nada de trailer `Contrato-Front`. Se uma tarefa concluir que precisa, para e pergunta ao coordenador.
- Toda mensagem de commit termina com:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` e `Claude-Session: https://claude.ai/code/session_012v6r8uz1aiPHNQoDZ66AVf`.
- Modelos candidatos (ids exatos, sem prefixo de perfil): `amazon.nova-lite-v1:0` (padrão), `openai.gpt-oss-120b-1:0`, `qwen.qwen3-32b-v1:0`, `mistral.ministral-3-14b-instruct`.
- Valores fixos do spec: `max_editais_por_rodada = 10`; `caracteres_por_trecho = 60000`; sobreposição de 1 página; `max_trechos = 15`; `max_tokens = 4096`; `temperature = 0`; `teto_mensal_usd = 2.0`; página sem texto < 200 caracteres; `sem_texto` quando > 50% das páginas; até 3 documentos por edital; `grande_demais` > 20 MB; 1 s entre requisições ao PNCP; 3 tentativas; timeout HTTP 120 s; timeout de leitura do Bedrock 300 s, retries `adaptive` com 5 tentativas; tolerância de 1% nos evals; regressão de F1 > 0,05 falha o CI; agenda `cron(0 9 * * ? *)` America/Sao_Paulo; S3 expira em 180 dias.
- Formato de `config/ia.toml` (Task 1 escreve, Task 6 lê):
  ```toml
  regiao = "us-east-2"
  modelo_padrao = "amazon.nova-lite-v1:0"
  teto_mensal_usd = 2.0
  max_editais_por_rodada = 10
  caracteres_por_trecho = 60000
  max_trechos = 15
  max_tokens = 4096

  [modelos."amazon.nova-lite-v1:0"]
  modo_saida = "json_schema"   # json_schema | ferramenta
  entrada_usd_mtok = 0.06
  saida_usd_mtok = 0.24
  ```
- Variáveis novas em `radar.comum.Config`: `RADAR_CONFIG_IA` (padrão `config/ia.toml` da raiz), `RADAR_CONFIG_PERFIL` (padrão `config/perfil.toml`), `RADAR_IA_PROVEDOR` (`desligado` | `bedrock`, padrão `desligado`), `RADAR_DOCUMENTOS` (pasta local, padrão `~/.radar/documentos`, ou `s3://<bucket>/`).
- Logs com os nomes do spec: `analise_concluida`, `analise_sem_texto`, `achado_descartado`, `trecho_invalido`, `teto_ia_atingido`, `documento_falhou`. Linha final do `radar analisar`: `analise: <n> analisados, <n> pulados, <n> falhas, US$ <custo>`; **nunca** a palavra `resultado:`.

## Review Focus

1. `radar analisar` escreve no mesmo log group do coletor: uma linha com `resultado:` faria o alarme "coletor não rodou" calar. Esperado: a saída nunca contém `resultado:` (teste na Task 8).
2. Modelo devolve um trecho parafraseado ou inventado: o achado some, é contado e não vira exigência (teste na Task 3).
3. Edital só com zip ou PDF escaneado: análise `verificar`, `incompleta`, **zero** chamadas ao provedor (teste na Task 8 com provedor que falha se chamado).
4. Teto mensal atingido no terceiro trecho: nenhuma chamada a mais, nenhuma `analise_edital` gravada para aquele edital, saída 1 (teste na Task 8).
5. Prompt ou esquema mudou e ninguém regravou as respostas: o CI falha com a lista de chaves ausentes, nunca chama a API (teste na Task 9).

---

### Task 1: Sonda do Bedrock (o coordenador, não um subagente)

Custa ~US$ 0,01. Precisa do perfil AWS `radar`. Bloqueia as Tasks 6 e 7; as outras podem começar antes.

**Files:**
- Create: `config/ia.toml`, `tests/dados/ia/converse_json_schema.json`, `tests/dados/ia/converse_ferramenta.json`

**Interfaces:**
- Produces: `config/ia.toml` no formato das Global Constraints, com uma seção por modelo que respondeu; respostas cruas da Converse (sem cabeçalhos HTTP nem ids de requisição) como fixtures.

- [ ] **Step 1:** para cada modelo candidato, uma chamada Converse em us-east-2 (script descartável no scratchpad, fora do repositório) pedindo `{"achados": [...]}` de um parágrafo de edital com uma exigência de capital, primeiro com `outputConfig.textFormat` (json_schema), depois, se recusar, com `toolConfig` de uma ferramenta e `toolChoice` forçado. Anotar qual modo funcionou, o formato exato do pedido que funcionou e se vieram blocos de raciocínio.
- [ ] **Step 2:** conferir os preços on-demand de us-east-2 de cada modelo na página de preços do Bedrock; o que não for confirmado fica com comentário `# [N] não conferido`.
- [ ] **Step 3:** escrever `config/ia.toml` e as duas fixtures (uma resposta de cada modo, de qualquer modelo). Modelo que deu AccessDenied fica fora do arquivo, com uma linha de comentário dizendo por quê.
- [ ] **Step 4:** commit `chore(analise): sonda do Bedrock: modos de saída e preços por modelo`.

### Task 2: Tipos das exigências, tabelas e migração 0006

**Files:**
- Create: `src/radar/analise/exigencias.py`, `src/radar/analise/modelos.py`, `migrations/versions/0006_analise.py`
- Modify: `src/radar/analise/__init__.py`, `src/radar/comum/config.py`, `migrations/env.py` (registrar `radar.analise.modelos`), `pyproject.toml` (`pypdf>=5` em dependencies, `uv lock`; módulos internos de `radar.analise` em `forbidden_modules` do contrato "api e cli importam só a raiz")
- Test: `tests/unit/test_exigencias.py`, `tests/integration/test_analise_esquema.py`, `tests/unit/test_config.py`

**Interfaces:**
- Produces (em `exigencias.py`):
  - `Campo = Literal["atestado_tecnico", "capital_social_minimo", "patrimonio_liquido_minimo", "garantia_proposta", "garantia_contratual", "visita_tecnica", "consorcio", "me_epp", "prazo_execucao", "documento_habilitacao"]`; `DOCUMENTOS: tuple[str, ...]` (os 11 do spec).
  - `class Achado(BaseModel)`: `campo: Campo`, `valor: str`, `numero: float | None`, `unidade: str`, `trecho: str` (`extra="forbid"`). `class RespostaExtracao(BaseModel)`: `achados: list[Achado]`.
  - `esquema_resposta() -> dict[str, Any]`: JSON Schema de `RespostaExtracao` nas regras do Bedrock (todo objeto com `additionalProperties: false`, todos os campos em `required`, sem `$ref`/`$defs`, sem `title`, um só nullable).
  - `class ValorEvidencia(BaseModel)`: `numero: Decimal | None`, `trecho: str`, `pagina: int`, `conflito: bool = False`, `observacao: str = ""`. `class CategoriaEvidencia(BaseModel)`: `valor: str`, `trecho: str`, `pagina: int`, `conflito: bool = False`. `class Atestado(BaseModel)`: `descricao: str`, `quantidade: Decimal | None`, `unidade: str | None`, `trecho: str`, `pagina: int`.
  - `class Exigencias(BaseModel)`: `atestados: list[Atestado] = []`, `capital_social_minimo`, `patrimonio_liquido_minimo`, `garantia_proposta_percentual`, `garantia_contratual_percentual`, `prazo_execucao_dias: ValorEvidencia | None = None`, `visita_tecnica`, `consorcio`, `me_epp: CategoriaEvidencia | None = None`, `documentos: list[CategoriaEvidencia] = []` (o `valor` de cada um é um item de `DOCUMENTOS`).
- Produces (em `modelos.py`): ORM `DocumentoEdital`, `AnaliseEdital`, `RespostaLLM` com as colunas, CHECKs e únicos do spec ("Dados"); `StatusDocumento = Literal["extraido", "sem_texto", "nao_pdf", "grande_demais", "falha"]`; `Veredito = Literal["apto", "inapto", "verificar"]`.
- Produces (em `Config`): `config_ia: Path`, `config_perfil: Path`, `ia_provedor: Literal["desligado", "bedrock"] = "desligado"`, `documentos: str` (padrão `str(Path.home() / ".radar" / "documentos")`).

- [ ] **Step 1:** testes: `test_esquema_resposta_regras_bedrock` (percorre o esquema: todo `object` tem `additionalProperties is False` e `required` igual às chaves de `properties`; nenhuma chave `$ref`, `$defs`, `title`, `minimum`, `maxLength`; exatamente 1 tipo com `"null"`); `test_achado_extra_proibido` (`Achado.model_validate({..., "pagina": 3})` → `ValidationError`); `test_exigencias_ida_e_volta_json` (`Exigencias.model_validate_json(e.model_dump_json()) == e`); em `test_analise_esquema.py`: `alembic check` sem diferenças, `status="x"` → `IntegrityError`, segundo `AnaliseEdital` com a mesma `(fonte, id_na_fonte, modelo, versao_prompt)` → `IntegrityError`; em `test_config.py`: padrões novos (`ia_provedor == "desligado"`).
- [ ] **Step 2:** rodar `uv run pytest tests/unit/test_exigencias.py tests/integration/test_analise_esquema.py -v` → FAIL (import).
- [ ] **Step 3:** implementar. `esquema_resposta` parte de `RespostaExtracao.model_json_schema()` e resolve `$defs` em linha.
- [ ] **Step 4:** testes passam; `uv run lint-imports` passa.
- [ ] **Step 5:** commit `feat(analise): tipos das exigências e tabelas (migração 0006)`.

### Task 3: Ancoragem, normalização e junção

Depende da Task 2. Paralela às Tasks 4, 5, 6 e 10.

**Files:**
- Create: `src/radar/analise/normalizacao.py`
- Test: `tests/unit/test_normalizacao.py`

**Interfaces:**
- Consumes: `Achado`, `Exigencias`, `ValorEvidencia`, `CategoriaEvidencia`, `Atestado`, `DOCUMENTOS` (Task 2); `Trecho` (Task 5: `texto`, `pagina_inicial`), só o tipo.
- Produces:
  - `@dataclass(frozen=True) AchadoAncorado(achado: Achado, pagina: int)`.
  - `ancorar(achados: list[Achado], texto_trecho: str, pagina_inicial: int) -> tuple[list[AchadoAncorado], int]`: compara com espaços colapsados e sem diferença de maiúsculas; página = número do último marcador `[[pagina N]]` antes da posição (ou `pagina_inicial` se não houver); devolve os descartados como contagem e loga `achado_descartado`.
  - `normalizar_unidade(unidade: str) -> tuple[str, Decimal]` (unidade canônica e fator: `"metros"` → `("km", Decimal("0.001"))`, `"km"`/`"quilometros"` → `("km", 1)`, `"un"`/`"unidades"`/`"hidrometros"` → `("un", 1)`, `"leituras"` → `("leitura", 1)`, `"ligacoes"` → `("ligacao", 1)`; desconhecida → `(minúscula sem acento, 1)`).
  - `juntar(achados: list[AchadoAncorado], valor_estimado: Decimal | None) -> tuple[Exigencias, int]` (o int = achados descartados por valor fora do vocabulário). Regras exatamente as do spec ("Do achado à exigência", itens 2 e 3).

- [ ] **Step 1:** testes: `test_trecho_parafraseado_descartado` (achado com trecho que não existe → lista vazia, descartados 1); `test_trecho_com_quebra_de_linha_ancora` (texto `"capital  social\nmínimo"` casa com `"capital social mínimo"`); `test_pagina_vem_do_marcador` (trecho depois de `[[pagina 7]]` → `pagina == 7`, mesmo que o achado não diga nada de página); `test_numeros_diferentes_ficam_com_maior_e_conflito` (capital 100000 e 150000 → `numero == 150000`, `conflito is True`); `test_duplicata_da_sobreposicao_conta_uma_vez` (mesmo atestado em dois trechos → `len(atestados) == 1`); `test_precedencia_visita` (`facultativa` + `obrigatoria` → `obrigatoria`, `conflito`); `test_percentual_vira_reais_com_valor_estimado` (10% de 2.000.000 → 200000); `test_percentual_sem_valor_estimado` (`numero is None`, `observacao` contém `"10%"`); `test_meses_viram_dias` (12 meses → 360); `test_metros_viram_km` (5000 m → `Decimal("5")`, `"km"`); `test_valor_fora_do_vocabulario_descartado` (`visita_tecnica` com valor `"talvez"` → `visita_tecnica is None`, contagem 1); `test_documento_desconhecido_descartado`.
- [ ] **Step 2:** rodar `uv run pytest tests/unit/test_normalizacao.py -v` → FAIL.
- [ ] **Step 3:** implementar.
- [ ] **Step 4:** testes passam.
- [ ] **Step 5:** commit `feat(analise): ancoragem, normalização e junção dos achados`.

### Task 4: Perfil de exemplo e comparação

Depende da Task 2. Paralela às Tasks 3, 5, 6 e 10.

**Files:**
- Create: `src/radar/analise/perfil.py`, `src/radar/analise/comparacao.py`, `config/perfil.toml`
- Test: `tests/unit/test_comparacao.py`

**Interfaces:**
- Consumes: `Exigencias` e companhia (Task 2); `normalizar_unidade` (Task 3) — se a Task 3 ainda não estiver pronta, importe e deixe o teste do atestado com unidade em metros para depois dela.
- Produces:
  - `class AtestadoPerfil(BaseModel)`: `descricao: str`, `termos: list[str]`, `quantidade: Decimal`, `unidade: str`. `class Perfil(BaseModel)`: `porte: Literal["me", "epp", "demais"]`, `capital_social: Decimal | None`, `patrimonio_liquido: Decimal | None`, `garantia_maxima_percentual: Decimal`, `faz_visita_tecnica: bool`, `depende_de_consorcio: bool`, `documentos: list[str]`, `atestados: list[AtestadoPerfil]`. `carregar_perfil(caminho: Path) -> Perfil` (seção `[empresa]` + `[[atestados]]`; documento fora de `DOCUMENTOS` → `ValueError`).
  - `Situacao = Literal["atende", "nao_atende", "verificar", "informativo"]`; `class ItemComparacao(BaseModel)`: `campo: str`, `situacao: Situacao`, `exigido: str`, `perfil: str`, `motivo: str`, `trecho: str | None`, `pagina: int | None`. `class Comparacao(BaseModel)`: `veredito: Veredito`, `itens: list[ItemComparacao]`.
  - `comparar(exigencias: Exigencias, perfil: Perfil, incompleta: bool) -> Comparacao` com as regras do spec ("Comparação").
- `config/perfil.toml`: primeira linha `# PERFIL DE EXEMPLO: valores fictícios, não são dados de nenhuma empresa real.`; `porte = "demais"`, capital 1.000.000, patrimônio 1.500.000, garantia máxima 5, visita `true`, consórcio `false`, 8 documentos; quatro `[[atestados]]` genéricos (vazamentos 1500 km, hidrômetros 20000 un, leituras 500000 leitura, rede de água 30000 m).

- [ ] **Step 1:** testes em tabela (`pytest.mark.parametrize`), um caso por regra do spec, com o `situacao` esperado; mais: `test_conflito_vira_verificar`; `test_atestado_nunca_nao_atende` (unidade diferente → `verificar`); `test_atestado_casa_por_termo_sem_acento` (exigido "pesquisa de VAZAMENTOS não visíveis", 1000 km → `atende`); `test_veredito_inapto_vence_verificar`; `test_incompleta_sem_nao_atende_vira_verificar`; `test_tudo_atende_vira_apto`; `test_perfil_de_exemplo_carrega` (lê `config/perfil.toml` do repositório); `test_documento_invalido_no_perfil` → `ValueError`.
- [ ] **Step 2:** rodar `uv run pytest tests/unit/test_comparacao.py -v` → FAIL.
- [ ] **Step 3:** implementar.
- [ ] **Step 4:** testes passam.
- [ ] **Step 5:** commit `feat(analise): perfil de exemplo e comparação determinística`.

### Task 5: Documentos do PNCP, extração de texto, trechos e armazém

Depende da Task 2. Paralela às Tasks 3, 4, 6 e 10.

**Files:**
- Create: `src/radar/analise/arquivos_pncp.py`, `src/radar/analise/extracao.py`, `src/radar/analise/trechos.py`, `src/radar/analise/armazem.py`, `tests/dados/ia/edital_texto.pdf` (2 páginas com texto conhecido, gerado uma vez fora do projeto e commitado; < 50 KB) e `tests/dados/ia/edital_escaneado.pdf` (2 páginas em branco, `pypdf.PdfWriter.add_blank_page`)
- Test: `tests/unit/test_arquivos_pncp.py`, `tests/unit/test_extracao.py`, `tests/unit/test_trechos.py`, `tests/unit/test_armazem.py`

**Interfaces:**
- Produces:
  - `chave_pncp(id_na_fonte: str) -> tuple[str, int, int] | None` (`"78200482000110-1-000298/2026"` → `("78200482000110", 2026, 298)`).
  - `@dataclass(frozen=True) ArquivoPNCP(sequencial: int, titulo: str, tipo_nome: str, url: str)`.
  - `ClienteArquivosPNCP(http: httpx.Client, pausa_s: float = 1.0, tentativas: int = 3)`: `listar(cnpj: str, ano: int, sequencial: int) -> list[ArquivoPNCP]`; `baixar(url: str, limite_bytes: int = 20_000_000) -> bytes` (lê em streaming; passou do limite → `GrandeDemais`). `criar_http_arquivos() -> httpx.Client` (base `https://pncp.gov.br/pncp-api/v1`, timeout 120 s, User-Agent do projeto). Erro definitivo → `ErroArquivos`.
  - `escolher_documentos(arquivos: list[ArquivoPNCP]) -> list[ArquivoPNCP]` (regra do spec: `edital` ou `termo de referencia` no tipo sem acento, até 3, por `sequencial`).
  - `@dataclass(frozen=True) TextoExtraido(paginas: list[str], paginas_sem_texto: int)`; `eh_pdf(dados: bytes) -> bool`; `extrair_paginas(dados: bytes) -> TextoExtraido`; `status_do_texto(t: TextoExtraido) -> Literal["extraido", "sem_texto"]`.
  - `@dataclass(frozen=True) Trecho(texto: str, pagina_inicial: int, pagina_final: int)`; `dividir(paginas: list[str], caracteres: int = 60000) -> list[Trecho]` (cada página prefixada por `[[pagina N]]\n`, N a partir de 1; sobreposição de 1 página; página gigante cortada por caracteres).
  - `class Armazem(Protocol)`: `guardar(chave: str, dados: bytes) -> None`, `ler(chave: str) -> bytes | None`. `ArmazemLocal(raiz: Path)`, `ArmazemS3(bucket: str, prefixo: str = "", cliente: Any | None = None)`, `criar_armazem(destino: str) -> Armazem` (`s3://bucket/prefixo` ou pasta).

- [ ] **Step 1:** testes: `test_chave_pncp` (válida e inválida → `None`); `test_listar_le_documentos` (MockTransport com o JSON de exemplo do manual: `sequencialDocumento`, `url`, `tipoDocumentoNome`, `titulo`); `test_429_tenta_de_novo_e_depois_falha` (3 respostas 429 → `ErroArquivos`, 3 requisições); `test_baixar_corta_no_limite` (corpo de 30 bytes, limite 10 → `GrandeDemais`); `test_escolher_documentos` (Edital, Termo de Referência, Planilha, Edital 2, Edital 3 → os 3 primeiros elegíveis por sequencial); `test_eh_pdf` (`b"%PDF-1.7"` sim, `b"PK\x03\x04"` não); `test_extrair_paginas_pdf_de_texto`; `test_escaneado_vira_sem_texto`; `test_dividir_sobrepoe_uma_pagina` (5 páginas de 25.000 caracteres → trechos `(1,2)`, `(2,3)`, `(3,4)`, `(4,5)`; cada texto começa com o marcador da sua `pagina_inicial`); `test_dividir_pagina_gigante` (1 página de 130.000 → 3 trechos, nenhum acima de 60.000 + tamanho do marcador); `test_armazem_local_ida_e_volta`; `test_armazem_s3_usa_bucket_e_prefixo` (cliente falso registra `put_object(Bucket, Key)`); `test_ler_ausente_devolve_none` (local e S3 com `NoSuchKey`).
- [ ] **Step 2:** rodar `uv run pytest tests/unit/test_arquivos_pncp.py tests/unit/test_extracao.py tests/unit/test_trechos.py tests/unit/test_armazem.py -v` → FAIL.
- [ ] **Step 3:** implementar. Retentativa com `tenacity` como em `radar/coleta/pncp.py` (sem importar de lá: `analise` não importa `coleta`).
- [ ] **Step 4:** testes passam.
- [ ] **Step 5:** commit `feat(analise): documentos do PNCP, texto por página, trechos e armazém`.

### Task 6: Provedor Bedrock, cache e custos

Depende das Tasks 1 e 2. Paralela às Tasks 3, 4, 5 e 10.

**Files:**
- Create: `src/radar/analise/provedor.py`, `src/radar/analise/cache.py`, `src/radar/analise/custos.py`
- Test: `tests/unit/test_provedor.py`, `tests/unit/test_cache.py`, `tests/integration/test_custos.py`

**Interfaces:**
- Consumes: `config/ia.toml` e as fixtures `tests/dados/ia/converse_*.json` (Task 1); `RespostaLLM` (Task 2).
- Produces:
  - `@dataclass(frozen=True) Pedido(modelo: str, sistema: str, texto: str, esquema: dict[str, Any], max_tokens: int)`; `@dataclass(frozen=True) Resposta(texto: str, tokens_entrada: int, tokens_saida: int, latencia_ms: int)`; `class Provedor(Protocol): def gerar(self, pedido: Pedido) -> Resposta`.
  - `ModoSaida = Literal["json_schema", "ferramenta"]`; `@dataclass(frozen=True) PrecoModelo(modo_saida: ModoSaida, entrada_usd_mtok: Decimal, saida_usd_mtok: Decimal)`; `@dataclass(frozen=True) ConfigIA(regiao: str, modelo_padrao: str, teto_mensal_usd: Decimal, max_editais_por_rodada: int, caracteres_por_trecho: int, max_trechos: int, max_tokens: int, modelos: dict[str, PrecoModelo])`; `carregar_config_ia(caminho: Path) -> ConfigIA` (região ≠ `us-east-2` ou modelo com prefixo proibido → `ValueError`).
  - `validar_modelo(modelo: str, regiao: str) -> None`: `ValueError` para prefixo `us.`, `global.`, `eu.`, `apac.` ou região ≠ `us-east-2`.
  - `ProvedorBedrock(config: ConfigIA, cliente: Any | None = None)`: valida antes de chamar; Converse com `temperature=0`; no modo `ferramenta` devolve `json.dumps(toolUse.input)`; ignora blocos `reasoningContent`; tokens de `usage`; `ErroProvedor` para resposta sem texto nem ferramenta.
  - `chave_cache(pedido: Pedido, modo_saida: str) -> str` e `versao_prompt(sistema: str, esquema: dict[str, Any]) -> str` (12 hex), regras do spec ("Cache").
  - `class Cache(Protocol)`: `ler(chave: str) -> Resposta | None`, `gravar(chave: str, modelo: str, resposta: Resposta, custo_usd: Decimal) -> None`. `CacheArquivos(raiz: Path)` (arquivo `<raiz>/<modelo com ":" e "." trocados por "_">/<chave>.json`, JSON ordenado com indentação 2 para diff legível); `CacheBanco(sessao: Session)` (sem commit: quem chama decide).
  - `class RespostaAusente(Exception)` com atributo `chave`; `ProvedorComCache(interno: Provedor | None, cache: Cache, modo: Literal["gravar", "replay"], config: ConfigIA, antes_de_chamar: Callable[[Pedido], None] | None = None)` implementa `Provedor`; atributo `custo_usd: Decimal` acumulado e `chamadas_reais: int`. `antes_de_chamar` é o gancho do teto.
  - `custo(resposta: Resposta, preco: PrecoModelo) -> Decimal`; `estimar(pedido: Pedido, preco: PrecoModelo) -> Decimal` (caracteres/3 tokens de entrada + `max_tokens` de saída); `gasto_do_mes(sessao: Session, agora: datetime) -> Decimal`; `class TetoAtingido(Exception)`; `guarda_teto(sessao: Session, config: ConfigIA, agora: Callable[[], datetime], gasto_rodada: Callable[[], Decimal]) -> Callable[[Pedido], None]` (levanta `TetoAtingido` e loga `teto_ia_atingido` quando mês + rodada + estimativa > teto).

- [ ] **Step 1:** testes: `test_prefixo_cross_region_recusado` (`us.amazon.nova-lite-v1:0` e `global.x` → `ValueError` **e** o cliente falso não recebeu chamada); `test_converse_json_schema_le_fixture` e `test_converse_ferramenta_le_fixture` (cliente falso devolve as fixtures da Task 1 → `Resposta.texto` valida em `RespostaExtracao`); `test_bloco_de_raciocinio_ignorado`; `test_pedido_usa_temperatura_zero_e_max_tokens` (inspeciona os kwargs do cliente falso); `test_chave_muda_com_sistema_esquema_modelo_texto` (4 variações → 4 chaves distintas; mesmo pedido → mesma chave); `test_replay_sem_entrada_levanta` (`RespostaAusente`, `chave` preenchida, provedor interno não chamado); `test_gravar_chama_uma_vez_e_depois_acerta` (2 `gerar` iguais → 1 chamada real, `custo_usd` só da primeira); `test_cache_arquivos_ida_e_volta`; integração: `test_gasto_do_mes_soma_so_o_mes` (linhas de outubro e novembro); `test_guarda_teto_levanta_antes` (gasto do mês 1,99, estimativa 0,02 → `TetoAtingido`); `test_carregar_config_ia_do_repositorio` (lê `config/ia.toml`; `modelo_padrao` está em `modelos`).
- [ ] **Step 2:** rodar `uv run pytest tests/unit/test_provedor.py tests/unit/test_cache.py tests/integration/test_custos.py -v` → FAIL.
- [ ] **Step 3:** implementar. Cliente: `boto3.client("bedrock-runtime", region_name=config.regiao, config=botocore.config.Config(read_timeout=300, retries={"mode": "adaptive", "max_attempts": 5}))`. O formato exato do pedido de cada modo é o que a Task 1 anotou.
- [ ] **Step 4:** testes passam; `uv run mypy src` limpo.
- [ ] **Step 5:** commit `feat(analise): provedor Bedrock trocável, cache de respostas e teto de custo`.

### Task 7: Núcleo da extração e o prompt

Depende das Tasks 3, 5 e 6.

**Files:**
- Create: `src/radar/analise/extrator.py`, `src/radar/analise/prompts/extracao.md`
- Test: `tests/unit/test_extrator.py`

**Interfaces:**
- Consumes: `dividir`, `Trecho` (Task 5); `ancorar`, `juntar` (Task 3); `Provedor`, `Pedido`, `ConfigIA`, `versao_prompt` (Task 6); `esquema_resposta`, `RespostaExtracao` (Task 2).
- Produces:
  - `SISTEMA: str` (conteúdo de `prompts/extracao.md`, lido com `importlib.resources`).
  - `@dataclass(frozen=True) Extracao(exigencias: Exigencias, trechos: int, trechos_invalidos: int, achados_descartados: int, truncado: bool, tokens_entrada: int, tokens_saida: int)`; propriedade `incompleta -> bool` (`trechos_invalidos > 0 or truncado`).
  - `extrair_exigencias(paginas: list[str], provedor: Provedor, modelo: str, config: ConfigIA, valor_estimado: Decimal | None) -> Extracao`: `dividir` → no máximo `max_trechos` → um `Pedido` por trecho → `RespostaExtracao.model_validate_json` (falha → `trecho_invalido`, segue) → `ancorar` → `juntar` uma vez sobre todos os ancorados. `TetoAtingido` e `RespostaAusente` propagam.
- Prompt (em português): o papel ("extrai exigências de habilitação de edital brasileiro"), a tabela de campos e vocabulários do spec, "copie o `trecho` literalmente do texto, sem corrigir nada", "não invente: se o texto não trata do campo, não devolva achado", "`numero` em reais, percentual, quantidade ou dias conforme o campo; `null` se não houver número".

- [ ] **Step 1:** testes com provedor falso que responde por trecho: `test_dois_trechos_juntam` (capital no trecho 1, visita no 2 → ambos em `exigencias`); `test_resposta_invalida_marca_incompleta` (trecho 2 devolve `"não sei"` → `trechos_invalidos == 1`, `incompleta`, achados do trecho 1 mantidos); `test_trunca_em_max_trechos` (`max_trechos=2`, texto de 4 trechos → 2 chamadas, `truncado`); `test_sistema_e_esquema_vao_no_pedido` (`pedido.esquema == esquema_resposta()`, `pedido.sistema == SISTEMA`); `test_teto_propaga` (provedor levanta `TetoAtingido` → propaga); `test_prompt_empacotado` (`SISTEMA` não vazio e cita os 10 campos).
- [ ] **Step 2:** rodar `uv run pytest tests/unit/test_extrator.py -v` → FAIL.
- [ ] **Step 3:** implementar. Conferir que o wheel inclui `prompts/extracao.md` (`uv build` e listar o wheel) e que `docker/api-lambda.Dockerfile` já copia `src/` inteiro (copia).
- [ ] **Step 4:** testes passam.
- [ ] **Step 5:** commit `feat(analise): extração em trechos com o prompt versionado`.

### Task 8: Pipeline com banco, `radar analisar` e a rota da API

Depende das Tasks 4 e 7. Paralela à Task 9.

**Files:**
- Create: `src/radar/analise/pipeline.py`
- Modify: `src/radar/analise/__init__.py`, `src/radar/cli.py`, `src/radar/api/app.py`, `src/radar/api/modelos.py`, `frontend/openapi.json` (`uv run python scripts/exportar_openapi.py`), `frontend/src/dados/api.d.ts` (`cd frontend && npm run contrato`)
- Test: `tests/integration/test_pipeline_analise.py`, `tests/integration/test_cli_analisar.py`, `tests/integration/test_api.py`

**Interfaces:**
- Consumes: tudo de `radar.analise` acima; `filtrar`, `separar_id_publico`, `obter_edital`, `carregar_criterios` de `radar.editais`.
- Produces:
  - `@dataclass(frozen=True) AlvoAnalise(fonte: str, id_na_fonte: str, valor_estimado: Decimal | None)`.
  - `@dataclass(frozen=True) Dependencias(cliente_arquivos: ClienteArquivosPNCP, armazem: Armazem, provedor: Provedor, config: ConfigIA, perfil: Perfil, modelo: str)`.
  - `ja_analisado(sessao: Session, alvo: AlvoAnalise, modelo: str, versao: str) -> bool`.
  - `analisar_edital(sessao: Session, alvo: AlvoAnalise, dep: Dependencias, agora: datetime) -> AnaliseEdital`: documentos (listar, escolher, baixar se o `sha256` não está no armazém, extrair, gravar `DocumentoEdital` com upsert) → se nenhum documento `extraido`: análise `verificar`, `incompleta`, sem provedor, log `analise_sem_texto` → senão `extrair_exigencias` sobre as páginas dos documentos `extraido` em ordem → `comparar` → upsert de `AnaliseEdital` → commit. Um `DocumentoEdital` `falha` loga `documento_falhou`. `TetoAtingido` faz rollback e propaga (nada gravado para esse edital).
  - `obter_analise(sessao: Session, fonte: str, id_na_fonte: str, modelo: str) -> AnaliseLida | None` (`AnaliseLida` = dataclass com `AnaliseEdital` + lista de `DocumentoEdital`, a mais nova por `criada_em` para o modelo).
  - CLI `radar analisar [--limite N] [--modelo ID] [--edital ID_PUBLICO] [--refazer]`: `RADAR_IA_PROVEDOR=desligado` → sai 2 com "defina RADAR_IA_PROVEDOR=bedrock"; seleciona `filtrar(..., somente_abertos=True)` só `fonte == "pncp"` e não analisados, até `--limite` (padrão `max_editais_por_rodada`); provedor = `ProvedorComCache(ProvedorBedrock, CacheBanco, "gravar", guarda_teto)`; linha final do spec; sai 1 se houve falha ou teto.
  - API `GET /api/analises/{id_publico:path}` → `AnaliseOut` (campos do spec, "API") ou 404, modelo = `modelo_padrao`.

- [ ] **Step 1:** testes de pipeline (banco do testcontainers, `httpx.MockTransport` servindo listagem + `tests/dados/ia/edital_texto.pdf`, `ArmazemLocal(tmp_path)`, provedor falso): `test_analisa_e_grava_documento_e_analise`; `test_zip_e_escaneado_nao_chamam_provedor` (provedor que levanta se chamado; veredito `verificar`, `incompleta`); `test_teto_no_meio_nao_grava` (provedor que levanta `TetoAtingido` no 2º trecho → 0 linhas em `analise_edital`, exceção propaga); `test_pdf_ja_no_armazem_nao_baixa_de_novo` (segunda análise com `--refazer`: só a listagem vai à rede); `test_refazer_regrava_upsert` (1 linha só).
- [ ] **Step 2:** testes de CLI (CliRunner, fakes injetados como em `tests/integration/fakes.py`): `test_analisar_pula_ja_analisado`; `test_analisar_so_pncp_aberto_que_casa`; `test_saida_nao_tem_resultado_dois_pontos` (`"resultado:" not in saida` e a linha começa com `analise: `); `test_provedor_desligado_sai_2`; `test_teto_sai_1`.
- [ ] **Step 3:** testes de API: `test_analise_404_sem_analise`; `test_analise_com_barra_no_id` (`/api/analises/pncp~78200482000110-1-000298/2026` → 200 com `veredito`).
- [ ] **Step 4:** rodar os três arquivos → FAIL. Implementar. Regerar `openapi.json` e `api.d.ts`; dentro de `frontend/`: `npm test`, `npx tsc --noEmit` passam sem mudar nenhum outro arquivo do front.
- [ ] **Step 5:** verificação geral (Global Constraints) → PASS.
- [ ] **Step 6:** commit `feat(analise): pipeline, radar analisar e GET /api/analises`.

### Task 9: Evals: gabarito, métricas, `radar avaliar` e replay no CI

Depende das Tasks 5 e 7. Paralela à Task 8.

**Files:**
- Create: `src/radar/analise/evals.py`, `evals/README.md` (formato e o passo a passo de conferência, curto), `evals/linha_de_base.json` (`{}`), `tests/dados/evals/` (2 editais de brinquedo: texto, gabarito conferido, respostas em cache)
- Modify: `src/radar/cli.py` (sub-app `avaliar`), `src/radar/analise/__init__.py`
- Test: `tests/unit/test_evals_metricas.py`, `tests/unit/test_evals_replay.py`

**Interfaces:**
- Consumes: `extrair_exigencias`, `Extracao` (Task 7); `CacheArquivos`, `ProvedorComCache`, `RespostaAusente`, `ConfigIA` (Task 6); `Exigencias`, `normalizar_unidade` (Tasks 2 e 3); `ClienteArquivosPNCP`, `escolher_documentos`, `extrair_paginas` (Task 5).
- Produces:
  - `class DocumentoGabarito(BaseModel)`: `sequencial: int`, `url: str`, `sha256: str`. `class Gabarito(BaseModel)`: `edital: str`, `documentos: list[DocumentoGabarito]`, `conjunto: Literal["ajuste", "teste"]`, `conferido: bool`, `conferido_por: str | None`, `conferido_em: date | None`, `observacoes: str`, `exigencias: Exigencias`. `slug(id_publico: str) -> str`.
  - `@dataclass Contagem(vp: int = 0, fp: int = 0, fn: int = 0, ausentes: int = 0, alucinacoes: int = 0)` com `precisao`, `recall`, `f1`, `taxa_alucinacao` (divisão por zero → `None`).
  - `comparar_com_gabarito(previsto: Exigencias, gabarito: Exigencias) -> dict[str, Contagem]` (chaves: os 8 campos escalares/categóricos, `atestados`, `documentos`; regras do spec, "Métricas").
  - `@dataclass Relatorio(modelo: str, por_campo: dict[str, dict[str, Contagem]], nao_conferidos: dict[str, Contagem], custo_usd: Decimal, latencias_ms: list[int], achados_descartados: int, trechos_invalidos: int, ausentes: list[str])` (`por_campo[conjunto][campo]`); `para_markdown(r: Relatorio) -> str` (seção "rascunho não conferido: métrica sem valor" quando houver).
  - `avaliar(raiz: Path, provedor: Provedor, modelo: str, config: ConfigIA, conjunto: Literal["ajuste", "teste", "todos"]) -> Relatorio`: lê `textos/` e `gabarito/`; em replay, junta todas as `RespostaAusente` em `ausentes` em vez de parar no primeiro.
  - `validar_gabaritos(raiz: Path) -> list[str]` (erros: esquema, slug × `edital`, trecho de valor do gabarito que não existe no texto).
  - CLI: `radar avaliar preparar ID_PUBLICO --conjunto ajuste|teste` (baixa, extrai, grava `evals/textos/<slug>.json`; não cria gabarito), `radar avaliar validar` (sai 1 com erros), `radar avaliar rodar --modelo ID --modo replay|gravar [--conjunto] [--teto-usd 1.0]` (imprime o Markdown; `gravar` exige `RADAR_IA_PROVEDOR=bedrock` e para no teto da rodada).

- [ ] **Step 1:** testes de métricas com casos à mão: `test_escalar_igual_vp`; `test_escalar_diferente_conta_fp_e_fn`; `test_tolerancia_um_por_cento` (100000 × 100900 → VP; × 102000 → FP+FN); `test_ausente_nos_dois_nao_conta_e_soma_ausentes`; `test_alucinacao` (gabarito ausente, modelo presente → FP e `alucinacoes == 1`, `taxa_alucinacao == 1.0`); `test_atestados_par_unidade_quantidade` (descrição diferente, mesmo par → VP; 5000 m vs 5 km → VP); `test_documentos_por_item`; `test_divisao_por_zero_e_none`.
- [ ] **Step 2:** testes de replay sobre `tests/dados/evals/`: `test_replay_completo_gera_relatorio` (modo `replay`, provedor interno `None`, F1 esperado conhecido do caso de brinquedo); `test_replay_com_falta_lista_chaves` (apaga uma resposta numa cópia em `tmp_path` → `ausentes` com 1 chave e o comando sai 1 com "rode `radar avaliar rodar --modo gravar`"); `test_nao_conferido_vai_para_secao_separada`; `test_validar_acusa_trecho_inexistente`. E o teste do CI de verdade: `test_evals_do_repositorio_em_replay` (roda sobre `evals/` com `modelo_padrao`; `pytest.skip` se `evals/gabarito/` estiver vazio; falha se `ausentes`; com `linha_de_base.json` não vazio, falha se o F1 de algum campo cair mais de 0,05).
- [ ] **Step 3:** rodar `uv run pytest tests/unit/test_evals_metricas.py tests/unit/test_evals_replay.py -v` → FAIL. Implementar.
- [ ] **Step 4:** testes passam; verificação geral → PASS.
- [ ] **Step 5:** commit `feat(analise): evals com gabarito, métricas por campo e replay no CI`.

### Task 10: Infra: bucket, role da análise e agenda

Depende só dos nomes (`radar analisar`, variáveis das Global Constraints). Paralela às Tasks 2–9.

**Files:**
- Create: `infra/prod/analise.tf`
- Modify: `infra/prod/variables.tf` (`modelos_ia`), `infra/prod/coletor.tf` (só o `iam:PassRole` da role do Scheduler passa a incluir a role nova), `infra/prod/outputs.tf` (`bucket_documentos`)
- Test: `tests/unit/test_infra_estatico.py`

**Interfaces:**
- Produces:
  - `variable "modelos_ia"` (`list(string)`, padrão: os quatro ids das Global Constraints) com `validation` que recusa `^(us|eu|apac|global)\.`.
  - `aws_s3_bucket` `radar-documentos-${account_id}` com `aws_s3_bucket_public_access_block` (tudo `true`), `aws_s3_bucket_server_side_encryption_configuration` (AES256), `aws_s3_bucket_lifecycle_configuration` (expira em 180 dias).
  - `aws_iam_role` `radar-analisador-task` (trust `ecs-tasks.amazonaws.com`) com: `bedrock:InvokeModel` em `[for m in var.modelos_ia : "arn:aws:bedrock:${var.regiao}::foundation-model/${m}"]`; `s3:GetObject`, `s3:PutObject` em `${bucket.arn}/*`.
  - `aws_scheduler_schedule` `radar-analisar-diario`: `cron(0 9 * * ? *)`, America/Sao_Paulo, alvo `arn:aws:scheduler:::aws-sdk:ecs:runTask`, `input = jsonencode({Cluster, TaskDefinition = local.familia_coletor, LaunchType = "FARGATE", NetworkConfiguration = {AwsvpcConfiguration = {Subnets, SecurityGroups, AssignPublicIp = "ENABLED"}}, Overrides = {TaskRoleArn = <role nova>, ContainerOverrides = [{Name = "coletor", Command = ["analisar"], Environment = [RADAR_IA_PROVEDOR=bedrock, RADAR_DOCUMENTOS=s3://<bucket>/]}]}})`, mesma role do Scheduler, `retry_policy` igual ao do coletor.
  - Nenhum alarme novo (saída ≠ 0 já cai em `radar-coletor-falhou`); nenhum recurso da lista `PROIBIDOS`.

- [ ] **Step 1:** testes estáticos novos: `test_bedrock_so_foundation_model_in_region` (em `analise.tf`: contém `foundation-model/`; não contém `inference-profile`, `bedrock:*`, `"us.`, `"global.`); `test_modelos_ia_recusa_perfil` (o `validation` de `modelos_ia` existe com a regex); `test_bucket_documentos_privado_e_com_expiracao` (`block_public_acls = true`, `days = 180`); `test_analisar_usa_familia_do_coletor` (`Command` = `["analisar"]` e `TaskDefinition` = `local.familia_coletor`).
- [ ] **Step 2:** rodar `uv run pytest tests/unit/test_infra_estatico.py -v` → FAIL. Escrever o Terraform. `terraform fmt -check` e `terraform init -backend=false && terraform validate` em `infra/prod`.
- [ ] **Step 3:** testes passam.
- [ ] **Step 4:** commit `feat(infra): bucket de documentos, role da análise com Bedrock in-region e agenda das 09:00`.

### Task 11: Corpus e rascunho do gabarito (o coordenador, não um subagente)

Depende da Task 9. Termina num **bloqueio humano**: o Augusto confere.

- [ ] Escolher 12 editais do PNCP com PDF (abertos ou encerrados) das quatro linhas, conforme o spec ("Evals"): pelo menos 2 sem capital nem atestado com quantidade, 1 escaneado; 8 `ajuste`, 4 `teste`.
- [ ] `radar avaliar preparar <id> --conjunto ...` para cada um; conferir os `evals/textos/*.json` (página vazia demais, ordem das páginas).
- [ ] O Claude lê cada texto e escreve `evals/gabarito/<slug>.json` com `conferido: false`, cada valor com trecho e página, **sem rodar nenhum dos modelos avaliados**. `radar avaliar validar` → 0 erros.
- [ ] Commit `docs(evals): corpus de 12 editais e rascunho do gabarito (não conferido)`.
- [ ] **Parar e pedir ao Augusto** que confira cada gabarito contra o PDF (o `url` está no arquivo) e marque `conferido: true`, `conferido_por`, `conferido_em`. Reanotar 3 editais uma semana depois mede a consistência dele; anotar o resultado em `observacoes`.

### Task 12: Rodada real e fechamento (o coordenador, não um subagente)

Depende de todas as anteriores e da conferência do gabarito.

- [ ] `radar avaliar rodar --modo gravar --conjunto todos --teto-usd 1.0` para `amazon.nova-lite-v1:0`, `openai.gpt-oss-120b-1:0` e `qwen.qwen3-32b-v1:0` (e `mistral.ministral-3-14b-instruct` se sobrar teto); commitar `evals/respostas/`. Escolher o modelo padrão pelo F1 do conjunto `ajuste` e custo; reportar o `teste` só no fim, uma vez. Gravar `evals/linha_de_base.json` com o F1 por campo do padrão.
- [ ] `terraform apply` em `infra/prod` (perfil `radar`); `alembic upgrade head` no Neon (pela `DATABASE_URL_UNPOOLED` do `.env.local`, sem imprimi-la).
- [ ] PR para `main`, checks verdes (o replay de evals entre eles), merge, deploy verde.
- [ ] `RunTask` manual com o mesmo override da agenda; conferir no log a linha `analise: ...`, no Neon as linhas de `analise_edital`, no S3 os PDFs, e `GET /api/analises/<id>` com JWT. Conferir que o alarme "coletor não rodou" não mudou de estado e anotar o custo real no Cost Explorer no dia seguinte.
- [ ] `docs/etapas/06-ia-editais.md`: o que foi feito; tabela modelo × F1 por campo × alucinação × custo × latência; o que a rodada real ensinou; **5 perguntas de entrevista** com "Resposta:" vazio (sugestões: por que o LLM não decide a aptidão; como provar que o modelo não inventou a exigência; como rodar evals no CI sem gastar; como trocar de modelo sem mexer no pipeline; por que a análise não é um consumidor da fila).
- [ ] `docs/videos/06-ia-editais.md`: roteiro no formato de `docs/videos/03-coletores.md` (gancho, achado × exigência, ancoragem ao vivo com um trecho inventado, replay no CI, tabela dos modelos, perguntas, fecho).
- [ ] Atualizar `docs/checklist.md` (seção "IA aplicada": extração com pypdf, saída estruturada **sem instructor**, comparação **por regras, sem pgvector**, evals com gabarito) e `~/carreira/estado.md`.
