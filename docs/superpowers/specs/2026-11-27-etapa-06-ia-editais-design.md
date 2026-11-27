# Etapa 6: exigências dos editais com IA, comparação com o perfil e evals

Data: 2026-11-27. Decisões do Claude com autonomia dada pelo Augusto; revisar. Tema 4 da trilha (IA aplicada).

## Por que

O radar é um projeto de estudo público. Esta etapa existe para que o Augusto entenda e saiba defender, em
entrevista e em vídeo, **extração estruturada de documento longo com LLM, divisão em trechos e junção,
ancoragem contra alucinação, provedor de modelo trocável, cache de respostas e evals com gabarito feito à
mão** (precisão e recall por campo, replay sem custo no CI, comparação de modelos). "IA aplicada" é a lacuna
do tema 4 em `~/carreira/projetos/radar-licitacoes.md`: "extração de exigências de PDF longo, comparação com
o perfil, evals com gabarito".

Para o usuário (a empresa de serviços de saneamento), é o item 2 do escopo da v1: ler o edital, extrair as
exigências de habilitação e dizer se a empresa atende, com o trecho e a página de cada exigência para
conferir. Hoje o radar só diz *que* o edital existe e casa com uma linha de serviço.

Estado de partida:

- `src/radar/analise/` existe e está vazio; o import-linter já o trata como módulo de domínio independente
  de `coleta` e `editais`.
- Os editais do PNCP têm `id_na_fonte` = `numeroControlePNCP` (`<cnpj>-1-<sequencial>/<ano>`); daí saem
  `cnpj/ano/sequencial` do endpoint público de arquivos (testado sem autenticação, 200 OK, PDF de 3 MB;
  `~/carreira/projetos/tema4-ia-editais.md`, seção 1).
- Bedrock: **Claude não está liberado nesta conta** (AccessDeniedException em `us.anthropic.claude-haiku-5-5`,
  2026-11-27). Amazon Nova Lite (`amazon.nova-lite-v1:0`) responde in-region em us-east-2 pela Converse API.
  Também aparecem on-demand in-region: `openai.gpt-oss-120b-1:0`, `qwen.qwen3-32b-v1:0`,
  `mistral.ministral-3-14b-instruct` (`~/carreira/projetos/radar-decisoes-madrugada.md`). Perfis `us.` e
  `global.` são cross-region e ficam proibidos (o plano FREE não aceita recursos multi-região).

## Objetivos

1. Para cada edital **do PNCP que casa com o filtro e está aberto**, baixar os documentos do edital, extrair
   o texto por página e guardar o PDF e o texto.
2. Extrair as **exigências** (esquema abaixo) com um LLM no Bedrock, em trechos, com saída estruturada
   validada por Pydantic. Cada exigência guarda o trecho literal e a página.
3. **Comparar** as exigências com um **perfil da empresa** versionado (`config/perfil.toml`, só valores de
   exemplo) por **regras determinísticas**, com veredito `apto | inapto | verificar` e o motivo de cada item.
4. Expor a análise pela API (`GET /api/analises/{id_publico}`), sem mudar a tela nesta etapa.
5. **Evals**: gabarito JSON por edital feito pelo Augusto, harness com precisão e recall por campo, replay sem
   custo no CI e rodada real manual comparando 2 a 3 modelos in-region.
6. Custo de centavos por edital, dentro do orçamento de US$ 5/mês, com teto próprio da IA e o alarme que já
   existe.
7. Local e testes funcionam sem AWS e sem rede.
8. A etapa fecha com a nota `docs/etapas/06-ia-editais.md` (números, lições, 5 perguntas de entrevista) e o
   roteiro `docs/videos/06-ia-editais.md`.

## Não objetivos

- **Tela das exigências.** A lista de componentes e de rotas do front é fechada (`frontend/CLAUDE.md`), e os
  arquivos que a fecham estão em `GUARDADOS` de `frontend/scripts/contrato-guardado.mjs`. Mostrar a análise
  na página de detalhe fica para uma etapa 6b, depois de o Augusto decidir se cabe nos componentes
  existentes. Nesta etapa a API e o `openapi.json` mudam; a tela não.
- **Sanepar e Compesa.** Os anexos delas vêm do site de cada companhia, por um caminho que ninguém testou.
  Só o PNCP nesta etapa (ver "Decisões arriscadas").
- OCR de páginas escaneadas (docling, tesseract). Página sem texto é contada e o edital vai para `verificar`.
- zip, docx e outros formatos que não são PDF: o documento fica com status `nao_pdf`.
- Retificação de edital: um edital já analisado não é reanalisado se mudar de documento (só com `--refazer`).
- pgvector e embeddings. O fluxo é "um documento entra, um JSON sai"; busca semântica só faria sentido depois
  de o gabarito mostrar falha medida do filtro por palavra-chave.
- instructor, LangChain, promptfoo e inspect-ai. Pydantic, pytest e JSON bastam (pesquisa, seção 5).
- Implementar a chamada à API da Anthropic. A interface `Provedor` comporta um adaptador desses; ele não é
  escrito nesta etapa.

## Antiobjetivos (falharia mesmo funcionando)

- **O LLM decidir se a empresa está apta.** O modelo só extrai; a comparação é código determinístico e
  testado.
- **Exigência sem lastro**: guardar um valor cujo trecho citado não existe no texto extraído.
- **Gabarito feito pelo modelo que está sendo avaliado**, ou métrica divulgada sobre gabarito não conferido.
- **CI chamando a API** (custo, segredo e resultado não determinístico).
- Inferência fora de us-east-2 (perfis `us.`, `global.`, `eu.`, `apac.`), credencial fixa, custo sem teto.
- Dado privado da empresa no repositório (atestados reais, capital real, propostas).
- Mascarar o alarme "coletor não rodou" (ver "Onde roda").

## Restrições

- AWS: projeto da nova experiência, plano FREE, **us-east-2**, SCPs gerenciadas, orçamento de US$ 5/mês com
  alertas (`infra/prod/orcamento.tf`), alarmes no SNS `radar-alertas`.
- IAM: `bedrock:InvokeModel` (a Converse usa essa ação) **só** nos ARNs
  `arn:aws:bedrock:us-east-2::foundation-model/<id>` dos modelos escolhidos, e só na role da tarefa de
  análise. Nenhum `bedrock:*`, nenhum `Resource = "*"`, nenhum ARN de `inference-profile`.
- Neon no plano grátis (0,5 GB): texto extraído e PDFs não vão para o banco.
- Python 3.13, mypy estrito, import-linter: `radar.analise` não importa `coleta` nem `editais`; quem orquestra
  é a porta de entrada (`radar.cli`).
- **Arquivos guardados**: o desenho não toca nenhum (nem `.github/workflows/deploy.yml`, nem `.gitignore`).
  Se a implementação concluir que precisa tocar algum, para e pede autorização ao Augusto; o trailer
  `Contrato-Front: aprovado` não é usado nesta etapa.
- Merge na main só por PR, com os checks obrigatórios.

## Abordagens consideradas

### Quando analisar

- A. **Novo tipo de mensagem na outbox** (`edital_registrado`), consumido pela Lambda `radar-processador`.
  Ganha nova tentativa e DLQ de graça. Contra: a decisão "casa com o filtro e está aberto" é uma consulta
  sobre o estado atual (o `filtro.toml` muda, o prazo vence), não um fato de uma página; a Lambda tem 120 s
  e a primeira chamada com esquema novo pode levar ~90 s; a role do processador ganharia permissão de
  Bedrock; e um retry automático de chamada paga multiplica custo sem ninguém ver.
- B. **No fim do `radar atualizar`.** Simples, mas os editais novos ainda estão na fila quando ele termina
  (o consumidor é assíncrono desde a 4a), e o coletor ganharia permissão de Bedrock.
- **C. Comando `radar analisar`, agendado à parte (09:00, três horas depois da coleta).** Escolhida. A seleção
  é uma consulta (`filtrar` com `somente_abertos=True`), idempotente: o que já foi analisado com o mesmo
  modelo e prompt é pulado. Repetir a rodada não gasta nada. A permissão de Bedrock fica numa role só da
  análise. O tema 4 não precisa repetir o tema 2.

### Saída estruturada

- A. JSON livre no texto + parse. Frágil.
- B. instructor. Uma dependência a mais para fazer o que a Converse e o Pydantic já fazem.
- **C. Converse com esquema JSON (`outputConfig.textFormat`) ou, se o modelo não aceitar, ferramenta única com
  `toolChoice` forçado; validação Pydantic na volta.** Escolhida. O modo de cada modelo sai da sonda
  (Task 1 do plano) e fica em `config/ia.toml`.

### Formato do que o modelo devolve

- A. O objeto final de exigências direto (um campo por exigência). Esbarra nos limites do esquema do Bedrock
  (no máximo 12 propriedades opcionais e 16 uniões/nullable; pesquisa, seção 4) e obriga o modelo a resolver
  conflitos entre trechos que ele não vê.
- **B. Lista plana de achados** `{campo, valor, numero, unidade, trecho}` por trecho; a normalização e a
  junção são código. Escolhida: um só nullable no esquema, junção testável sem LLM, e cada achado é
  ancorado.

## Desenho

### Quais editais

- Fonte `pncp`, resultado de `filtrar(sessao, criterios, agora, somente_abertos=True)` (linha de serviço, UF,
  modalidade, aberto com prazo no futuro), na ordem do prazo mais próximo.
- Pula os que já têm `analise_edital` com o mesmo `modelo` e `versao_prompt`.
- No máximo `max_editais_por_rodada = 10` por execução. Com ~24 abertos que casam e poucos novos por dia,
  a primeira rodada leva três dias e as seguintes pegam só os novos.
- `radar analisar --edital <id_publico>` analisa um edital pela chave, mesmo fechado (para evals e
  demonstração). `--refazer` ignora a análise existente.

### Documentos

- Listagem: `GET https://pncp.gov.br/pncp-api/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos`. Download
  pela `url` de cada documento. Sem token; `User-Agent` do projeto; 1 s entre requisições; 3 tentativas
  com espera em 429, 5xx e falha de transporte; timeout de 120 s.
- Documentos analisados: `tipoDocumentoNome` normalizado (sem acento, minúsculo) contendo `edital` ou
  `termo de referencia`, no máximo 3 por edital, na ordem de `sequencialDocumento`. Os ids de tipo do PNCP
  não foram conferidos além do `2 = Edital` [N]; a rodada real confere.
- Defesas: magic bytes `%PDF` (senão `nao_pdf`); mais de 20 MB → `grande_demais`, sem baixar o resto.
- Extração: **pypdf** (BSD, puro Python, sem torch), uma string por página. Página com menos de 200
  caracteres conta como sem texto; mais da metade das páginas sem texto → `sem_texto` (OCR fica fora).
  pypdf em vez de pdfplumber por ser uma dependência só; a troca fica isolada em uma função, e o eval mede
  se precisa.
- **Armazém**: PDF em `documentos/<sha256>.pdf` e texto em `textos/<sha256>.json` (lista de páginas).
  Local: pasta `~/.radar/documentos` (fora do repositório, para não mexer no `.gitignore` guardado). AWS:
  bucket S3 `radar-documentos-<conta>` (privado, SSE-S3, expira em 180 dias). É o "dado bruto separado do
  tratado" do tema 1, e o S3 volta como o checklist prometeu. O banco guarda só os metadados.

### Esquema das exigências (`radar.analise.exigencias`)

O modelo devolve, por trecho, `{"achados": [Achado]}`, com
`Achado = {campo, valor: string, numero: number | null, unidade: string, trecho: string}`; todos os
campos `required`, `additionalProperties: false`. `campo` é um destes:

| campo | valor | numero / unidade | vira em `Exigencias` |
|---|---|---|---|
| `atestado_tecnico` | descrição do serviço | quantidade mínima / `km`, `m`, `un`, `leitura`, `ligacao`... | `atestados: list[Atestado]` |
| `capital_social_minimo` | `""` ou `percentual` | R$ ou % do valor estimado | `capital_social_minimo: ValorEvidencia` |
| `patrimonio_liquido_minimo` | idem | idem | `patrimonio_liquido_minimo` |
| `garantia_proposta` | modalidade, se houver | % | `garantia_proposta_percentual` |
| `garantia_contratual` | idem | % | `garantia_contratual_percentual` |
| `visita_tecnica` | `obrigatoria` / `facultativa` / `nao_exigida` | — | `visita_tecnica: CategoriaEvidencia` |
| `consorcio` | `permitido` / `vedado` | — | `consorcio` |
| `me_epp` | `exclusiva` / `cota` / `ampla` | — | `me_epp` |
| `prazo_execucao` | — | número / `dias`, `meses` | `prazo_execucao_dias` |
| `documento_habilitacao` | um de `DOCUMENTOS` | — | `documentos: set[str]` |

`DOCUMENTOS = certidao_federal, certidao_fgts, certidao_trabalhista, certidao_estadual, certidao_municipal,
certidao_falencia, balanco_patrimonial, registro_conselho, contrato_social, declaracao_menor, outro`.

`ValorEvidencia = {numero: Decimal | None, trecho, pagina, conflito: bool, observacao}` e
`CategoriaEvidencia = {valor, trecho, pagina, conflito}`. Campo `None` em `Exigencias` = o edital não trata
disso (ou a extração não achou; o eval mede a diferença).

### Do achado à exigência (determinístico)

1. **Ancoragem**: o `trecho` do achado, com espaços normalizados, tem de ser substring do texto do trecho
   enviado. Se não for, o achado é descartado e contado em `achados_descartados`. A **página** sai da
   posição do trecho no texto (marcadores `[[pagina N]]`), nunca do modelo.
2. **Normalização**: valor fora do vocabulário do campo é descartado e contado; unidades por uma tabela
   (`metros` → `m` → km/1000, `unidades`/`hidrometros` → `un`...); `meses` → dias × 30; `percentual` de capital
   ou patrimônio vira R$ quando o `valor_estimado` do edital existe, senão `numero = None` com observação.
3. **Junção** entre trechos e documentos: achado repetido (mesmo campo, valor e número) conta uma vez;
   números diferentes no mesmo campo ficam com **o mais restritivo** (maior) e `conflito = True`; categorias
   com precedência `obrigatoria > facultativa > nao_exigida`, `vedado > permitido`,
   `exclusiva > cota > ampla`, também com `conflito`; atestados e documentos por união.

### Perfil da empresa (`config/perfil.toml`)

Arquivo versionado, **só com valores fictícios de exemplo** e um comentário dizendo isso. Campos: `porte`
(`me | epp | demais`), `capital_social`, `patrimonio_liquido`, `garantia_maxima_percentual`,
`faz_visita_tecnica`, `depende_de_consorcio`, `documentos` (lista de `DOCUMENTOS`) e `[[atestados]]` com
`descricao`, `termos` (palavras que casam com a descrição exigida), `quantidade` e `unidade`. Exemplos
genéricos das quatro linhas (pesquisa de vazamentos em km, troca de hidrômetros em un, leituras, rede em m).
Os dados reais ficam fora do repositório (`RADAR_CONFIG_PERFIL` aponta para outro arquivo).

### Comparação (`radar.analise.comparacao`)

Uma função pura `comparar(exigencias, perfil, situacao_documentos) -> Comparacao`. Cada item tem `campo`,
`situacao` (`atende | nao_atende | verificar | informativo`), `exigido`, `perfil` e `motivo`:

- capital e patrimônio: perfil ≥ exigido → `atende`; menor → `nao_atende`; `numero` nulo → `verificar`.
- garantias: ≤ `garantia_maxima_percentual` → `atende`; acima → `verificar` (é decisão financeira, não
  inaptidão).
- visita obrigatória: `faz_visita_tecnica` → `atende`, senão `nao_atende`.
- consórcio vedado e `depende_de_consorcio` → `nao_atende`; outros casos `atende`.
- ME/EPP `exclusiva` e porte `demais` → `nao_atende`; `cota` → `atende` com motivo "concorre na cota ampla".
- prazo de execução → `informativo`.
- documentos exigidos que o perfil não lista → `verificar` (dá para tirar), com a lista.
- cada atestado: há atestado do perfil com algum `termo` na descrição exigida (sem acento, minúsculo), mesma
  unidade normalizada e quantidade ≥ exigida → `atende`; senão `verificar`. Nunca `nao_atende`: o
  casamento por termo é aproximado.
- qualquer `conflito` → `verificar`.

Veredito: algum `nao_atende` → `inapto`; senão algum `verificar`, análise `incompleta` (trecho com resposta
inválida, documento truncado, `sem_texto`, `nao_pdf`) → `verificar`; senão `apto`. O texto da API diz "segundo
o que foi extraído": exigência que a extração perdeu vira `apto` falso, e é isso que o recall mede.

### PDFs longos

- Texto com marcadores `[[pagina N]]`. Trechos de até **60.000 caracteres** (~15 a 20 mil tokens em
  português), cortados em fim de página, com **1 página de sobreposição** (a junção absorve o achado
  repetido). Página maior que o limite é cortada por caracteres.
- No máximo **15 trechos** por edital (~900 mil caracteres). Além disso, analisa os 15 primeiros e marca
  `truncado` (veredito `verificar`).
- Chamadas em sequência, `temperature = 0`, `maxTokens = 4096`. Resposta que não valida no Pydantic: o trecho
  conta em `trechos_invalidos`, a análise fica `incompleta`, **sem nova tentativa** (nova tentativa com o
  mesmo pedido daria a mesma resposta em cache e quebraria o replay).

### Provedor trocável (`radar.analise.provedor`)

- `Pedido(modelo, sistema, texto, esquema, max_tokens)`, `Resposta(texto, tokens_entrada, tokens_saida,
  latencia_ms)`, `class Provedor(Protocol): def gerar(self, pedido) -> Resposta`.
- `ProvedorBedrock(regiao="us-east-2")`: `bedrock-runtime` Converse, modo de saída por modelo
  (`json_schema` ou `ferramenta`, de `config/ia.toml`); ignora blocos de raciocínio (gpt-oss, qwen) e lê só o
  texto ou o `toolUse.input`; retries do boto3 em modo `adaptive`, 5 tentativas, timeout de leitura 300 s.
  **Recusa** id de modelo com prefixo `us.`, `global.`, `eu.`, `apac.` ou região diferente de us-east-2
  antes de qualquer chamada.
- `ProvedorAnthropic` não existe: o `Protocol` é a porta. Quando houver chave, um adaptador novo implementa
  `gerar` e entra em `config/ia.toml`; nada mais muda.

### Cache das respostas (`radar.analise.cache`)

- Chave = sha256 do JSON ordenado de `{modelo, modo_saida, sistema, esquema, max_tokens, sha256(texto)}`.
  Mudou prompt, esquema, modelo ou trecho, mudou a chave.
- `versao_prompt` = os 12 primeiros hex do sha256 de `sistema + esquema`; vai para `analise_edital`.
- `ProvedorComCache(provedor | None, cache, modo)`: `gravar` (acerto devolve do cache; falta chama e grava) ou
  `replay` (falta levanta `RespostaAusente(chave)`; nunca chama).
- Dois cofres: `CacheBanco` (tabela `resposta_llm`, produção) e `CacheArquivos` (`evals/respostas/<modelo>/
  <chave>.json`, commitado; é o que o CI usa).

### Custos e teto

- Preços por modelo em `config/ia.toml` (US$ por milhão de tokens, entrada e saída), conferidos na sonda.
  Custo = tokens × preço; acerto de cache custa 0.
- Ordem de grandeza (Nova Lite ~US$ 0,06 entrada e ~0,24 saída por MTok [N]): um edital de 80 páginas
  (~80 mil tokens, 5 trechos, ~5 mil de saída) custa **~US$ 0,006**. Pior caso de 10 editais/dia por 30 dias:
  ~US$ 1,80/mês.
- **Teto próprio**: `teto_mensal_usd = 2.0` em `config/ia.toml`. Antes de cada chamada real, gasto do mês
  (soma de `resposta_llm.custo_usd` do mês UTC) + estimativa do pedido (caracteres/3 × preço de entrada +
  `max_tokens` × preço de saída) acima do teto → `TetoAtingido`; a rodada para, loga `teto_ia_atingido`
  e sai com 1. O orçamento de US$ 5 e seus alertas continuam valendo por cima.

### Dados (migração `0006_analise`)

- `documento_edital`: `id`, `fonte`, `id_na_fonte`, `sequencial`, `titulo`, `tipo_nome`, `url`, `sha256`,
  `tamanho`, `paginas`, `paginas_sem_texto`, `status` (CHECK `extraido | sem_texto | nao_pdf | grande_demais |
  falha`), `erro`, `baixado_em`. Único em `(fonte, id_na_fonte, sequencial)`.
- `analise_edital`: `id`, `fonte`, `id_na_fonte`, `modelo`, `versao_prompt`, `hash_documentos`, `veredito`
  (CHECK), `incompleta`, `exigencias` (JSONB), `comparacao` (JSONB), `trechos`, `trechos_invalidos`,
  `achados_descartados`, `truncado`, `tokens_entrada`, `tokens_saida`, `custo_usd`, `criada_em`. Único em
  `(fonte, id_na_fonte, modelo, versao_prompt)`; regravar é upsert.
- `resposta_llm`: `chave` (PK), `modelo`, `resposta`, `tokens_entrada`, `tokens_saida`, `custo_usd`,
  `latencia_ms`, `criada_em`; índice em `criada_em`.
- Sem chave estrangeira para `edital`: as tabelas são de `radar.analise`, que não importa `editais`; uma
  análise órfã não faz mal (editais não são apagados).

### Onde roda

- Mesma imagem e **mesma família de task** do coletor (`radar-coletor`), com override na agenda: comando
  `["analisar"]`, role `radar-analisador-task` e variáveis `RADAR_IA_PROVEDOR=bedrock`,
  `RADAR_DOCUMENTOS=s3://<bucket>/`. Assim o deploy, que já registra revisões novas da família, cobre a
  análise sem mexer em `deploy.yml` (guardado).
- Agenda `radar-analisar-diario`, `cron(0 9 * * ? *)` em America/Sao_Paulo, pelo alvo universal do Scheduler
  (`arn:aws:scheduler:::aws-sdk:ecs:runTask`), que aceita `Overrides` com `TaskRoleArn` e
  `ContainerOverrides` [N: conferir no `terraform apply` e numa execução]. A role do Scheduler ganha
  `iam:PassRole` na role nova.
- **Cuidado com o log group compartilhado** (`/radar/coletor`): o alarme "coletor não rodou" conta linhas
  `resultado:`. O `analisar` nunca imprime `resultado:`; imprime `analise: ...`. Teste trava isso.
- Falha = código de saída ≠ 0, que a regra `radar-coletor-falhou` (qualquer task do cluster) já manda ao SNS.
  Nenhum alarme novo.
- Local: `RADAR_IA_PROVEDOR=desligado` (padrão) faz `radar analisar` sair com 2 e mensagem clara.

### API

- `GET /api/analises/{id_publico:path}` → `AnaliseOut` (`id_publico`, `modelo`, `versao_prompt`,
  `criada_em`, `veredito`, `incompleta`, `itens` com `campo/situacao/exigido/perfil/motivo/trecho/pagina`,
  `documentos` com `titulo/url/paginas/status`) ou 404. Rota própria, e não `/api/editais/{id}/analise`,
  porque o `id_publico` do PNCP tem `/` e o conversor `path` da rota de detalhe engoliria o sufixo.
- Leitura pela raiz de `radar.analise` (`obter_analise`), com o modelo padrão de `config/ia.toml`.
- Atrás do mesmo autorizador JWT (`/api/{proxy+}`). `frontend/openapi.json` e `frontend/src/dados/api.d.ts`
  são regerados (não são guardados).

### Evals (`evals/` e `radar.analise.evals`)

- **Corpus**: 12 editais do PNCP com PDF de texto (podem estar encerrados), das quatro linhas de serviço;
  pelo menos 2 sem exigência de capital nem de atestado com quantidade, e 1 escaneado (espera `sem_texto`).
  8 no conjunto `ajuste` e 4 no `teste`, que ninguém olha ao mexer no prompt.
- `evals/textos/<slug>.json`: as páginas extraídas (dado público, commitado; o PDF não vai para o git).
  `slug` = `id_publico` com `~` e `/` trocados por `_`.
- `evals/gabarito/<slug>.json`: `{edital, documentos: [{sequencial, url, sha256}], conjunto, conferido,
  conferido_por, conferido_em, observacoes, exigencias}`, com `exigencias` no **mesmo modelo Pydantic** que o
  pipeline produz (com trecho e página de cada valor).
- **Quem faz o gabarito**: o Claude (sessão de coordenação, lendo o texto, sem usar os modelos avaliados)
  escreve um **rascunho** com `conferido: false`; **só o Augusto confere** e muda para `true`, com o nome e a
  data. `radar avaliar validar` confere esquema e que todo trecho do gabarito existe no texto.
- **Métricas** por campo e por conjunto (micro-média sobre os editais):
  - escalares (capital, patrimônio, garantias, prazo): igual com tolerância relativa de 1%. Valor do modelo
    presente e igual → VP; presente e (gabarito ausente ou diferente) → FP; gabarito presente e (modelo
    ausente ou diferente) → FN.
  - categóricos (visita, consórcio, ME/EPP): igualdade exata, mesma contagem.
  - `documentos`: por item do conjunto.
  - `atestados`: par `(unidade normalizada, quantidade ±1%)`; a descrição não entra no par.
  - precisão, recall, e **alucinação** = FP onde o gabarito é ausente ÷ casos de gabarito ausente.
  - por modelo: custo total, latência p50/p95 por trecho, `achados_descartados` e `trechos_invalidos`.
- **Relatório**: tabela Markdown impressa por `radar avaliar rodar`. Gabarito com `conferido: false` entra
  numa seção separada com o aviso "rascunho não conferido: métrica sem valor".
- **CI (replay, custo zero)**: um teste roda o harness em modo `replay` sobre `evals/` para o modelo padrão.
  Falta de resposta em cache **falha** com a lista de chaves e a instrução "rode `radar avaliar rodar
  --modo gravar`". Com gabarito conferido, compara com `evals/linha_de_base.json` e falha se o F1 de algum
  campo cair mais de 0,05 (protege normalização, junção e ancoragem, que são código).
- **Rodada real (manual, máquina do Augusto, perfil AWS `radar`)**: `radar avaliar rodar --modo gravar` para
  Nova Lite, gpt-oss-120b e qwen3-32b (ministral se sobrar); respostas commitadas. 12 editais × 3 modelos ×
  ~US$ 0,01 = ~US$ 0,40. `--teto-usd 1.0` por rodada.

### Logs

Nomes fixos: `analise_concluida`, `analise_sem_texto`, `achado_descartado`, `trecho_invalido`,
`teto_ia_atingido`, `documento_falhou`. A linha final do comando é `analise: <n> analisados, <n> pulados,
<n> falhas, US$ <custo>`.

## Decisões arriscadas

1. **Só PNCP.** Os editais mais relevantes para o usuário (pesquisa de vazamentos na Sanepar) ficam de fora
   até alguém testar o download dos anexos da Sanepar. Custo se errado: a demonstração mostra menos do que
   poderia; o pipeline já recebe `AlvoAnalise` genérico, então a fonte nova é um cliente de arquivos a mais.
2. **Modo de saída e preço de cada modelo ainda não conferidos.** Nova Lite e os outros podem não aceitar
   `outputConfig.textFormat`; por isso a sonda (US$ 0,01) vem antes de qualquer código, e o modo
   `ferramenta` é o plano B. Custo se errado: um adaptador a mais.
3. **Agenda pelo alvo universal do Scheduler com override na família do coletor**, para não tocar no
   `deploy.yml` guardado. Divide o log group com o coletor (o alarme de "não rodou" depende de o `analisar`
   não imprimir `resultado:`). Custo se errado: família de task própria, que exige mudar `deploy.yml` com
   autorização do Augusto.
4. **"Nada extraído" vira "não se aplica".** Exigência que a extração perdeu produz `apto` falso. Mitigação:
   recall medido por campo e o texto "segundo o que foi extraído".

## Testes (o que provar)

- Ancoragem: trecho parafraseado é descartado e contado; página sai da posição, não do modelo.
- Junção: valores diferentes ficam com o mais restritivo e `conflito`; duplicata da sobreposição conta uma vez.
- Comparação: uma tabela de casos por regra, inclusive `conflito → verificar` e o veredito geral.
- Documentos: não PDF, grande demais, escaneado (sem chamada ao LLM); trechos com sobreposição e corte de
  página gigante.
- Provedor: modelo `us.`/`global.` recusado sem chamada; resposta real gravada na sonda é lida nos dois
  modos; blocos de raciocínio ignorados.
- Cache: mudar prompt muda a chave; replay com falta levanta `RespostaAusente`; acerto custa 0.
- Teto: atingido no meio da rodada para antes da chamada e não grava análise.
- Pipeline e CLI: edital já analisado é pulado; `--refazer` regrava; saída sem `resultado:`; provedor
  desligado sai com 2.
- API: 404 sem análise; id com `/` funciona.
- Evals: métricas de casos montados à mão (VP/FP/FN conhecidos); replay no CI sem rede.
- Infra estática: IAM do Bedrock só com `foundation-model/` em us-east-2, sem curinga.

## O que fica com quem constrói

- Texto exato do prompt (em português, com o vocabulário de cada campo e a regra "copie o trecho literal").
- Biblioteca de fake do S3 e do `bedrock-runtime` nos testes (stub do botocore ou fake próprio).
- Mensagens da CLI além da linha final fixada acima.
- Formato da tabela do relatório de evals.
