# Etapa 08a: painel de leitura — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tela inicial (`/`) vira o Painel de leitura: contadores, seções Decidir e Mais adiante, selo "novo", aderência só com perfil real e faixa de coleta quando algo falhou; a Lista vai para `/editais` e aceita os filtros dos contadores.

**Architecture:** `radar.editais` ganha `primeiro_visto_em` (só na inserção), veredito no resumo por `LEFT JOIN LATERAL` em `analise_edital` (tabela referida por `sqlalchemy.table()`, sem importar `radar.analise`), o `Recorte` da Lista e a separação pura do painel (`editais/painel.py`). `radar.analise` declara os campos novos do `Perfil` e grava `perfil_resumo` em `analisar`/`recomparar`. `radar.coleta` diz que fontes estão atrasadas ou falharam. A API compõe tudo em `GET /api/painel`; o front lê por `useFonte`/`useCarga`, com `fonteExemplo` seguindo as mesmas regras.

**Tech Stack:** Python 3.13, SQLAlchemy 2, Alembic, FastAPI/Pydantic, Typer, pytest + testcontainers (Postgres 17); React 19 + Vite + Astryx 0.7.17, Vitest, Playwright 1.65.1.

**Spec:** `docs/superpowers/specs/2026-11-28-painel-design.md` (só a fase 08a: "Back-end 08a", "Filtros da Lista" para os contadores, "Front", linhas 08a do inventário, "Fila de análise por prazo"). Triagem (08b) e prioridade/ordem (08c) ficam fora.

## Global Constraints

- Repositório público: nada do perfil real nem da empresa no código, nos exemplos, nos testes ou nos docs. Perfis de teste são fictícios; `config/perfil.toml` continua "PERFIL DE EXEMPLO" e ganha `exemplo = true` em `[empresa]`.
- Qualidade Python = comandos do CI: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `PYTHONIOENCODING=utf-8 uv run lint-imports`, `uv run pytest -v` (integração sobe Postgres 17 por testcontainers; precisa do Docker).
- Import-linter: `coleta`, `editais`, `analise`, `precos` independentes; `api`/`cli` importam só a raiz dos pacotes. Módulo novo de domínio entra em `forbidden_modules` do contrato "api e cli importam só a raiz" em `pyproject.toml` (`radar.editais.painel`, `radar.analise.perfil_resumo`). `perfil_resumo` mora em `radar.analise` (quem grava é a análise); a API lê pela raiz `radar.analise`. `editais` referencia `analise_edital` só por `sqlalchemy.table("analise_edital", column(...))`, nunca por import.
- `radar.cli` não importa `radar.analise` ao carregar (`test_cli_nao_importa_a_analise_ao_carregar`): imports da análise ficam dentro dos comandos.
- Fuso: colunas de data do edital (`data_encerramento_proposta`, `data_publicacao`) são horário de Brasília **sem fuso**; compare com `agora.astimezone(FUSO).replace(tzinfo=None)`. `primeiro_visto_em`, `visto_em`, `criada_em` e as colunas da coleta são `timestamptz`; compare com `datetime` com fuso. Saída da API: sem fuso → `_iso`; `timestamptz` → `iso_com_fuso`.
- Nos testes, `primeiro_visto_em` vem do `now()` do banco (relógio real, não o `AGORA` do teste): fixe-o com `UPDATE` quando o teste depender dele.
- Neon derruba sessão ociosa em transação: nada de transação aberta durante rede ou IA; gravação curta seguida de `commit` (ou `rollback` depois de leitura).
- A API nunca devolve o texto cru de `erro` da coleta (pode trazer URL e detalhe interno).
- Front (`frontend/CLAUDE.md`, contrato obrigatório): sem HTML cru fora de `src/componentes/base/`, sem `style`/`className`, sem dependência nova, dados por `useFonte`/`useCarga`, datas por `src/dados/formatos.ts`, jscpd com threshold 0 (reaproveite, não copie), filtros na URL, textos em pt-BR, cabe em 390 px sem rolagem horizontal. Antes de usar um componente Astryx: `npx astryx component <Nome>` (Collapsible, ClickableCard, Banner, Token).
- Estilo do front: não há config de prettier no repo; o código segue `--single-quote --no-bracket-spacing --print-width 100 --arrow-parens avoid`. Se rodar prettier, só `npx prettier@3 --check` com essas opções, sem adicionar dependência nem arquivo de config.
- Verificação do front (dentro de `frontend/`, depois de `npm ci`): `npm test`, `npm run lint`, `npm run duplicacao`, `npm run contrato:verificar`, `npx tsc --noEmit`, `npm run build`.
- Trailer `Contrato-Front: aprovado` (aprovado pelo Augusto no inventário 08a do spec) só nos commits que tocam `rotas.tsx`, `rotas.test.ts`, `frontend/CLAUDE.md`, `componentes.test.ts` e `frontend/e2e/`; nada de guardado fora desse inventário (nada de `package.json`, `eslint.config.js`, `.jscpd.json`, `Shell.tsx`, workflows).
- Todo commit termina com o bloco de trailers (o `Contrato-Front` entra no mesmo bloco, quando couber):
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_012v6r8uz1aiPHNQoDZ66AVf
  ```
- Sem push, sem `git stash` sem nome. A migração 0015 em produção (Neon) e o deploy são feitos depois do merge, com o Augusto; não fazem parte deste plano.

## Review Focus

1. Edital que encerra no 20º dia de calendário às 23:59 (Brasília) entra em Decidir, no `encerra_em_dias=20` da Lista e no contador; o de 00:00 do 21º dia não. Contador e Lista filtrada dão o mesmo número (Tasks 2, 5, 6).
2. Edital que já existia antes da migração (PNCP sem `visto_em`, Compesa sem `data_publicacao`, publicado ontem) não aparece como novo; upsert com conteúdo novo, versão mais nova do PNCP e reconciliação de retrato não renovam `primeiro_visto_em` (Task 1).
3. Perfil de exemplo (sem linha em `perfil_resumo` ou `exemplo = true`): inapto não some de Decidir, o link do contador não filtra por veredito e nenhum selo de aderência aparece, no Painel e na Lista (Tasks 5, 6, 8).
4. Link antigo `#/?linha=redes&uf=SP` (favorito, e-mail) abre a Lista com os mesmos filtros, e o Voltar do detalhe volta para `/editais?...` (Tasks 8, 9, 10).
5. Fonte parada há mais de 36 h ou que nunca rodou aparece na faixa; fonte fresca com última leitura ok não aparece, para a faixa não virar paisagem (Tasks 4, 8).

## Ordem e paralelismo

- Task 1 primeiro (esquema e modelos de que todas dependem).
- Depois da 1, em paralelo: Task 2 (`editais/filtro.py`), Task 3 (`analise/*`, `cli.py`, `config/perfil.toml`) e Task 4 (`coleta/frescor.py`). Arquivo compartilhado: `pyproject.toml` (Tasks 3 e 5 acrescentam uma linha cada na mesma lista; quem chegar depois faz rebase da linha).
- Task 5 depois da 2 (pode correr junto com a 3 e a 4).
- Task 6 depois de 2, 3, 4 e 5.
- Front em série: 7 → 8 → 9 → 10 (cada uma usa os tipos e componentes da anterior).

---

### Task 1: Esquema 0015 (primeiro_visto_em, índice da análise, perfil_resumo) e upsert protegido

**Files:**
- Create: `migrations/versions/0015_painel.py`
- Modify: `src/radar/editais/modelos.py` (`Edital`), `src/radar/editais/registro.py` (`_upsert`), `src/radar/analise/modelos.py` (`AnaliseEdital.__table_args__`, classe nova `PerfilResumo`), `src/radar/analise/__init__.py` (exporta `PerfilResumo`), `tests/conftest.py` (recebe a fixture `banco_migracao`), `tests/integration/test_multifonte.py` (perde a cópia local da fixture)
- Test: `tests/integration/test_painel_esquema.py` (novo), `tests/unit/test_cadeia_migracoes.py`, `tests/integration/test_esquema.py` (sem mudança; tem de passar)

**Interfaces:**
- Produces: coluna `Edital.primeiro_visto_em: Mapped[datetime]` (`DateTime(timezone=True)`, `server_default=func.now()`, não nula); constante `_SO_NA_INSERCAO = frozenset({"primeiro_visto_em"})` em `registro.py`, excluída do `set_` do `ON CONFLICT`; índice `ix_analise_edital_ultima` em `analise_edital(fonte, id_na_fonte, modelo, criada_em, id)`; tabela `perfil_resumo(id smallint pk default 1 check id = 1 [ck_perfil_resumo_uma_linha], exemplo bool not null, uf_sede char(2) null, valor_maximo_obra numeric null, atualizado_em timestamptz not null)` e o modelo `PerfilResumo` (com `autoincrement=False` no `id`).
- Migração: `revision = "0015"`, `down_revision = "0014"`. Upgrade: `add_column` nula → `UPDATE edital SET primeiro_visto_em = least(visto_em, data_publicacao AT TIME ZONE 'America/Sao_Paulo', now() - interval '30 days')` → `NOT NULL` + `server_default now()`; índice; tabela. Downgrade desfaz na ordem inversa.

- [ ] **Step 1: Mover a fixture `banco_migracao` de `test_multifonte.py` para `tests/conftest.py`** (mesmo corpo) e conferir que `uv run pytest tests/integration/test_multifonte.py -v` continua passando.

- [ ] **Step 2: Escrever os testes que falham**

Em `tests/integration/test_painel_esquema.py`:
```python
def test_backfill_primeiro_visto_em_nunca_parece_novo(banco_migracao):
    # upgrade 0014; INSERT cru de 3 editais:
    #   pncp "recente": visto_em NULL, data_publicacao '2026-11-27 10:00'
    #   compesa "sem_publicacao": visto_em = now(), data_publicacao NULL
    #   pncp "antigo": visto_em NULL, data_publicacao '2025-01-10 09:00'
    # upgrade 0015; lê now() do banco como `agora_db`
    assert recente <= agora_db - timedelta(days=30)
    assert sem_publicacao <= agora_db - timedelta(days=30)
    assert antigo == datetime(2025, 1, 10, 9, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert not colunas["primeiro_visto_em"]["nullable"]

def test_downgrade_0015_volta_e_sobe_de_novo(banco_migracao):
    # upgrade head; downgrade 0014
    assert "primeiro_visto_em" not in colunas_edital
    assert "perfil_resumo" not in inspetor.get_table_names()
    assert "ix_analise_edital_ultima" not in indices_analise
    # upgrade head sem erro

def test_edital_novo_ganha_primeiro_visto_em(sessao): ...          # não nulo após registrar()
def test_upsert_com_conteudo_novo_nao_muda_primeiro_visto_em(sessao):
    # registrar_normalizados; UPDATE primeiro_visto_em = '2026-10-01 12:00-03'; de novo com objeto diferente
    assert primeiro_visto_em == datetime(2026, 10, 1, 12, 0, tzinfo=FUSO)
    assert edital.objeto == "objeto novo"                         # o upsert aconteceu
def test_versao_mais_nova_do_pncp_nao_muda_primeiro_visto_em(sessao): ...   # registrar com dataAtualizacaoGlobal maior
def test_reconciliar_retrato_nao_muda_primeiro_visto_em(sessao): ...        # encerrado, mesma data
def test_perfil_resumo_so_tem_uma_linha(sessao):
    with pytest.raises(IntegrityError): ...                         # INSERT com id = 2
```
Em `tests/unit/test_cadeia_migracoes.py`: renomear para `test_cadeia_linear_ate_o_painel`, com `script.get_heads() == ["0015"]` e `script.get_revision("0015").down_revision == "0014"` (mantém as asserções antigas).

- [ ] **Step 3: Rodar e ver falhar**

Run: `uv run pytest tests/integration/test_painel_esquema.py tests/unit/test_cadeia_migracoes.py -v`
Expected: FAIL (revisão 0015 inexistente, coluna ausente).

- [ ] **Step 4: Implementar** a migração, os modelos e o filtro `c.name not in _SO_NA_INSERCAO` no dicionário `colunas_novas` de `_upsert`. Índice em ordem ascendente: o Postgres varre o btree de trás para frente e serve o `ORDER BY criada_em DESC, id DESC` de `obter_analise`, sem índice de expressão (que o `alembic check` compara mal).

- [ ] **Step 5: Rodar e ver passar**

Run: `uv run pytest tests/integration/test_painel_esquema.py tests/unit/test_cadeia_migracoes.py tests/integration/test_esquema.py tests/integration/test_multifonte.py tests/integration/test_registrar.py -v`
Expected: PASS (inclusive `test_modelos_e_migracoes_sem_diferencas`).

- [ ] **Step 6: Commit**

```bash
git add migrations/versions/0015_painel.py src/radar/editais/modelos.py src/radar/editais/registro.py src/radar/analise/modelos.py src/radar/analise/__init__.py tests/conftest.py tests/integration/test_multifonte.py tests/integration/test_painel_esquema.py tests/unit/test_cadeia_migracoes.py
git commit -m "feat(painel): primeiro_visto_em só na inserção, índice da última análise e perfil_resumo" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012v6r8uz1aiPHNQoDZ66AVf"
```

### Task 2: Veredito no resumo e recorte da Lista (editais)

**Files:**
- Modify: `src/radar/editais/filtro.py`, `src/radar/editais/__init__.py`
- Test: `tests/integration/test_filtro.py`

**Interfaces:**
- Consumes: `Edital.primeiro_visto_em`, índice `ix_analise_edital_ultima` (Task 1).
- Produces:
  - `EditalResumo` ganha `veredito: str | None`, `motivo_verificar: str | None`, `primeiro_visto_em: datetime | None` (sem default; `_campos_resumo` preenche).
  - `FiltroVeredito = Literal["apto", "verificar", "inapto", "sem_analise"]`; `DIAS_NOVO = 3`.
  - `@dataclass(frozen=True) class Recorte: encerra_em_dias: int | None = None; novos: bool = False; vereditos: frozenset[str] = frozenset()` (vazio = todos).
  - `filtrar(sessao, criterios, agora, somente_abertos=True, limite: int | None = 200, modelo_analise: str | None = None, recorte: Recorte = Recorte()) -> list[EditalResumo]` (`limite=None` = sem `LIMIT`).
  - `obter_edital(sessao, fonte, id_na_fonte, criterios, modelo_analise: str | None = None)` e `listar_abertos(..., modelo_analise: str | None = None)` trazem o mesmo veredito.
  - Regras: veredito = o da análise mais nova do `modelo_analise` (`criada_em desc, id desc limit 1`, lateral); sem modelo, tudo nulo. `motivo_verificar = jsonb_path_query_first(comparacao, '$.itens[*] ? (@.situacao == "verificar").campo') #>> '{}'` (o `campo` do primeiro item `verificar`). `encerra_em_dias=N`: `data_encerramento_proposta < início do dia (Brasília) de hoje + N + 1`. `novos`: `primeiro_visto_em >= agora - DIAS_NOVO dias`. `sem_analise` casa veredito nulo.

- [ ] **Step 1: Escrever os testes que falham** (helper local `_analise(sessao, id_na_fonte, veredito, criada_em, modelo="m", itens=())` que grava `radar.analise.AnaliseEdital`; `AGORA = 26/11/2026 12:00 Brasília`)

```python
def test_resumo_traz_veredito_da_analise_mais_nova_do_modelo(sessao):
    # "m" apto em 20/11, "m" inapto em 25/11, "outro" verificar em 26/11
    assert filtrar(sessao, CRITERIOS, AGORA, modelo_analise="m")[0].veredito == "inapto"
def test_empate_de_criada_em_fica_com_o_maior_id(sessao): ...
def test_motivo_verificar_e_o_campo_do_primeiro_item_verificar(sessao):
    # itens: atende capital_social_minimo, verificar atestados, verificar documentos
    assert resumo.motivo_verificar == "atestados"
def test_sem_analise_ou_sem_modelo_veredito_nulo(sessao):
    assert (resumo.veredito, resumo.motivo_verificar) == (None, None)
def test_encerra_em_dias_conta_dias_de_calendario_em_brasilia(sessao):
    # encerramentos: 16/12 23:59 (20º dia) e 17/12 00:00 (21º dia)
    assert ids(Recorte(encerra_em_dias=20)) == ["dia20/2026"]
def test_encerra_em_dias_zero_e_so_hoje(sessao): ...           # 26/11 18:00 entra, 27/11 08:00 não
def test_novos_sao_os_vistos_ha_ate_3_dias(sessao):
    # primeiro_visto_em = AGORA - 3 dias (entra) e AGORA - 3 dias - 1 min (não)
def test_vereditos_filtra_e_sem_analise_casa_nulo(sessao):
    assert ids(Recorte(vereditos=frozenset({"apto", "sem_analise"}))) == [...]
def test_limite_nulo_traz_todos(sessao):
    assert len(filtrar(sessao, CRITERIOS, AGORA, limite=None)) == 3
    assert len(filtrar(sessao, CRITERIOS, AGORA, limite=2)) == 2
def test_obter_edital_traz_veredito_e_primeiro_visto_em(sessao): ...
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/integration/test_filtro.py -v`
Expected: FAIL (`modelo_analise`/`Recorte` desconhecidos).

- [ ] **Step 3: Implementar** em `filtro.py`: `_ANALISE = table("analise_edital", column("id"), column("fonte"), column("id_na_fonte"), column("modelo"), column("criada_em"), column("veredito"), column("comparacao", JSONB))`; helper `_colunas_analise(modelo: str | None) -> tuple[list[Any], Lateral | None]` (sem modelo devolve `null().label(...)` e nenhum join; com modelo, `outerjoin(lateral, true())`); helper `_condicoes_recorte(recorte, agora) -> list[ColumnElement[bool]]`. A ordem por encerramento continua igual.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/integration/test_filtro.py tests/integration/test_multifonte.py tests/integration/test_exportar_exemplo.py -v` e `PYTHONIOENCODING=utf-8 uv run lint-imports`
Expected: PASS; contratos do import-linter mantidos.

- [ ] **Step 5: Commit** (`git add src/radar/editais/filtro.py src/radar/editais/__init__.py tests/integration/test_filtro.py`; mensagem `feat(editais): veredito da última análise no resumo e recorte por prazo, novos e veredito` + trailers).

### Task 3: Perfil declarado, perfil_resumo gravado por analisar e recomparar, fila por prazo conferida

**Files:**
- Create: `src/radar/analise/perfil_resumo.py`
- Modify: `src/radar/analise/perfil.py`, `src/radar/analise/__init__.py`, `config/perfil.toml`, `src/radar/cli.py` (`analisar`, `recomparar`), `pyproject.toml` (`"radar.analise.perfil_resumo"` na lista proibida para api/cli)
- Test: `tests/unit/test_comparacao.py`, `tests/integration/test_perfil_resumo.py` (novo), `tests/integration/test_cli_analisar.py`

**Interfaces:**
- Consumes: modelo `PerfilResumo` (Task 1).
- Produces:
  - `Perfil` ganha `exemplo: bool = False`, `uf_sede: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")`, `valor_maximo_obra: Decimal | None = None`; `ler_perfil` converte `valor_maximo_obra` float→`Decimal(str())` como os outros e loga `perfil_campo_desconhecido` (extra `campos`: só os nomes, nunca valores) para chave de `[empresa]` que o `Perfil` não declara. Sem `extra="forbid"`: o perfil privado no S3 não pode quebrar por campo a mais.
  - `@dataclass(frozen=True) class ResumoDoPerfil: exemplo: bool; uf_sede: str | None; valor_maximo_obra: Decimal | None; atualizado_em: datetime`
  - `gravar_perfil_resumo(sessao: Session, perfil: Perfil, agora: datetime) -> None` (upsert da linha `id = 1`; sem commit) e `ler_perfil_resumo(sessao: Session) -> ResumoDoPerfil | None`, exportados pela raiz `radar.analise`.
  - `radar analisar`: grava e commita logo depois de obter a trava, antes de montar os alvos e de qualquer rede. `radar recomparar`: grava e commita depois de `recomparar_analises`. Imports dentro dos comandos.
  - Fila da análise: `_alvos_analise` já usa `filtrar` (ordem por `data_encerramento_proposta` crescente) com a reserva de 3 vagas da Sanepar (`_candidatos_por_fonte`). É só ordenação e já é por prazo: **sem mudança de código**, só o teste que fixa.

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/unit/test_comparacao.py
def test_perfil_de_exemplo_carrega():          # acrescentar
    assert perfil.exemplo is True and perfil.uf_sede is None and perfil.valor_maximo_obra is None
def test_perfil_campos_da_sede_e_capacidade(tmp_path):
    # exemplo = false, uf_sede = "SP", valor_maximo_obra = 2500000.5
    assert (perfil.exemplo, perfil.uf_sede, perfil.valor_maximo_obra) == (False, "SP", Decimal("2500000.5"))
def test_uf_sede_invalida_e_erro(tmp_path):
    with pytest.raises(ValidationError): ...   # uf_sede = "Paraná"
def test_campo_desconhecido_no_perfil_avisa(tmp_path, caplog):
    assert any(r.message == "perfil_campo_desconhecido" for r in caplog.records)

# tests/integration/test_perfil_resumo.py
def test_sem_linha_le_none(sessao): assert ler_perfil_resumo(sessao) is None
def test_gravar_cria_e_depois_atualiza_a_mesma_linha(sessao):
    # grava perfil exemplo, depois perfil com exemplo=False, uf_sede="SP"
    assert sessao.scalar(select(func.count()).select_from(PerfilResumo)) == 1
    assert (lido.exemplo, lido.uf_sede) == (False, "SP")

# tests/integration/test_cli_analisar.py
def test_analisar_grava_o_resumo_do_perfil(rede, config_filtro):
    assert lido is not None and lido.exemplo is True          # config/perfil.toml
def test_recomparar_grava_o_resumo_do_perfil(rede, tmp_path, monkeypatch):
    # RADAR_CONFIG_PERFIL -> perfil fictício com exemplo = false, uf_sede = "SP", valor_maximo_obra = 3000000
    assert (lido.exemplo, lido.uf_sede, lido.valor_maximo_obra) == (False, "SP", Decimal("3000000"))
def test_fila_da_analise_vai_pelo_prazo_mais_curto(rede, config_filtro):
    # dois PNCP abertos: encerra 2026-12-20 e 2026-12-01; --limite 1 --fontes pncp
    assert _analisados(fabrica) == {"<o de 01/12>"}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/test_comparacao.py tests/integration/test_perfil_resumo.py tests/integration/test_cli_analisar.py -v`
Expected: FAIL nos novos (o da fila deve PASSAR já; se falhar, a fila não é por prazo e o spec manda corrigir a ordenação aqui).

- [ ] **Step 3: Implementar** `perfil.py`, `perfil_resumo.py`, os exports, a linha `exemplo = true` em `config/perfil.toml` (com comentário "true no perfil de exemplo; o perfil real omite ou põe false") e as duas chamadas no `cli.py`.

- [ ] **Step 4: Rodar e ver passar**

Run: o mesmo do Step 2, mais `uv run pytest tests/unit/test_estrutura.py tests/integration/test_pipeline_analise.py -v` e `PYTHONIOENCODING=utf-8 uv run lint-imports`
Expected: PASS.

- [ ] **Step 5: Commit** (arquivos acima; `feat(analise): perfil declara exemplo, uf_sede e valor_maximo_obra e perfil_resumo gravado pela análise` + trailers).

### Task 4: Falhas de coleta por fonte (coleta)

**Files:**
- Modify: `src/radar/coleta/frescor.py`, `src/radar/coleta/__init__.py`
- Test: `tests/integration/test_frescor.py`

**Interfaces:**
- Produces:
  - `LIMITE_ATRASO_H = 36.0` (o mesmo do `AvisoDesatualizado` do front).
  - `@dataclass(frozen=True) class FalhaColeta: fonte: str; motivo: Literal["atrasado", "falha"]; dados_de: datetime | None` (`dados_de` = o dado mais novo que chegou a edital, o mesmo de `frescor_por_fonte`; nulo sem dado).
  - `falhas_de_coleta(sessao: Session, agora: datetime, fontes: Iterable[str]) -> list[FalhaColeta]`, na ordem de `fontes`, só as com problema: `atrasado` se o frescor passa de 36 h (inclusive sem dado nenhum); senão `falha` se a última leitura terminou em falha (retrato: último `coleta_retrato` não `em_andamento`, como `ultimo_retrato`; PNCP: última `coleta_janela` de `atualizacao` com status `concluida` ou `falha`, por `coalesce(concluida_em, iniciada_em) desc, id desc`). Sem texto de erro.

- [ ] **Step 1: Escrever os testes que falham** (reusa `_retrato`, `_janela`)

```python
def test_fonte_fresca_e_ok_nao_aparece(sessao): assert falhas_de_coleta(sessao, AGORA, ["sanepar"]) == []
def test_fonte_parada_ha_mais_de_36h_e_atrasada(sessao):
    # retrato concluído e processado há 37 h
    assert falhas == [FalhaColeta("sanepar", "atrasado", AGORA - timedelta(hours=37))]
def test_36h_exatas_nao_e_atrasada(sessao): ...
def test_fonte_que_nunca_rodou_e_atrasada_sem_data(sessao):
    assert falhas == [FalhaColeta("compesa", "atrasado", None)]
def test_ultima_leitura_em_falha_com_dado_fresco(sessao):
    # concluído há 2 h, falha há 1 h
    assert falhas == [FalhaColeta("sanepar", "falha", AGORA - timedelta(hours=2))]
def test_pncp_com_ultima_janela_em_falha(sessao): ...
```

- [ ] **Step 2: Rodar e ver falhar** — Run: `uv run pytest tests/integration/test_frescor.py -v` — Expected: FAIL (`falhas_de_coleta` não existe).
- [ ] **Step 3: Implementar** reaproveitando `_mais_novo_pncp`/`_mais_novo_retrato`.
- [ ] **Step 4: Rodar e ver passar** — mesmo comando — Expected: PASS.
- [ ] **Step 5: Commit** (`feat(coleta): fontes atrasadas ou com a última leitura em falha` + trailers).

### Task 5: Separação do painel (editais, função pura)

**Files:**
- Create: `src/radar/editais/painel.py`
- Modify: `src/radar/editais/__init__.py`, `pyproject.toml` (`"radar.editais.painel"` na lista proibida para api/cli)
- Test: `tests/unit/test_painel.py` (novo)

**Interfaces:**
- Consumes: `EditalResumo` com `veredito`/`primeiro_visto_em`, `DIAS_NOVO` (Task 2).
- Produces:
  - `DIAS_DECIDIR = 20`, `LIMITE_SECAO = 30`, `ChaveSecao = Literal["decidir", "mais_adiante"]`.
  - `dias_ate_encerrar(encerramento: datetime, agora: datetime) -> int` (dias de calendário de Brasília; `encerramento` sem fuso, `agora` com fuso; mesma conta do `formatarPrazo` do front).
  - `eh_novo(primeiro_visto_em: datetime | None, agora: datetime) -> bool` (`>= agora - DIAS_NOVO dias`; nulo é falso).
  - `@dataclass(frozen=True) class Secao: chave: ChaveSecao; total: int; editais: list[EditalResumo]`
  - `@dataclass(frozen=True) class Painel: secoes: list[Secao]; decidir: int; novos: int; abertos: int`
  - `separar_painel(editais: list[EditalResumo], agora: datetime, considerar_veredito: bool, limite_secao: int = LIMITE_SECAO) -> Painel`: entrada já é a base dos abertos em ordem de prazo; com `considerar_veredito`, inapto fica fora das seções (conta em `abertos`); cada edital numa seção só, Decidir se `dias_ate_encerrar <= 20`, senão Mais adiante; `total` conta antes do corte; `novos` e `abertos` contam a base inteira; `decidir == secoes[0].total`.

- [ ] **Step 1: Escrever os testes que falham** (factory local de `EditalResumo`, `AGORA = 26/11/2026 12:00 Brasília`)

```python
def test_dias_ate_encerrar_em_calendario_de_brasilia():
    assert dias_ate_encerrar(datetime(2026, 12, 16, 23, 59), AGORA) == 20
    assert dias_ate_encerrar(datetime(2026, 12, 17, 0, 0), AGORA) == 21
    assert dias_ate_encerrar(datetime(2026, 11, 26, 18, 0), AGORA) == 0
def test_eh_novo_ate_3_dias_inclusive():
    assert eh_novo(AGORA - timedelta(days=3), AGORA) and not eh_novo(AGORA - timedelta(days=3, minutes=1), AGORA)
    assert not eh_novo(None, AGORA)
def test_decidir_ate_20_dias_e_mais_adiante_depois(): ...
def test_inapto_fora_das_secoes_mas_conta_em_abertos():
    assert (painel.decidir, painel.abertos) == (1, 2)
def test_sem_considerar_veredito_inapto_entra_em_decidir(): ...
def test_cada_edital_numa_secao_so(): ...      # ids das seções sem repetição, soma == abertos sem inaptos
def test_secao_cortada_no_limite_mas_total_conta_todos():
    secao = separar_painel(cinco_em_decidir, AGORA, True, limite_secao=2).secoes[0]
    assert (secao.total, len(secao.editais)) == (5, 2)
def test_novos_contam_a_base_inteira(): ...    # inclui inapto e mais adiante
```

- [ ] **Step 2: Rodar e ver falhar** — Run: `uv run pytest tests/unit/test_painel.py -v` — Expected: FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar e ver passar** — o mesmo, mais `PYTHONIOENCODING=utf-8 uv run lint-imports` — Expected: PASS.
- [ ] **Step 5: Commit** (`feat(editais): separação do painel em Decidir e Mais adiante` + trailers).

### Task 6: API: GET /api/painel, filtros da Lista e campos novos do resumo; openapi.json

**Files:**
- Modify: `src/radar/api/app.py`, `src/radar/api/modelos.py`, `frontend/openapi.json` (gerado)
- Test: `tests/integration/test_api.py`, `tests/unit/test_modelos_api.py`, `tests/unit/test_openapi_atualizado.py` (sem mudança; tem de passar)

**Interfaces:**
- Consumes: `filtrar(..., limite=None, modelo_analise=, recorte=)`, `Recorte`, `separar_painel`, `Painel`, `Secao`, `Fonte` (raiz `radar.editais`); `ler_perfil_resumo` (raiz `radar.analise`); `falhas_de_coleta` (raiz `radar.coleta`).
- Produces (OpenAPI, que vira `api.d.ts` na Task 7):
  - `EditalResumoOut` ganha `veredito: Veredito | None`, `motivo_verificar: str | None`, `primeiro_visto_em: str | None` (`iso_com_fuso`).
  - `ListaOut` ganha `perfil_exemplo: bool`.
  - `class FiltroVeredito(StrEnum)`: `apto`, `verificar`, `inapto`, `sem_analise`.
  - `GET /api/editais` aceita `encerra_em_dias: Annotated[int | None, Query(ge=0, le=365)]`, `novos: bool = False`, `veredito: Annotated[list[FiltroVeredito] | None, Query()]` (repetível, como `uf`).
  - `SecaoPainelOut(chave: Literal["decidir", "mais_adiante"], total: int, editais: list[EditalResumoOut])`, `ContadoresOut(decidir: int, novos: int, abertos: int)`, `FalhaColetaOut(fonte: Fonte, motivo: Literal["atrasado", "falha"], dados_de: str | None)`, `PainelOut(atualizado_em: str | None, perfil_exemplo: bool, contadores: ContadoresOut, secoes: list[SecaoPainelOut], coleta: list[FalhaColetaOut])`; `painel_out(painel: Painel, atualizado_em, perfil_exemplo, falhas) -> PainelOut` em `modelos.py`.
  - `GET /api/painel`: uma chamada `filtrar(sessao, criterios, agora, limite=None, modelo_analise=modelo_padrao)`; `perfil_exemplo = resumo is None or resumo.exemplo`; `separar_painel(..., considerar_veredito=not perfil_exemplo)`; `falhas_de_coleta(sessao, agora, [f.value for f in Fonte])`. Detalhe e lista passam `modelo_analise=modelo_padrao`.

- [ ] **Step 1: Escrever os testes que falham** (`tests/integration/test_api.py`; reusa `_registro`, `_gravar_analise`; fixa `primeiro_visto_em` por `UPDATE`)

```python
def test_resumo_traz_veredito_motivo_e_primeiro_visto_em(cliente, sessao):
    assert e["veredito"] == "verificar" and e["motivo_verificar"] == "atestados"
    assert e["primeiro_visto_em"].endswith("-03:00")
def test_lista_filtra_encerra_em_dias_novos_e_veredito(cliente, sessao): ...
@pytest.mark.parametrize("params", [{"encerra_em_dias": "366"}, {"encerra_em_dias": "-1"}, {"veredito": "talvez"}])
def test_recorte_invalido_422(cliente, params): assert cliente.get("/api/editais", params=params).status_code == 422
def test_painel_separa_e_conta_da_mesma_base(cliente, sessao):
    # 3 em até 20 dias (1 novo), 2 depois
    assert corpo["contadores"] == {"decidir": 3, "novos": 1, "abertos": 5}
    assert [s["chave"] for s in corpo["secoes"]] == ["decidir", "mais_adiante"]
def test_painel_contador_decidir_bate_com_a_lista_filtrada(cliente, sessao):
    lista = cliente.get("/api/editais", params={"encerra_em_dias": 20, "veredito": ["apto", "verificar", "sem_analise"]})
    assert len(lista.json()["editais"]) == painel["contadores"]["decidir"]
def test_painel_sem_perfil_resumo_e_exemplo_e_ignora_veredito(cliente, sessao):
    assert corpo["perfil_exemplo"] is True and inapto_id in ids_decidir
def test_painel_com_perfil_real_tira_inapto_de_decidir(cliente, sessao):
    # gravar_perfil_resumo com Perfil fictício exemplo=False
    assert corpo["perfil_exemplo"] is False and inapto_id not in ids_de_todas_as_secoes
def test_painel_lista_fonte_atrasada_sem_texto_de_erro(cliente, sessao):
    # retrato da sanepar em falha com erro="http://interno/segredo"
    assert {"fonte": "sanepar", "motivo": "atrasado", "dados_de": None} in corpo["coleta"]
    assert "interno" not in resposta.text
def test_lista_traz_perfil_exemplo(cliente): assert cliente.get("/api/editais").json()["perfil_exemplo"] is True
```
Em `tests/unit/test_modelos_api.py`: o builder de `EditalDetalhe` recebe os três campos novos; `test_resumo_out_traz_primeiro_visto_em_com_fuso`.

- [ ] **Step 2: Rodar e ver falhar** — Run: `uv run pytest tests/integration/test_api.py tests/unit/test_modelos_api.py -v` — Expected: FAIL.
- [ ] **Step 3: Implementar** rotas e modelos.
- [ ] **Step 4: Regerar o contrato** — Run: `uv run python scripts/exportar_openapi.py` — Expected: `frontend/openapi.json` alterado (o `api.d.ts` é regerado na Task 7, junto com o front que passa a compilar com ele).
- [ ] **Step 5: Rodar e ver passar** — Run: `uv run pytest tests/integration/test_api.py tests/unit/test_modelos_api.py tests/unit/test_openapi_atualizado.py tests/integration/test_multifonte.py -v`, depois `uv run ruff check . && uv run ruff format --check . && uv run mypy src` — Expected: PASS.
- [ ] **Step 6: Commit** (`app.py`, `modelos.py`, testes, `frontend/openapi.json`; `feat(api): GET /api/painel, recorte da lista e veredito no resumo` + trailers).

### Task 7: Front, dados: tipos, Fonte.painel, recorte, fonteExemplo e formatos

**Files:**
- Modify: `frontend/src/dados/api.d.ts` (gerado), `frontend/src/dados/tipos.ts`, `frontend/src/dados/fonte.ts`, `frontend/src/dados/fonteApi.ts`, `frontend/src/dados/fonteExemplo.ts`, `frontend/src/dados/formatos.ts`, `frontend/src/componentes/Aderencia.tsx` (passa a usar `nomeCampo`), `frontend/src/testes/apoio.tsx`
- Test: `frontend/src/dados/formatos.test.ts`, `frontend/src/dados/fonte.test.ts`

**Interfaces:**
- Consumes: `openapi.json` da Task 6.
- Produces:
  - `tipos.ts`: `Painel = PainelOut`, `SecaoDoPainel = SecaoPainelOut`, `FalhaColeta = FalhaColetaOut`, `FiltroVeredito = components['schemas']['FiltroVeredito']`.
  - `fonte.ts`: `Filtros = {linha?; ufs?; todos?; encerraEmDias?: number; novos?: boolean; vereditos?: FiltroVeredito[]}`; `Fonte.painel(): Promise<Painel>`.
  - `fonteApi.ts`: `consulta()` manda `encerra_em_dias`, `novos=true` (só quando verdadeiro), `veredito` repetido; `painel: () => lerJson('/api/painel')`.
  - `formatos.ts`: `diasAteEncerrar(iso: string, agora: Date): number` (`formatarPrazo` passa a usá-la), `ehNovo(primeiroVistoEm: string | null, agora: Date): boolean` (`DIAS_NOVO = 3`, inclusivo), `nomeCampo(campo: string): string` (o mapa `CAMPOS` sai de `Aderencia.tsx`), `ehFiltroVeredito(v: string): v is FiltroVeredito`, `DIAS_DECIDIR = 20`.
  - `fonteExemplo.ts`: o `exemplo.json` **não é regerado**; ao carregar, cada edital ganha `veredito: null`, `motivo_verificar: null`, `primeiro_visto_em: data_publicacao` quando faltarem. `selecionar` aplica o recorte com as mesmas regras do back. `painel()` usa a mesma base de `selecionar` (sem filtros), `perfil_exemplo: true`, `coleta: []`, `LIMITE_SECAO = 30`, contadores da base inteira. `listar` devolve `perfil_exemplo: true`.
  - `apoio.tsx`: `fonteFalsa` ganha `painel` padrão (seções vazias, contadores zero); `edital()` ganha `veredito: null`, `motivo_verificar: null`, `primeiro_visto_em: '2026-11-01T10:00:00-03:00'`.

- [ ] **Step 1: Regerar os tipos** — Run (em `frontend/`): `npm ci && npm run contrato` — Expected: `src/dados/api.d.ts` com `PainelOut`, `FiltroVeredito`; `npx tsc --noEmit` falha até o Step 4.

- [ ] **Step 2: Escrever os testes que falham**

```ts
// formatos.test.ts (AGORA = 26/11/2026 12:00 -03:00)
it('diasAteEncerrar conta dias de calendário em Brasília', () => {
  expect(diasAteEncerrar('2026-12-16T23:59:00', AGORA)).toBe(20);
  expect(diasAteEncerrar('2026-12-17T00:00:00', AGORA)).toBe(21);
});
it('ehNovo vale até 3 dias, inclusive', () => {
  expect(ehNovo('2026-11-23T12:00:00-03:00', AGORA)).toBe(true);
  expect(ehNovo('2026-11-23T11:59:00-03:00', AGORA)).toBe(false);
  expect(ehNovo(null, AGORA)).toBe(false);
});
it('nomeCampo traduz o campo e deixa o desconhecido como veio', () => {
  expect(nomeCampo('atestados')).toBe('Atestado técnico');
  expect(nomeCampo('novo_campo')).toBe('novo_campo');
});
// fonte.test.ts
it('api: listar manda encerra_em_dias, novos e veredito repetido', ...);
  // URL: /api/editais?encerra_em_dias=20&novos=true&veredito=apto&veredito=sem_analise
it('api: painel lê /api/painel', ...);
it('exemplo: recorte por encerraEmDias, novos e vereditos', ...);   // sem_analise casa todos (veredito nulo)
it('exemplo: painel separa Decidir e Mais adiante, um edital numa seção só e contadores da base', ...);
  // contadores.abertos === (await listar({})).editais.length; perfil_exemplo === true
```

- [ ] **Step 3: Rodar e ver falhar** — Run: `npm test -- src/dados` — Expected: FAIL.
- [ ] **Step 4: Implementar.**
- [ ] **Step 5: Rodar e ver passar** — Run: `npm test && npm run lint && npm run duplicacao && npm run contrato:verificar && npx tsc --noEmit` — Expected: tudo verde.
- [ ] **Step 6: Commit** (`feat(front): dados do painel, recorte da lista e campos novos do resumo` + trailers; sem arquivo guardado).

### Task 8: Front, componentes e página Painel

**Files:**
- Create: `frontend/src/componentes/SecaoPainel.tsx`, `frontend/src/componentes/ContadorPainel.tsx`, `frontend/src/componentes/AvisoColeta.tsx`, `frontend/src/paginas/Painel.tsx`, `frontend/src/paginas/Painel.test.tsx`
- Modify: `frontend/src/componentes/CartaoEdital.tsx`, `frontend/src/componentes.test.ts` (guardado), `frontend/src/paginas/Lista.tsx` (passa `comAderencia`)

**Interfaces:**
- Consumes: Task 7 (`Painel`, `SecaoDoPainel`, `FalhaColeta`, `ehNovo`, `nomeCampo`, `DIAS_DECIDIR`, `Fonte.painel`).
- Produces:
  - `CartaoEdital({edital, href, agora, comAderencia = false})`: na linha de baixo, depois de `SeloFonte`, `Token` "novo" quando `ehNovo(edital.primeiro_visto_em, agora)`; com `comAderencia`, `Token` de aderência: apto → "apto" (verde); verificar → `verificar: ${nomeCampo(motivo)}` ou "verificar" (amarelo); inapto → "inapto" (vermelho); nulo → "sem análise · conferir edital" (cinza). Sem `comAderencia`, nenhum selo de aderência.
  - `ContadorPainel({valor: number, rotulo: string, href: string})`: `ClickableCard` com `label` `${valor} ${rotulo}` (ex.: "9 para decidir").
  - `SecaoPainel({titulo: string, descricao: string, vazio: string, secao: SecaoDoPainel, agora: Date, comAderencia: boolean, recolhida?: boolean, hrefLista: string})`: `Heading` nível 2, descrição, cartões (`href` `/edital/${encodeURIComponent(id)}`, sem consulta), linha única `vazio` quando não há editais, link "Ver os ${total} na lista" quando `total > editais.length`; `recolhida` usa `Collapsible` fechado.
  - `AvisoColeta({falhas: FalhaColeta[]})`: `null` sem falhas; senão `Banner` de aviso. Uma falha: título é a frase dela; várias: "N fontes podem estar desatualizadas", com as frases num `Collapsible` "detalhes". Frases: atrasado com data → "`<Fonte>` pode estar desatualizado desde `dd/mm/aaaa HH:MM`"; atrasado sem data → "`<Fonte>` ainda não teve nenhuma leitura concluída"; falha → "A última leitura de `<Fonte>` falhou; os editais dela são de `dd/mm/aaaa HH:MM`". Nome por `NOMES_FONTE`, data por `formatarDataHora`.
  - `Painel()`: se a consulta tem `linha` ou `uf`, `<Navigate to={`/editais?${consulta}`} replace />`; senão `useCarga(() => fonte.painel(), 'painel')`, com carregando/erro (`ErroAoCarregar` com "Tentar de novo"). `Heading` nível 1 "Painel"; `AvisoColeta`; contadores "para decidir" (href `/editais?encerra_em_dias=20&veredito=apto&veredito=verificar&veredito=sem_analise`, ou `/editais?encerra_em_dias=20` com perfil de exemplo), "novos" (`/editais?novos=true`), "abertos ao todo" (`/editais`); com `perfil_exemplo`, o texto "Aderência indisponível: perfil não carregado."; seções Decidir ("Sem triagem, encerra em até 20 dias", vazio "Nada para decidir agora.") e Mais adiante ("Sem triagem, encerra em mais de 20 dias", vazio "Nada mais adiante.", recolhida). Grade como a da Lista (uma coluna em 390 px).
  - `componentes.test.ts`: `COMPONENTES` ganha `'AvisoColeta.tsx'`, `'ContadorPainel.tsx'`, `'SecaoPainel.tsx'`, na ordem de `sort()`.
  - `Lista.tsx`: `comAderencia={!carga.dados.perfil_exemplo}` no `CartaoEdital`.

- [ ] **Step 1: Confirmar os componentes Astryx** — Run: `npx astryx component Collapsible`, `npx astryx component ClickableCard`, `npx astryx component Token` — Expected: props conhecidas (se `Collapsible` não existir, `npx astryx search "collapsible"` e usar o equivalente do Astryx; nada de HTML cru).

- [ ] **Step 2: Escrever os testes que falham** (`Painel.test.tsx`, jsdom, `vi.useFakeTimers({toFake: ['Date']})` em 26/11/2026 12:00, `renderizar` do `apoio.tsx`)

```ts
it('painel_mostra_contadores_com_links_para_a_lista', ...);
  // getByRole('link', {name: '3 para decidir'}).href === '/editais?encerra_em_dias=20&veredito=apto&veredito=verificar&veredito=sem_analise'
  // '1 novos' -> '/editais?novos=true'; '5 abertos ao todo' -> '/editais'
it('painel_secao_vazia_vira_uma_linha', ...);             // getByText('Nada para decidir agora.')
it('painel_selos_novo_e_verificar_com_motivo', ...);      // 'novo' e 'verificar: Atestado técnico'
it('painel_perfil_de_exemplo_esconde_aderencia', ...);
  // getByText('Aderência indisponível: perfil não carregado.'); queryByText(/^verificar/) === null; link sem veredito=
it('painel_aviso_de_coleta_so_quando_falha', ...);        // sem falhas: queryByRole('alert'...) nulo; com uma: frase da Sanepar
it('painel_mais_adiante_comeca_recolhido_e_abre', ...);
it('painel_mostra_ver_todos_quando_a_secao_foi_cortada', ...);  // total 40, 30 cartões: 'Ver os 40 na lista'
it('painel_erro_tem_tentar_de_novo', ...);
it('link_antigo_com_linha_e_uf_vai_para_editais_com_os_mesmos_parametros', ...);
  // renderizar com endereco '/?linha=redes&uf=SP' e rotas '/' e '/editais' -> endereço '/editais?linha=redes&uf=SP'
```
`componentes.test.ts` passa a esperar os três arquivos novos (falha até os arquivos existirem).

- [ ] **Step 3: Rodar e ver falhar** — Run: `npm test -- src/paginas/Painel.test.tsx src/componentes.test.ts` — Expected: FAIL.
- [ ] **Step 4: Implementar.**
- [ ] **Step 5: Rodar e ver passar** — Run: `npm test && npm run lint && npm run duplicacao && npm run contrato:verificar && npx tsc --noEmit` — Expected: verde (o Painel ainda não está em `rotas.tsx`; a fronteira permite o import pelo próprio teste).
- [ ] **Step 6: Commit** (trailer do contrato por causa de `componentes.test.ts`):

```bash
git add frontend/src/componentes frontend/src/componentes.test.ts frontend/src/paginas/Painel.tsx frontend/src/paginas/Painel.test.tsx frontend/src/paginas/Lista.tsx
git commit -m "feat(front): página Painel com SecaoPainel, ContadorPainel e AvisoColeta" -m "Contrato-Front: aprovado
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012v6r8uz1aiPHNQoDZ66AVf"
```

### Task 9: Front, rotas (/ = Painel, /editais = Lista), recorte visível na Lista e links

**Files:**
- Modify: `frontend/src/rotas.tsx`, `frontend/src/rotas.test.ts` (guardado), `frontend/CLAUDE.md` (guardado; só a lista de rotas e o "única no menu"), `frontend/src/App.test.tsx`, `frontend/src/paginas/Lista.tsx`, `frontend/src/paginas/Lista.test.tsx`, `frontend/src/paginas/Detalhe.tsx`, `frontend/src/paginas/Detalhe.test.tsx`, `frontend/src/paginas/NaoEncontrada.tsx`, `frontend/src/paginas/Entrar.test.tsx` (só nomes de teste)

**Interfaces:**
- Consumes: `Painel` (Task 8), `Filtros` com recorte e `ehFiltroVeredito` (Task 7).
- Produces:
  - `rotas`: `[{'/', 'Painel', noMenu: true, Painel}, {'/editais', 'Editais', noMenu: true, Lista}, {'/edital/:id', ...}, {'/entrar', ...}, {'*', ...}]`; Painel e Lista no pacote inicial, Detalhe e Entrar continuam `lazy`. `Shell.tsx` não muda.
  - `frontend/CLAUDE.md`: "`/`: painel (primeiro no menu)", "`/editais`: lista de editais (no menu)", 404 "com link para a lista (`/editais`)"; o restante do arquivo intacto.
  - `Lista`: lê da URL `encerra_em_dias` (inteiro de 0 a 365; outro valor é ignorado), `novos=true` e `veredito` (só `ehFiltroVeredito`); passa no `Filtros`; mostra, quando há recorte, uma linha `Text` "Só os que encerram em até N dias · só os novos · aderência: apto, verificar, sem análise" (as partes presentes) com `Button` "Mostrar todos", que tira os três parâmetros e mantém `linha` e `uf`. Os cartões continuam levando a consulta inteira para o detalhe.
  - `Detalhe` → `Voltar` aponta para `/editais${search}`. `NaoEncontrada` → botão "Voltar para a lista" com `href="/editais"`. `Entrar` continua com `navegar('/')`: depois do login cai no Painel, a nova tela inicial.

- [ ] **Step 1: Escrever os testes que falham**

```ts
// rotas.test.ts
expect(rotas.map(r => r.caminho)).toEqual(['/', '/editais', '/edital/:id', '/entrar', '*']);
it('só o painel e a lista aparecem no menu, nessa ordem', () =>
  expect(rotas.filter(r => r.noMenu).map(r => r.caminho)).toEqual(['/', '/editais']));
// App.test.tsx
expect(tituloDaRota('/')).toBe('Painel · Radar FR');
expect(tituloDaRota('/editais')).toBe('Editais · Radar FR');
// Lista.test.tsx
it('lista_le_recorte_da_url_e_mostra_o_que_esta_filtrando', ...);
  // endereco '/editais?encerra_em_dias=20&novos=true' -> listar recebeu {encerraEmDias: 20, novos: true, ...}
  // getByText('Só os que encerram em até 20 dias · só os novos')
it('lista_mostrar_todos_limpa_o_recorte_e_mantem_linha', ...);   // endereço vira '/editais?linha=redes'
it('lista_ignora_recorte_invalido_na_url', ...);                  // encerra_em_dias=abc, veredito=talvez -> sem recorte
// Detalhe.test.tsx: Voltar '/editais?linha=redes&uf=SP' e, sem consulta, '/editais'
```

- [ ] **Step 2: Rodar e ver falhar** — Run: `npm test` — Expected: FAIL nos acima.
- [ ] **Step 3: Implementar** (o `renderizar` dos testes da Lista e do Detalhe passa `padrao: '/editais'` onde precisar).
- [ ] **Step 4: Rodar e ver passar** — Run: `npm test && npm run lint && npm run duplicacao && npm run contrato:verificar && npx tsc --noEmit && npm run build` — Expected: verde.
- [ ] **Step 5: Commit** com `Contrato-Front: aprovado` no bloco de trailers (`git add` dos arquivos acima; mensagem `feat(front): / abre o Painel, a Lista vai para /editais e mostra o recorte`).

### Task 10: E2E, screenshots no container Linux e documentação da etapa

**Files:**
- Create: `frontend/e2e/painel.spec.ts`, `docs/etapas/08-painel.md`, `docs/videos/08-painel.md`
- Modify: `frontend/e2e/navegacao.spec.ts`, `frontend/e2e/__screenshots__/{celular,desktop}/shell.spec.ts/*.png` (regerados), `docs/checklist.md`
- Sem `docs/decisoes/` nesta fase (a 0008 é da 08b).

**Interfaces:**
- Consumes: app completo com `VITE_FONTE=exemplo` (Tasks 7 a 9); relógio de `e2e/relogio.ts` (26/11/2026 12:00).

- [ ] **Step 1: Atualizar e escrever os e2e**

`navegacao.spec.ts`: `page.goto('/#/editais')`; as URLs esperadas passam a `/#\/editais\?linha=redes$/` (ao filtrar e depois do Voltar).
`painel.spec.ts`:
```ts
test('painel abre em / e o contador de decidir leva à lista filtrada', ...);
  // heading nível 1 'Painel'; clica no link /para decidir$/; URL /#\/editais\?encerra_em_dias=20/; heading 'Editais abertos'
test('link antigo #/?linha=redes abre a lista com o filtro', ...);
  // goto('/#/?linha=redes') -> URL /#\/editais\?linha=redes$/; botão 'Redes' com aria-pressed 'true'
test('painel e lista não passam da largura da tela', ...);   // scrollWidth <= viewport em '/#/' e '/#/editais'
```
`shell.spec.ts` não muda (o `beforeEach` em `/` acha cartões em Decidir: os prazos da fixture vencem até 05/12).

- [ ] **Step 2: Rodar o e2e no container Linux do CI e regerar as referências** (o menu ganhou "Painel": `shell.png` e `gaveta.png` mudam). No PowerShell, na raiz do worktree, com o `node_modules` do Windows fora do container:

```powershell
docker run --rm -v "${PWD}:/repo" mcr.microsoft.com/playwright:v1.65.1-noble bash -c "cp -r /repo/frontend /tmp/f && cd /tmp/f && rm -rf node_modules && npm ci && npx playwright test --update-snapshots && npx playwright test && cp -r e2e/__screenshots__/. /repo/frontend/e2e/__screenshots__/"
```
Expected: a segunda rodada (sem `--update-snapshots`) passa nos dois projetos (celular e desktop). Abrir os três PNG e conferir que só o menu mudou ("Painel" e "Editais").

- [ ] **Step 3: Documentação**
  - `docs/etapas/08-painel.md`, no formato das outras etapas: o porquê (do spec), "O que foi feito (08a)" (primeiro_visto_em e backfill, veredito por junção lateral e índice, `perfil_resumo`, `/api/painel`, faixa de coleta, rotas e recorte da Lista, fila de análise já por prazo), "Decisões da 08a" (as regras desta lista de Review Focus e as escolhas do plano: perfil de exemplo ignora veredito, dias de calendário, Voltar do detalhe vai para a Lista, Entrar cai no Painel, índice ascendente), "O que fica para 08b e 08c", "Perguntas de entrevista" (5). Desenho e plano citados pelo caminho.
  - `docs/videos/08-painel.md`, no formato de `docs/videos/07-historico.md`: gancho ("o que eu preciso decidir hoje?"), por que 20 dias (Lei 14.133, art. 55), o selo "novo" sem carimbo de visita, perfil de exemplo sem aderência falsa, a faixa que só aparece quando algo falhou, a tela.
  - `docs/checklist.md`: seção nova "## Painel (etapa 8)" com itens marcados: `primeiro_visto_em` protegido do upsert e backfill com data-sentinela (`migrations/versions/0015_painel.py`, `src/radar/editais/registro.py`); veredito no resumo por `LEFT JOIN LATERAL` com índice (`src/radar/editais/filtro.py`); `GET /api/painel` com contadores da mesma base (`src/radar/api/app.py`, `src/radar/editais/painel.py`); faixa de coleta por fonte (`src/radar/coleta/frescor.py`, `frontend/src/componentes/AvisoColeta.tsx`); `/` = Painel e `/editais` = Lista com redirecionamento de link antigo (`frontend/src/rotas.tsx`); cada um com "Vídeo: a gravar (`docs/videos/08-painel.md`)". Itens abertos: "08b: triagem compartilhada", "08c: prioridade configurável", "Migração 0015 no Neon e deploy da 08a".

- [ ] **Step 4: Verificação completa**

Run (raiz): `uv run ruff check . && uv run ruff format --check . && uv run mypy src && PYTHONIOENCODING=utf-8 uv run lint-imports && uv run pytest -v`
Run (`frontend/`): `npm test && npm run lint && npm run duplicacao && npm run contrato:verificar && npm run contrato && git diff --exit-code src/dados/api.d.ts && npx tsc --noEmit && npm run build`
Expected: tudo verde; `git diff` do `api.d.ts` vazio.

- [ ] **Step 5: Commits** (dois, para o trailer ficar só no que toca `e2e/`):

```bash
git add frontend/e2e
git commit -m "test(front): e2e do painel e screenshots do shell regerados no container Linux" -m "Contrato-Front: aprovado
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012v6r8uz1aiPHNQoDZ66AVf"
git add docs/etapas/08-painel.md docs/videos/08-painel.md docs/checklist.md
git commit -m "docs(painel): etapa 08a, roteiro do vídeo e checklist" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012v6r8uz1aiPHNQoDZ66AVf"
```
