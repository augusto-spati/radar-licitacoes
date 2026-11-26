# Radar de licitações: plano da etapa 3 (coletores Sanepar e Compesa)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O radar passa a ter várias fontes. Os editais de Obras e Engenharia da Sanepar e as licitações da Compesa entram no mesmo banco, filtro, API e tela do PNCP. Antes disso, fecham-se as brechas da guarda do contrato do front.

**Architecture:**
- `edital` passa a ter chave `(fonte, id_na_fonte)` e campos normalizados.
- A coleta ganha o modo **retrato**: a lista do portal é lida inteira a cada rodada, ao lado das janelas do PNCP.
- Os coletores ficam em `radar.coleta` (`sanepar.py`, `compesa.py`, com o motor comum em `retratos.py`). Eles gravam páginas brutas e extraem **dicionários normalizados**.
- `radar.editais` valida e grava esses dicionários (`registrar_normalizados`) e reconcilia os editais que saíram da lista.
- A `cli` faz a ponte entre os dois módulos, como já faz hoje.

**Tech Stack:** o existente, mais um parser HTML (`selectolax` ou `beautifulsoup4`, à escolha de quem implementa, com justificativa).

**Spec:** `docs/superpowers/specs/2026-11-26-etapa-03-coletores-design.md`. Detalhes técnicos das fontes em `2026-11-26-fonte-sanepar.md` e `2026-11-26-fonte-compesa.md`, no mesmo diretório.

## Global Constraints

- **Fontes:** `pncp | sanepar | compesa`.
- **Id público:** `<fonte>~<id_na_fonte>`.
  - `id_na_fonte` da Sanepar: `OE-<numero>/<aa>`, por exemplo `OE-345/26`.
  - `id_na_fonte` da Compesa: o `lc_chave`, por exemplo `2026LC213CEL-1`.
  - `id_na_fonte` do PNCP: o `numeroControlePNCP`.
- **Situações normalizadas** (`aberta | em_julgamento | encerrada | outra`):

  | Fonte | aberta | em_julgamento | encerrada | outra |
  |---|---|---|---|---|
  | PNCP (`situacao_id`) | 1 | — | 2, 3 | 4 |
  | Sanepar | Abertura | Julgamento | ao sair de um retrato completo | — |
  | Compesa | Abertura | Habilitação | Homologação, Contrato, Fracassada, Deserta, Revogada, Anulada | Suspensa e qualquer valor desconhecido (log `situacao_desconhecida`) |

- **Aberto** = `situacao_normalizada = 'aberta' AND data_encerramento_proposta > agora` (horário de Brasília). Prazo nulo nunca conta como aberto.
- **Prazo:**
  - Sanepar: o campo "Propostas" do detalhe; se faltar, a "Data de Abertura" (`dd/mm/aaaa às HHhMM`).
  - Compesa: ABERTURA mais HORA.
- **Filtros:** UF, texto e aberto valem para as três fontes. Modalidades (`filtro.toml`) valem só para o PNCP.
- **Cortesia com as fontes:**
  - User-Agent `radar-licitacoes-estudo (github.com/augusto-spati/radar-licitacoes)`;
  - **3 s** entre requisições à mesma fonte;
  - sem paralelismo;
  - em 403, 429, captcha ou página sem o marcador esperado, o retrato **para** e vira `falha`, sem tentar de novo dentro da rodada;
  - nunca usar POST na Compesa.
- **Retrato:**
  - abaixo do mínimo da fonte, ou com qualquer página em falha → `falha`. Não grava editais e não reconcilia;
  - menos de 50% do retrato anterior bem-sucedido → log `retrato_encolheu`, e grava normalmente.
- **Hash:** `hash_conteudo` é o sha256 do JSON canônico (chaves ordenadas) dos campos normalizados mais a lista de anexos (nome e id). Não entra nada volátil.
- **Fronteiras:**
  - `coleta` não importa `editais` e vice-versa;
  - `cli` e `api` importam só os pacotes raiz;
  - os contratos do import-linter continuam valendo.
- **Testes de rede:** os que acessam fontes reais têm a marca `rede` e ficam fora do CI.
- **Commits:**
  - todo commit termina com:
    ```
    Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
    Claude-Session: https://claude.ai/code/session_012v6r8uz1aiPHNQoDZ66AVf
    ```
  - commits que mexem nos arquivos guardados do front ou do CI incluem também a linha `Contrato-Front: aprovado`.
- **Branch e merge:** o trabalho é no branch `etapa-03-coletores`. A `main` é protegida e o merge acontece por PR, com os 4 checks verdes e o OK do Augusto.

## Review Focus

1. **O layout da fonte muda e os seletores não acham nada.** O retrato tem de virar `falha`, sem gravar e sem marcar todos os editais como "saiu da lista". O teste fica na Task 3, e o de cada fonte nas Tasks 4 e 5.
2. **Uma página do meio do retrato dá erro.** Nada é reconciliado, e os editais continuam abertos. Teste na Task 3.
3. **A Compesa responde em outro locale (`MM/dd/yyyy`).** O estudo viu isso a partir da página 2; a causa real é o `Accept-Language` da requisição, então o coletor pede pt-BR em toda requisição e lê tudo em dd/MM. A data tem de sair certa, e não com dia e mês trocados: uma página com muitas datas impossíveis em dd/MM é erro de layout. O retrato cobre os últimos 12 meses da grade, não a lista inteira. Teste na Task 5.
4. **Na Sanepar, a lista não muda mas o detalhe ganha um anexo.** O hash muda e o edital é atualizado. Teste na Task 4.
5. **O PNCP continua igual depois da migração.** Os mesmos 24 editais abertos aparecem no `filtrar` local, e as janelas e a auditoria seguem funcionando. Testes nas Tasks 1 e 6.

---

### Task 0: Fechar as brechas da guarda do contrato do front

**Files:**
- Modify: `.github/workflows/ci.yml` (job `contrato-guardado`), `frontend/scripts/contrato-guardado.mjs`, `.github/CODEOWNERS`, `frontend/CLAUDE.md`
- Test: o teste existente do script, que ganha casos novos

**Interfaces:**
- **Comportamento do job:** ele executa o script **da base do range**, com `git show <base>:frontend/scripts/contrato-guardado.mjs > "$RUNNER_TEMP/guarda.mjs"`, e não o do commit testado. Se o script não existir na base (primeira vez), usa o do commit e registra isso no log do job.
- **Novos caminhos guardados**, por prefixo ou glob:
  - `frontend/tsconfig*.json`
  - `frontend/vitest.config.*`
  - `frontend/vite.config.*`
  - `frontend/eslint.config.*`
  - `.gitignore`
  - `frontend/.gitignore`
- **Documentação:** registrar no `frontend/CLAUDE.md` que a guarda pressupõe o fluxo por PR, que é obrigatório na `main`.

- [ ] **Step 1:** Escrever testes que falham:
  - um commit que muda `frontend/tsconfig.json` sem o trailer é reprovado;
  - o mesmo para `frontend/vitest.config.ts` e `.gitignore`;
  - um commit que edita o próprio script para sempre aprovar continua reprovado, porque quem decide é o script da base. Para testar isso, simular rodando a função de decisão da base contra o diff.
- [ ] **Step 2:** Implementar. Rodar `npm test`, `npm run lint` e `npm run contrato:verificar`.
- [ ] **Step 3:** Commit com `Contrato-Front: aprovado`. Fazer push do branch e conferir os 4 jobs verdes com `gh run watch`.

### Task 1: Modelo multi-fonte (migração da chave)

**Files:**
- Create: `migrations/versions/0003_multifonte.py`
- Modify:
  - `src/radar/editais/modelos.py`, `registro.py`, `filtro.py`, `__init__.py`
  - `src/radar/api/modelos.py`, `app.py`, `exemplo.py`
  - os testes de `editais` e `api`
- Test: `tests/integration/test_multifonte.py` e os testes existentes, ajustados

**Interfaces:**
- **Tabela `edital`:**
  - chave `(fonte text, id_na_fonte text)`;
  - colunas novas: `modalidade_nome text NOT NULL`, `situacao_original text NOT NULL`, `situacao_normalizada text NOT NULL` (com CHECK nos quatro valores), `visto_em timestamptz NULL`, `hash_conteudo text NULL`;
  - `modalidade_id` e `situacao_id` passam a aceitar nulo;
  - `numero_controle_pncp` sai, porque o valor vai para `id_na_fonte`;
  - índice novo: `(situacao_normalizada, data_encerramento_proposta)`.
- **Migração 0003:**
  - preenche `fonte='pncp'` e `id_na_fonte = numero_controle_pncp`;
  - preenche `modalidade_nome` a partir do `bruto->>'modalidadeNome'`;
  - preenche a situação pelo mapa do PNCP;
  - tem `downgrade` completo;
  - no banco local, o `EXPLAIN (ANALYZE, BUFFERS)` da consulta do `filtrar`, antes e depois, vai para `docs/etapas/03-coletores.md` (rascunho criado aqui).
- **`registrar` (PNCP):** passa a preencher os campos normalizados.
- **`registrar_normalizados(sessao: Session, registros: list[dict[str, Any]]) -> ResultadoRegistro`:**
  - valida com um modelo Pydantic `EditalNormalizado`, com os campos `fonte`, `id_na_fonte`, `objeto`, `orgao_nome`, `orgao_cnpj | None`, `uf`, `municipio | None`, `modalidade_nome`, `situacao_original`, `situacao_normalizada`, `valor_estimado: Decimal | None`, `data_publicacao | None`, `data_abertura_proposta | None`, `data_encerramento_proposta | None`, `link_origem | None`, `anexos: list[{nome, id}]` e `bruto: dict`;
  - calcula `hash_conteudo`;
  - faz upsert por `(fonte, id_na_fonte)`. O WHERE do upsert é `edital.hash_conteudo IS DISTINCT FROM excluded.hash_conteudo`;
  - atualiza `visto_em` sempre, num segundo UPDATE quando o hash é igual;
  - conta como no PNCP: inseridos, atualizados, ignorados (o mesmo hash) e inválidos.
- **`reconciliar_retrato(sessao: Session, fonte: str, vistos: set[str], quando: datetime) -> int`:** marca como `encerrada` (com `situacao_original='saiu da lista'`) os editais da fonte que estavam `aberta` ou `em_julgamento` e não estão em `vistos`. Devolve quantos marcou.
- **`filtrar`, `listar_abertos` e `obter_edital`:**
  - passam a usar a regra de aberto da Global Constraint e as três fontes;
  - o filtro de modalidade vale só para `fonte='pncp'`;
  - `obter_edital` recebe `(fonte, id_na_fonte)`.
- **`EditalResumo` e `EditalDetalhe`:** ganham `fonte` e `id_publico` (`fonte~id`), e `numero_controle_pncp` sai.
- **API:**
  - `GET /api/editais/{id_publico:path}` faz o parse de `fonte~id`. Um formato inválido devolve 404;
  - `EditalResumoOut` ganha `fonte` e `id_publico`;
  - `link_origem` de reserva por fonte: Sanepar → `https://www.sanepar.com.br/licitacao/detalhe/<n>/<aa>`; Compesa → `https://servicos.compesa.com.br/licitacoes-e-contratos/`;
  - regerar `frontend/openapi.json`.

- [ ] **Step 1:** Escrever os testes que falham:
  - `test_migracao_preenche_pncp` (upgrade sobre dados da 0002: chaves e situação);
  - `test_downgrade_volta`;
  - `test_registrar_normalizados_insere_e_hash_igual_ignora`;
  - `test_hash_muda_atualiza`;
  - `test_visto_em_atualiza_sem_mudar`;
  - `test_reconciliar_so_fonte_e_abertos`;
  - `test_filtrar_tres_fontes_e_modalidade_so_pncp`;
  - `test_prazo_nulo_nao_e_aberto`;
  - `test_api_detalhe_por_id_publico` (incluindo o id com `/`);
  - `test_api_id_publico_invalido_404`.
- [ ] **Step 2:** Rodar. Esperado: FAIL.
- [ ] **Step 3:** Implementar e ajustar os testes antigos ao novo id.
- [ ] **Step 4:** Rodar a suíte Python inteira, `ruff`, `ruff format --check`, `mypy src`, `lint-imports` e o teste do OpenAPI. Esperado: tudo passa.
- [ ] **Step 5:** Commit `feat(editais): modelo multi-fonte com chave (fonte, id_na_fonte) e situação normalizada`.

### Task 2: Front multi-fonte

**Files:**
- Modify:
  - `frontend/src/dados/api.d.ts` (gerado), `tipos.ts`, `fonteExemplo.ts`, `fonteApi.ts`, `formatos.ts`, `exemplo.json` (regerado)
  - `src/componentes/CartaoEdital.tsx`, `src/paginas/Detalhe.tsx`, `src/paginas/Lista.tsx`
  - os testes do front
- Create: `src/componentes/SeloFonte.tsx`. A lista fechada em `componentes.test.ts` é atualizada no mesmo commit, com `Contrato-Front: aprovado`.

**Interfaces:**
- A rota continua `/edital/:id`, e o `:id` é o `id_publico` codificado num segmento só.
- `fonteApi.obter(idPublico)` monta `/api/editais/<fonte>~<id com / preservado>`.
- `SeloFonte` usa um `Token` do Astryx. Os rótulos são "PNCP", "Sanepar" e "Compesa", vindos de um `Record<Fonte, string>` tipado pelo enum gerado.
- O link de reserva vem da API. O front não monta link do PNCP para editais de outra fonte.

- [ ] **Step 1:** Rodar `npm run contrato`. Escrever os testes que falham:
  - o cartão mostra o selo da fonte;
  - o detalhe de `sanepar~OE-345/26` abre;
  - o link de reserva por fonte está certo;
  - `fonteExemplo` filtra as três fontes com a nova regra de aberto.
- [ ] **Step 2:** Implementar.
- [ ] **Step 3:** Rodar `npm test`, `lint`, `duplicacao`, `contrato:verificar`, `tsc --noEmit` e `build`, e o e2e no container Linux. Os screenshots do shell só são regerados se o shell mudar.
- [ ] **Step 4:** Commit com `Contrato-Front: aprovado`. Fazer push e conferir o CI verde.

### Task 3: Motor de retratos

**Files:**
- Create:
  - `migrations/versions/0004_retratos.py`
  - `src/radar/coleta/retratos.py`, que exporta pelo `coleta/__init__.py` as funções `executar_retrato`, `ultimo_retrato`, `ids_do_retrato`, `ClienteEducado`, `FonteRetrato` (Protocol), `ErroFonte` e `ResumoRetrato`
- Modify: `src/radar/coleta/modelos.py` (`ColetaRetrato` e a generalização de `PaginaBruta`), `janelas.py` (`ler_paginas_nao_processadas` passa a cobrir os retratos), `src/radar/cli.py` (`atualizar`, `processar` e `auditar`)
- Test: `tests/integration/test_retratos.py`, com uma fonte falsa

**Interfaces:**
- **Tabela `coleta_retrato`:** `id`, `fonte`, `status` (`em_andamento | concluido | falha`), `iniciado_em`, `concluido_em`, `paginas`, `registros`, `erro`.
- **`pagina_bruta` generalizada:**
  - `janela_id` passa a aceitar nulo, e entra `retrato_id` (FK, nulo), com CHECK de que exatamente um dos dois está preenchido;
  - colunas novas: `formato` (`json | html | js`) e `conteudo_texto text NULL`;
  - `conteudo` passa a aceitar nulo, e um CHECK garante que existe conteúdo compatível com o formato.
- **`class FonteRetrato(Protocol)`:**
  - atributos `nome: str` e `minimo_registros: int`;
  - `def coletar(self, cliente: ClienteEducado) -> Iterator[PaginaColetada]` produz as páginas brutas em ordem e lança `ErroFonte` na primeira falha;
  - `def extrair(self, paginas: list[PaginaColetada]) -> list[dict[str, Any]]` devolve os dicionários normalizados do retrato inteiro.
  - `PaginaColetada` é um dataclass com `url`, `formato` e `texto`.
- **`class ClienteEducado`:**
  - é construído com `(http: httpx.Client, intervalo_s: float = 3.0, dormir: Callable[[float], None] = time.sleep)`;
  - `get(url, params=None) -> httpx.Response` respeita o intervalo, manda o User-Agent da Global Constraint, lança `ErroFonte` em 403 ou 429, e não tenta de novo;
  - expõe o cookie jar do `httpx`.
- **`executar_retrato(sessao: Session, fonte: FonteRetrato, cliente: ClienteEducado, agora: datetime) -> ResumoRetrato`:**
  1. Cria o retrato `em_andamento` e grava cada página bruta (commit por página).
  2. Ao fim, chama `extrair`. Se der menos que `minimo_registros`, o retrato vira `falha` com o erro `"abaixo do minimo: N < M"`.
  3. Senão, fica `concluido`, com `registros`. Se `registros` ficar abaixo de 50% do último retrato concluído, registra o log `retrato_encolheu`.
  4. Um `ErroFonte` em qualquer página marca o retrato como `falha`.
  5. Nada é registrado em `editais`, porque isso é papel da `cli`.
  - `ResumoRetrato` traz `retrato_id`, `status`, `registros`, `erro` e `normalizados: list[dict]` (só quando concluído).
- **`ids_do_retrato(resumo) -> set[str]`:** os `id_na_fonte` dos normalizados.
- **`cli atualizar`:**
  1. Roda o PNCP (como hoje).
  2. Para cada fonte de retrato: chama `executar_retrato`. Se concluído, chama `registrar_normalizados` e depois `reconciliar_retrato`. Se falhou, só registra no log.
  3. Cada fonte falha de forma isolada. O código de saída é 1 se qualquer fonte falhou.
  4. A opção `--fontes pncp,sanepar,compesa` tem como padrão todas.
- **`cli auditar`:** passa a mostrar também o último retrato de cada fonte (status, horário e registros) e sai com 1 se o último retrato falhou ou tem mais de 36 h.

- [ ] **Step 1:** Escrever os testes que falham, com uma `FonteFalsa`:
  - `test_retrato_concluido_registra_e_reconcilia` (via cli);
  - `test_retrato_abaixo_do_minimo_e_falha_e_nao_reconcilia` (Review Focus 1);
  - `test_pagina_com_erro_e_falha_e_nao_reconcilia` (Review Focus 2);
  - `test_403_para_sem_tentar_de_novo`;
  - `test_intervalo_entre_requisicoes` (com `dormir` falso);
  - `test_retrato_encolheu_loga`;
  - `test_fonte_falha_isolada_saida_1`;
  - `test_auditar_mostra_retratos`;
  - `test_pagina_bruta_check_janela_ou_retrato`.
- [ ] **Step 2:** Rodar. Esperado: FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** Rodar a suíte inteira e a qualidade. Esperado: tudo passa.
- [ ] **Step 5:** Commit `feat(coleta): motor de retratos com cortesia, mínimo por fonte e reconciliação`.

### Task 4: Coletor Sanepar

**Files:**
- Create: `src/radar/coleta/sanepar.py`
- Test:
  - `tests/unit/test_sanepar.py`, usando as amostras que já estão em `tests/dados/fontes/` (`lista-oe-abertura-p1.html`, `lista-oe-abertura-p2.html`, `detalhe-345-26-vazamentos.html`, `detalhe-349-26-vazamentos.html`);
  - `tests/integration/test_sanepar_real.py` (marca `rede`)

**Interfaces:**
- `class FonteSanepar` implementa `FonteRetrato`, com `nome = "sanepar"` e `minimo_registros = 5`. O mínimo é justificado no relatório pelas amostras: são 57 editais abertos em OE.
- **`coletar`:**
  1. Lista `https://www.sanepar.com.br/licitacoes?Tipo=OE&Situacao=<s>&page=<n>` para `s` em Abertura e Julgamento, começando em `page=0` e seguindo enquanto houver linhas.
  2. Busca o detalhe de cada edital listado, em `/licitacao/detalhe/<n>/<aa>`.
  3. Uma página sem `.edt-results-count` (lista) ou sem `h2.sli-contract-title` (detalhe) gera `ErroFonte("layout")`.
- **`extrair`:** junta a linha da lista com o detalhe. Os campos:
  - `id_na_fonte = "OE-<n>/<aa>"`;
  - o objeto completo vem do detalhe;
  - `modalidade_nome`;
  - `situacao_original` e a normalizada;
  - `uf = "PR"`;
  - `orgao_nome = "SANEPAR"` e `orgao_cnpj = "76484013000145"`;
  - as datas: abertura `dd/mm/aaaa às HHhMM`, e o encerramento pela regra "Propostas" e depois "abertura";
  - `link_origem` = a URL do detalhe;
  - `anexos` (nome e id de `/licitacao/arquivo/{id}`);
  - `bruto` com os campos lidos.
- As funções puras de parse ficam testáveis isoladamente: `ler_lista(html) -> list[LinhaLista]` e `ler_detalhe(html) -> Detalhe`.

- [ ] **Step 1:** Escrever os testes que falham com as amostras:
  - a lista p1 tem 20 linhas, e a primeira traz número, objeto e datas esperados (copiar os valores exatos da amostra para o teste);
  - o detalhe 345/26 traz objeto, propostas e os anexos esperados;
  - `extrair` produz o normalizado completo;
  - uma lista sem `.edt-results-count` gera `ErroFonte`;
  - **anexo novo muda o hash** (Review Focus 4): extrair duas vezes, com o detalhe alterado por um anexo a mais, e comparar via `registrar_normalizados`, que atualiza;
  - um teste de `coletar` com cliente falso e duas páginas de lista confere as URLs e a ordem.
- [ ] **Step 2:** Rodar. Esperado: FAIL.
- [ ] **Step 3:** Implementar. Registrar a escolha do parser com uma linha de justificativa.
- [ ] **Step 4:** Rodar o teste de rede `test_sanepar_real` (`uv run pytest -m rede -k sanepar`). Ele coleta só a página 0 de Abertura e 1 detalhe e confere que o parse funciona contra o site atual. Esperado: PASS.
- [ ] **Step 5:** Rodar a suíte e a qualidade. Commit `feat(coleta): coletor da Sanepar (Obras e Engenharia)`.

### Task 5: Coletor Compesa

**Files:**
- Create: `src/radar/coleta/compesa.py` e `tests/dados/fontes/compesa-p1-reduzida.js.txt` e `compesa-p2-reduzida.js.txt`, reduzidas a cerca de 10 registros cada, a partir das amostras completas já em `tests/dados/fontes/`. As completas são apagadas no mesmo commit.
- Test: `tests/unit/test_compesa.py`, `tests/integration/test_compesa_real.py` (marca `rede`)

**Interfaces:**
- `class FonteCompesa` implementa `FonteRetrato`, com `nome = "compesa"`. O `minimo_registros` é justificado no relatório: a lista inteira tem centenas de linhas, então um valor como 50 é razoável.
- **`coletar`:** segue o fluxo do `2026-11-26-fonte-compesa.md`. A ordem é `form.jsp` → `openform.do` → `gridEdit.do`, e em seguida `navigate.do?param=next` e `gridEdit.do?param=refreshpage` até não vir linha nova. Tudo é GET, com o cookie jar do `ClienteEducado`. Uma resposta sem `data_` gera `ErroFonte("layout")`.
- **`extrair`:**
  - lê o array JavaScript **sem executar JS**, com parse tolerante a aspas simples e escapes `\x27`;
  - normaliza a data **por página**: a página 1 vem em `dd/MM/yyyy` e as seguintes em `MM/dd/yyyy`. O formato fica guardado na `PaginaColetada` ou é deduzido pela ordem;
  - acerta o encoding (ISO-8859-1 ou UTF-8, conforme o header);
  - situação pelo mapa da Global Constraint;
  - campos: `uf = "PE"`, `orgao_nome = "COMPESA"`, CNPJ se estiver na amostra, `municipio`, `valor_estimado` (custo estimado), `id_na_fonte = lc_chave`;
  - `link_origem = None`, para cair no link de reserva da API.

- [ ] **Step 1:** Gerar as amostras reduzidas. Escrever os testes que falham:
  - parse da p1 reduzida (chave, objeto, município, custo, data `dd/MM`);
  - **na p2 a data `MM/dd` sai certa** (Review Focus 3), com um caso em que dia é menor ou igual a 12 e os dois formatos dariam datas diferentes;
  - mapa de situações, incluindo uma desconhecida, que vira `outra` e gera log;
  - acentos do objeto corretos;
  - resposta sem `data_` gera `ErroFonte`;
  - `coletar` com cliente falso confere a sequência de URLs e o uso de GET.
- [ ] **Step 2:** Rodar. Esperado: FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** Rodar o teste de rede `test_compesa_real` (`-m rede -k compesa`). Ele coleta só a p1 e confere o parse. Esperado: PASS.
- [ ] **Step 5:** Rodar a suíte e a qualidade. Commit `feat(coleta): coletor da Compesa`.

### Task 6: Rodada real e fechamento (controlador, com o Augusto)

- [ ] **Step 1:** Rodar `uv run alembic upgrade head` no banco local e conferir o Review Focus 5: o `filtrar` continua com 24 editais do PNCP.
- [ ] **Step 2:** Rodar `uv run radar atualizar --fontes sanepar,compesa` e depois `radar auditar`. Registrar quantos editais vieram de cada fonte, quantos batem com o filtro, e o tempo de cada fonte.
- [ ] **Step 3:** Revisar com o Augusto os editais da Sanepar e da Compesa que batem com o filtro.
- [ ] **Step 4:** Regerar o `exemplo.json` e republicar o protótipo no mesmo link.
- [ ] **Step 5:** Escrever `docs/etapas/03-coletores.md`: o que foi feito, o `EXPLAIN` antes e depois, os números, e 5 perguntas de entrevista (lista do spec). Atualizar o checklist.
- [ ] **Step 6:** Abrir o PR para a `main`. O merge só acontece com o OK do Augusto.
