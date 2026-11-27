# Etapa 7a: histórico de resultados — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trazer 12 meses de contratações do recorte e o resultado (vencedor, valores, desconto) de cada uma, e mostrar na ficha do edital o histórico do órgão e a faixa de desconto dos vencedores.

**Architecture:** `radar.coleta` só fala HTTP com o PNCP (páginas por UF para a carga histórica, itens e resultados como JSON cru); `radar.editais` interpreta, grava e consulta (é dono da tabela `edital`, e o histórico junta edital com resultado); a CLI compõe os dois, como já faz com coleta e registro. A carga histórica é um comando retomável por checkpoint, rodado uma vez como task avulsa do Fargate; os resultados têm agenda diária às 07:30.

**Tech Stack:** Python 3.13, SQLAlchemy 2, Alembic, httpx + tenacity, FastAPI/Pydantic, Typer; React + Astryx no front; Terraform.

**Spec:** `docs/superpowers/specs/2026-11-27-etapa-07-historico-design.md` (seções 7a.1, 7a.2, 7a.4, 7a.5 e "Respostas à revisão do construtor"). 7a.3 (TCE-PE), 7b e 7c ficam fora deste plano.

## Global Constraints

- Import-linter: `radar.coleta`, `radar.editais`, `radar.analise` independentes; `radar.api`/`radar.cli`/`radar.processador` importam só a raiz dos pacotes de domínio (módulos novos de domínio entram na lista `forbidden_modules` do contrato "api e cli importam só a raiz" em `pyproject.toml`).
- Cortesia com o PNCP: User-Agent `radar-licitacoes-estudo (github.com/augusto-spati/radar-licitacoes)`, pausa ≥ 1 s entre requisições, uma requisição por vez, tentativas com espera exponencial em 429/5xx.
- Só grava o que casa com o filtro (Neon grátis, 0,5 GB). Nada de página bruta da carga histórica no banco.
- Desconto de uma contratação: `1 − Σ valorTotalHomologado ÷ Σ valorTotal estimado`, só itens homologados com estimado conhecido (`orcamentoSigiloso` falso e estimado > 0). Item ainda em andamento → contratação `parcial`. Só itens sigilosos → sem desconto. O `percentualDesconto` do PNCP é ignorado.
- Vitória conta uma por contratação: o fornecedor com o maior valor homologado nela.
- Vencedor pessoa física (CPF, 11 dígitos): grava `fornecedor_nome = "Pessoa física"` e `fornecedor_ni = None`.
- Comandos novos não imprimem linha começando com `resultado:` (o alarme "coleta não rodou" conta essa linha no log group do coletor).
- Sem métrica customizada nova (a 10ª fica para o alerta da auditoria).
- Limite de inexequibilidade: 0,75 do estimado (Lei 14.133); 0,70 quando o órgão é estatal da Lei 13.303 (CNPJ da Sanepar `76484013000145` ou da Compesa `09769035000164`).
- Faixas de valor da régua: até 1.000.000; de 1.000.000 a 5.000.000; acima de 5.000.000. Régua com n < 5 marca `poucos_casos` e vem acompanhada da régua só por linha (todas as UFs).
- Front: componentes novos só com o trailer `Contrato-Front: aprovado` no commit (autorizado: caminho B do Augusto); sem rota nova; textos em pt-BR; `npm test`, `npm run lint`, `npm run duplicacao`, `npm run contrato:verificar`, `npm run build`, `npx tsc --noEmit` antes de commitar.
- Backend antes de commitar: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `uv run lint-imports`, `uv run pytest -q`.

## Review Focus

1. Item com `temResultado` verdadeiro mas `/resultados` respondendo 204 ou lista vazia → item gravado sem vencedor, sem erro (Task 3 e Task 4).
2. Contratação com dezenas de itens (pregão de material) → paginação de `/itens` até o fim, sem perder itens nem repetir (Task 3).
3. Carga histórica interrompida no meio de um mês → rodar de novo retoma na página seguinte, sem regravar páginas concluídas (Task 2).
4. Edital de Sanepar/Compesa (fonte de retrato) na ficha → histórico do órgão pelo CNPJ, com limite de 0,70 (Task 6).
5. Órgão sem nenhuma contratação com resultado → API devolve histórico vazio (não 404) e a tela diz "Sem histórico deste órgão no recorte" (Tasks 7 e 9).

---

### Task 1: Esquema (migração 0010) e modelos

**Files:**
- Create: `migrations/versions/0010_historico.py`
- Modify: `src/radar/coleta/modelos.py` (modelo `HistoricoCarga`), `src/radar/editais/modelos.py` (modelos `ResultadoItem`, `ResultadoBusca`)
- Test: `tests/integration/test_migracoes.py` (ou o teste de migração existente: upgrade/downgrade de 0010)

**Interfaces:**
- Produces: tabelas
  - `historico_carga(id, uf text, modalidade int, mes date (1º dia), status text in pendente|em_andamento|concluida|falha, proxima_pagina int default 1, total_paginas int null, registros_lidos int default 0, registros_gravados int default 0, erro text null, atualizada_em timestamptz)`, único `(uf, modalidade, mes)`.
  - `resultado_item(id, fonte text, id_na_fonte text, numero_item int, descricao text, unidade text null, quantidade numeric null, valor_unitario_estimado numeric null, valor_total_estimado numeric null, orcamento_sigiloso bool, situacao text, fornecedor_ni text null, fornecedor_nome text null, fornecedor_porte text null, quantidade_homologada numeric null, valor_unitario_homologado numeric null, valor_total_homologado numeric null, data_resultado timestamptz null)`, único `(fonte, id_na_fonte, numero_item)`, FK para `edital(fonte, id_na_fonte)` com `ON DELETE CASCADE`.
  - `resultado_busca(fonte, id_na_fonte PK composta + FK edital, buscado_em timestamptz, itens int, situacao text in homologada|fracassada|deserta|parcial|em_andamento|sem_itens|anulada, desconto numeric null, valor_estimado_considerado numeric null, valor_homologado_considerado numeric null, vencedor_ni text null, vencedor_nome text null, vencedor_porte text null)`, índice em `(situacao)`.
  - Índice em `edital(orgao_cnpj)` se não existir.
- Modelos SQLAlchemy `HistoricoCarga` (coleta), `ResultadoItem` e `ResultadoBusca` (editais), exportados pela raiz dos pacotes.

- [ ] **Step 1:** teste de migração: upgrade até head cria as três tabelas e o índice; downgrade para 0009 remove.
- [ ] **Step 2:** rodar e ver falhar.
- [ ] **Step 3:** escrever a migração (down_revision `0009`) e os modelos.
- [ ] **Step 4:** rodar e ver passar; rodar a suíte inteira.
- [ ] **Step 5:** commit `feat(historico): esquema da carga histórica e dos resultados (0010)`.

### Task 2: Carga histórica (coleta)

**Files:**
- Modify: `src/radar/coleta/pncp.py` (`ClientePNCP.buscar(..., uf: str | None = None)` — envia `uf` só quando dado)
- Create: `src/radar/coleta/historico.py`
- Modify: `src/radar/coleta/__init__.py` (exportar), `pyproject.toml` (módulo novo na lista proibida para api/cli)
- Test: `tests/unit/test_historico_prefiltro.py`, `tests/integration/test_carga_historica.py`

**Interfaces:**
- Consumes: `ClientePNCP`, `HistoricoCarga` (Task 1), `obter_trava`/`liberar_trava` de `radar.coleta.trava`.
- Produces:
  - `prefiltrar(registros: list[dict[str, Any]], termos: list[str]) -> list[dict[str, Any]]` — mantém o registro cujo `objetoCompra`, sem acento e em minúsculas, contém algum termo (os termos já vêm sem acento e minúsculos de `config/filtro.toml`).
  - `meses_ate(hoje: date, n: int) -> list[date]` — 1º dia dos n meses anteriores ao mês corrente, do mais recente ao mais antigo (o mês corrente fica para a coleta diária).
  - `carregar_historico(sessao: Session, cliente: ClientePNCP, ufs: list[str], modalidades: list[int], meses: list[date], termos: list[str], gravar: Callable[[Session, list[dict[str, Any]]], int], refazer: set[date] = set()) -> ResumoHistorico` — cria as linhas de `historico_carga` que faltam (pendente), percorre as não concluídas, chama `cliente.buscar("publicacao", mes, fim_do_mes, modalidade, pagina, uf=uf)`, grava `gravar(sessao, prefiltrar(...))` e o checkpoint na mesma transação por página (commit por página); erro de PNCP marca `falha` com o erro e segue para a próxima combinação. `refazer` reabre os meses dados.
  - `@dataclass ResumoHistorico: combinacoes: int, concluidas: int, falhas: int, paginas: int, lidos: int, gravados: int`.
  - Trava `historico` (mesma `trava_fonte`) envolvendo a carga.

- [ ] **Step 1:** testes unitários de `prefiltrar` (acento e caixa: "Execução de REDE DE ÁGUA" casa "rede de agua"; "rede lógica" não casa) e de `meses_ate(date(2026,11,27), 2) == [date(2026,10,1), date(2026,9,1)]`.
- [ ] **Step 2:** teste de integração com cliente falso: 2 UFs × 1 modalidade × 1 mês, 2 páginas cada; só os registros que casam chegam ao `gravar`; checkpoint `concluida`; uma segunda chamada não chama o cliente de novo.
- [ ] **Step 3:** teste de retomada (Review Focus 3): o cliente falha na página 2 de uma combinação → `falha` com `proxima_pagina = 2`; segunda chamada recomeça na página 2 e conclui.
- [ ] **Step 4:** teste: `uf` vai na consulta só quando dado (`buscar` sem `uf` não manda o parâmetro).
- [ ] **Step 5:** implementar; rodar os testes; lint-imports.
- [ ] **Step 6:** commit `feat(historico): carga histórica do PNCP por UF, retomável, gravando só o que casa`.

### Task 3: Cliente de itens e resultados (coleta)

**Files:**
- Create: `src/radar/coleta/resultados_pncp.py`
- Modify: `src/radar/coleta/__init__.py`, `pyproject.toml`
- Test: `tests/unit/test_resultados_pncp.py`

**Interfaces:**
- Produces: `class ClienteResultadosPNCP(http: httpx.Client, pausa_s: float = 1.0, tentativas: int = 3, espera_base: float = 5.0)` com
  - `itens_com_resultados(cnpj: str, ano: int, sequencial: int) -> list[dict[str, Any]]` — percorre `GET https://pncp.gov.br/pncp-api/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens?pagina=N&tamanhoPagina=50` até a página vir com menos de 50 (ou 204), e para cada item com `temResultado` verdadeiro chama `.../itens/{numeroItem}/resultados` e põe a lista em `item["resultados"]` (204 ou lista vazia → `[]`). Devolve os itens crus do PNCP.
  - `fechar() -> None`; `criar_http_resultados() -> httpx.Client` (base `https://pncp.gov.br/pncp-api/v1`, UA do projeto, timeout 60 s).
  - Erros: `ErroResultados` para 4xx (exceto 404 → lista vazia de itens) e após esgotar tentativas em 429/5xx/transporte.

- [ ] **Step 1:** testes com `httpx.MockTransport`: paginação de 120 itens (50+50+20) sem repetir (Review Focus 2); item com `temResultado` e resultados 204 → `resultados == []` (Review Focus 1); item sem `temResultado` não chama `/resultados`; 429 seguido de 200 tenta de novo; 404 nos itens → `[]`; 500 três vezes → `ErroResultados`.
- [ ] **Step 2:** implementar (padrão de `ClientePNCP`: tenacity, pausa entre requisições).
- [ ] **Step 3:** rodar; commit `feat(resultados): cliente de itens e resultados do PNCP`.

### Task 4: Interpretar e gravar resultados; escolher alvos (editais)

**Files:**
- Create: `src/radar/editais/resultados.py`
- Modify: `src/radar/editais/__init__.py`, `pyproject.toml`
- Test: `tests/unit/test_resultados_calculo.py`, `tests/integration/test_resultados_gravacao.py`

**Interfaces:**
- Consumes: modelos da Task 1; itens crus no formato da Task 3; `Criterios`, `filtrar`.
- Produces:
  - `@dataclass AlvoResultado: fonte: str, id_na_fonte: str, cnpj: str, ano: int, sequencial: int`.
  - `alvos_resultado(sessao: Session, criterios: Criterios, agora: datetime, limite: int) -> list[AlvoResultado]` — editais `pncp` que casam o filtro (todas as situações, sem filtro de UF além do de `criterios`), com `data_encerramento_proposta < agora`, encerramento há menos de 180 dias, e (sem `resultado_busca`) ou (`buscado_em` < agora − 7 dias e situação em `parcial|em_andamento|sem_itens`); os nunca buscados primeiro, depois os mais antigos; `cnpj/ano/sequencial` saem do `id_na_fonte` (`<cnpj>-1-<seq>/<ano>`).
  - `interpretar_itens(itens: list[dict[str, Any]]) -> list[ItemResultado]` (dataclass com os campos de `resultado_item`; PF mascarada; `situacao` do `situacaoCompraItemNome`).
  - `resumir(itens: list[ItemResultado]) -> ResumoContratacao` (dataclass: `situacao`, `desconto`, `estimado_considerado`, `homologado_considerado`, `vencedor_ni/nome/porte`) — regras de desconto, parcial, sigiloso e vitória das Global Constraints. Situação agregada: nenhum item → `sem_itens`; algum homologado e algum em andamento → `parcial`; todos homologados (ou homologados + fracassados/desertos) → `homologada`; nenhum homologado e algum em andamento → `em_andamento`; todos fracassados → `fracassada`; todos desertos → `deserta`; todos anulados/revogados/cancelados → `anulada`.
  - `gravar_resultados(sessao: Session, fonte: str, id_na_fonte: str, itens: list[ItemResultado], agora: datetime) -> ResumoContratacao` — upsert dos itens e de `resultado_busca`; sem commit.

- [ ] **Step 1:** testes de `resumir` com números: dois itens homologados 100→80 e 50→45 → desconto `0.1667` (Σ 125/150); item sigiloso ignorado no desconto; só sigilosos → `desconto is None`; item em andamento + homologado → `parcial`; vencedor = maior homologado; todos "Fracassado" → `fracassada`.
- [ ] **Step 2:** teste: CPF de 11 dígitos vira "Pessoa física" sem `fornecedor_ni`.
- [ ] **Step 3:** teste de integração de `alvos_resultado`: edital aberto não entra; encerrado há 10 dias sem busca entra; buscado ontem não entra; `parcial` buscado há 8 dias entra; encerrado há 200 dias não entra; edital de outra fonte não entra.
- [ ] **Step 4:** teste de integração de `gravar_resultados`: gravar duas vezes não duplica (upsert) e atualiza a situação.
- [ ] **Step 5:** implementar; rodar; commit `feat(resultados): interpretar, resumir e gravar resultados; alvos da rodada`.

### Task 5: Comandos da CLI

**Files:**
- Modify: `src/radar/cli.py`
- Test: `tests/integration/test_cli_historico.py`

**Interfaces:**
- Consumes: Tasks 2, 3, 4 pela raiz dos pacotes.
- Produces:
  - `radar historico carregar [--meses 12] [--refazer-mes AAAA-MM ...]` — UFs e modalidades de `config/filtro.toml`, termos = união das linhas; `gravar` = `registrar` de `radar.editais` (conta inseridos + atualizados); saída final `historico: C combinações (K concluídas, F falhas), P páginas, L lidos, G gravados`; código 1 se houver falha.
  - `radar resultados atualizar [--limite 300]` — `alvos_resultado` → para cada alvo `itens_com_resultados` → `interpretar_itens` → `gravar_resultados` + commit por edital; erro de um edital loga `resultados_falhou` e segue; saída final `resultados: N editais, M itens, H homologadas, F falhas`; código 1 se houver falha.
  - Funções de fábrica `_criar_cliente_resultados()` (para os testes trocarem).

- [ ] **Step 1:** teste do `resultados atualizar` com cliente falso e banco de teste: grava `resultado_busca` e `resultado_item`; segunda rodada no mesmo instante não busca de novo; a saída não contém `resultado:`.
- [ ] **Step 2:** teste do `historico carregar` com `ClientePNCP` falso: grava só o que casa e imprime o resumo.
- [ ] **Step 3:** implementar; rodar a suíte; commit `feat(cli): radar historico carregar e radar resultados atualizar`.

### Task 6: Consulta do histórico do órgão (editais)

**Files:**
- Create: `src/radar/editais/historico.py`
- Modify: `src/radar/editais/__init__.py`, `pyproject.toml`
- Test: `tests/integration/test_historico_orgao.py`

**Interfaces:**
- Produces: `historico_do_edital(sessao: Session, criterios: Criterios, fonte: str, id_na_fonte: str, agora: datetime) -> HistoricoOrgao | None` (None só se o edital não existe), com dataclasses:
  - `HistoricoOrgao: orgao_cnpj: str | None, orgao_nome: str, contagens: dict[str, int]` (por situação agregada), `total: int, ultimas: list[ContratacaoPassada]` (até 10, por data de resultado ou encerramento decrescente), `vencedores: list[Vencedor]` (top 5 por vitórias, empate por maior soma homologada), `reguas: list[Regua], limite_inexequivel: Decimal`.
  - `ContratacaoPassada: id_publico, objeto, data (encerramento), situacao, valor_estimado, valor_homologado, desconto, parcial: bool, vencedor_nome, vencedor_porte`.
  - `Vencedor: nome, porte, vitorias: int, desconto_mediano: Decimal | None`.
  - `Regua: linha: str | None, uf: str | None, faixa: str | None, casos: int, p25, mediana, p75: Decimal | None, poucos_casos: bool`.
  - Recorte do órgão: editais `pncp` com o mesmo `orgao_cnpj`, que casam qualquer linha do filtro, com `resultado_busca`, encerramento nos últimos 24 meses. Régua: uma por linha do edital (edital sem linha → uma régua `linha=None` de todas as linhas), na UF e faixa de valor do edital, só contratações `homologada` com desconto; se `casos < 5`, acrescenta a régua da mesma linha sem UF e sem faixa. Percentis por interpolação linear (`statistics.quantiles(n=4, method="inclusive")`).

- [ ] **Step 1:** testes com dados montados: órgão com 3 homologadas, 1 fracassada → contagens e `ultimas` certas; vencedor com 2 vitórias aparece primeiro; régua com 6 casos dá p25/mediana/p75 esperados; com 3 casos vem `poucos_casos` + régua só da linha; edital `sanepar~` usa o CNPJ da Sanepar e limite `0.70` (Review Focus 4); órgão sem nada → `total == 0`, listas vazias (Review Focus 5).
- [ ] **Step 2:** implementar; rodar; commit `feat(historico): histórico do órgão, vencedores e régua de desconto`.

### Task 7: API `GET /api/historicos/{id_publico:path}`

**Files:**
- Modify: `src/radar/api/modelos.py` (`HistoricoOut` e submodelos), `src/radar/api/app.py`, `frontend/openapi.json` (via `uv run python scripts/exportar_openapi.py`)
- Test: `tests/integration/test_api_historico.py` (ou o arquivo de testes da API existente)

**Interfaces:**
- Produces: `GET /api/historicos/{id_publico:path}` → 200 `HistoricoOut` (espelho das dataclasses da Task 6; números como string decimal como `valor_estimado` já é; datas ISO com fuso), 404 se o edital não existe. Rota separada de `/api/editais/{id_publico:path}` porque o conversor `path` engoliria um sufixo.

- [ ] **Step 1:** testes: 200 com histórico; 200 com histórico vazio; 404 para edital inexistente; id com `/` funciona.
- [ ] **Step 2:** implementar; regenerar `frontend/openapi.json`; `test_openapi_atualizado` passa; commit `feat(api): histórico do órgão por edital`.

### Task 8: Agenda diária de resultados (infra)

**Files:**
- Modify: `infra/prod/coletor.tf` (schedule `radar-resultados-diario`, `cron(30 7 * * ? *)`, `America/Sao_Paulo`, mesmo alvo e role do `atualizar_diario`, command `["resultados", "atualizar"]`), `tests/unit/test_infra_estatico.py`
- Test: teste estático novo

- [ ] **Step 1:** teste estático: existe o schedule com o cron e o command; continua sem `pncp` nos commands do coletor de retratos.
- [ ] **Step 2:** implementar; `terraform fmt -check` e `validate`; commit `feat(infra): agenda diária dos resultados às 07:30`.

### Task 9: Tela "Histórico do órgão"

**Files:**
- Modify: `frontend/src/dados/tipos.ts`, `fonte.ts`, `fonteApi.ts`, `fonteExemplo.ts`, `frontend/src/testes/apoio.tsx`, `frontend/src/paginas/Detalhe.tsx`, `frontend/src/componentes.test.ts`, `frontend/src/dados/api.d.ts` (`npm run contrato`)
- Create: `frontend/src/componentes/HistoricoOrgao.tsx`, `frontend/src/componentes/ReguaDesconto.tsx`
- Test: `frontend/src/paginas/Detalhe.test.tsx`, `frontend/src/dados/fonte.test.ts`

**Interfaces:**
- Consumes: `GET /api/historicos/{id}` (Task 7).
- Produces: `Fonte.historico(id: string): Promise<Historico | null>` (404 → null; exemplo → null); seção "Histórico do órgão" abaixo de "Aderência à FR", carregada à parte (falha não derruba a página; "Tentar de novo").
- Conteúdo (spec 7a.5): contagens ("12 licitações · 8 homologadas · 3 fracassadas ou desertas · 1 em andamento"), "Quem vence" (nome, porte, vitórias), últimas contratações (objeto, data, estimado, homologado, desconto, vencedor; "parcial" marcado), régua por linha ("Faixa de desconto dos vencedores (redes, SP, 1–5 mi): 18% a 31% (mediana 24%), 14 casos"; "poucos casos" quando marcado) e o limite de inexequibilidade ("Abaixo de 75% do estimado a proposta é presumida inexequível"). Vazio: "Sem histórico deste órgão no recorte."
- Commit com o trailer `Contrato-Front: aprovado` (componentes novos na lista fechada).

- [ ] **Step 1:** testes de tela: com histórico mostra contagens, vencedor, régua com mediana e casos, limite; vazio mostra a frase; erro oferece "Tentar de novo" e não derruba o edital; `fonteApi.historico` com 404 → null.
- [ ] **Step 2:** implementar com componentes Astryx já usados (List/ListItem, Token, Text, MetadataList); sem HTML cru.
- [ ] **Step 3:** rodar toda a bateria do front; `npx astryx doctor --built dist`; commit com o trailer.

### Task 10: Documentação

**Files:**
- Create: `docs/etapas/07-historico.md`, `docs/videos/07-historico.md`
- Modify: `docs/checklist.md` (itens da etapa 7), `docs/etapas/06-ia-editais.md` (referência cruzada, se couber)

- [ ] **Step 1:** doc da etapa no padrão das outras (o que foi feito, por quê, números medidos da pesquisa: 66% com resultado, mediana 24,5%, campo de desconto do PNCP zerado em 129/132; como rodar a carga; o que ficou para 7b/7c/TCE-PE; perguntas de entrevista).
- [ ] **Step 2:** roteiro do vídeo; checklist; commit `docs: etapa 7a, histórico de resultados`.
