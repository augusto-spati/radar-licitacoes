# Etapa 7b.2: preço estimado das planilhas orçamentárias do PNCP

Data: 2026-11-27. Continuação da 7b (`2026-11-27-etapa-07b-precos-design.md`, seção "7b.2"), que já está construída na parte 7b.1 (TCE-PE, só Pernambuco). Este documento substitui o esboço da 7b.2 daquela spec com base num spike medido em editais reais de SP e MG. Decisões sem o Augusto marcadas **[decisão do Claude]**. Pesquisa anterior: `~/carreira/projetos/radar-pesquisa-precos-dados.md` (seção 5) e `radar-pesquisa-precos-decisoes.md`.

## Por que

A 7b.1 dá estimado e contratado por item, mas só em PE. A FR Saneamento atua sobretudo em SP e MG, onde não há fonte estruturada recente (AUDESP com 8+ meses de atraso, SICOM-MG com reCAPTCHA). O que existe é a planilha orçamentária anexada ao edital no PNCP: o preço **estimado** pelo órgão, item a item, com código de referência (SINAPI, SABESP, CDHU), data-base e BDI. É o número que diz "quanto o órgão acha que custa o metro de rede DN 200" na região onde ele disputa.

## Objetivos

1. Para editais de saneamento do PNCP (SP e MG primeiro, depois PR), achar a planilha orçamentária entre os documentos e extrair as linhas `item | código | fonte | descrição | unidade | quantidade | preço sem BDI | preço com BDI | data-base`.
2. Determinístico primeiro (PDF com texto, XLSX); IA só no que não parsear, com teto de custo próprio dentro do teto da IA.
3. Gravar em `preco_item` só as linhas que casam com um serviço-padrão, com origem própria, e alimentar a mesma tela "Preços de referência" da 7b.1.
4. Quando a 7a trouxer o resultado da contratação, calcular o "estimado com desconto" (aproximação, como na 7b).

## Não objetivos

- Preço contratado fora de PE (o PNCP não tem; medido na pesquisa: 0 de 11 obras).
- Buscar planilha fora do PNCP (portal do município, 1doc, Licitar Digital) na v1. Ver pergunta 1.
- OCR de PDF escaneado; RAR (no spike o único RAR era cópia do ZIP que o continha).
- Composições de preço unitário (CPU): insumo por hora de equipamento não é serviço comparável.

## Anti-objetivos

Os da 7b: não misturar estimado com contratado, não comparar unidades ou DNs diferentes, não mostrar "estimado com desconto" sem dizer que é aproximação. E mais: não gravar preço que a conta da própria linha não confirma (quantidade × unitário ≈ total).

## O que o spike mediu

Nove concorrências (modalidades 4 e 5) de obra de saneamento publicadas no PNCP entre 27/10 e 26/11/2026, 7 em SP e 2 em MG, escolhidas pelo objeto (adutora, coletor, rede, esgotamento). Todos os documentos baixados pela API pública (`/pncp-api/v1/.../arquivos`), ZIPs abertos. Parser de estudo fora do repositório (pypdf e openpyxl, sem IA).

| Edital (UF) | Onde está a planilha | Formato | Determinístico | Código / data-base / BDI |
|---|---|---|---|---|
| Cosmópolis, adutora (SP) | ZIP de anexos | XLSX + PDF gêmeo | 40/40 linhas nos dois | SINAPI/SABESP/SICRO; base no cabeçalho por tabela; BDI 24,18% serviço e 14,02% material |
| Santa Bárbara d'Oeste, adutora (SP) | ZIP (com RAR duplicado dentro) | XLSX + PDF gêmeo | 102/102 nos dois | SABESP/SINAPI/CDHU/cotação; base por linha ("jul/26"); BDI 24,84% e 15,28% |
| Ribeirão Preto, coletor (SP) | ZIP | PDF | 92/92 (páginas 3–4 são o modelo em branco do licitante) | SINAPI/SABESP/CDHU/SICRO/FDE; "ref. ago/26"; custo e preço com BDI |
| Araraquara (DAAE), coletor (SP) | ZIP | PDF com coluna de memorial de cálculo no meio | 31/33 | SINAPI/ORSE/SBC; base por linha; sem e com BDI |
| Ibaté, adutora (SP) | dentro do PDF do edital (4 páginas) | PDF com texto picotado por célula ("505,\n44") | 0 | SINAPI 07/2026 e CDHU ago/2026; BDI 25% |
| São Simão, redes por MND (SP) | dentro do PDF do edital, duas cópias | PDF de composições (insumos em hora) | linhas de insumo, sem preço por serviço | SABESP jan/2026, SINAPI abr/2026; BDI 16% e 26% |
| Votuporanga (SAEV), travessia de esgoto (SP) | **não está no PNCP** (só o edital) | n/a | n/a | n/a |
| João Monlevade (DAE), SAA (MG) | **não está no PNCP** ("arquivo independente") | n/a | n/a | n/a |
| Vargem Alegre, SES (MG) | **não está no PNCP** (página do Anexo III em branco) | n/a | n/a | n/a |

Resumo:

- **Disponível no PNCP em 6 de 9** (67%); nas 2 de MG, 0. Arquivo separado em 4 (todos dentro de ZIP de anexos); dentro do PDF do edital em 2. XLSX só em 2, sempre com um PDF gêmeo de mesmo conteúdo.
- **Determinístico resolve 4 das 6** com recall de 94 a 100%, usando a coerência aritmética da linha (quantidade × um dos unitários ≈ um dos totais, tolerância 0,3%) como critério de linha válida. O PDF gêmeo recuperou 100% das linhas do XLSX nos dois casos.
- **Código de referência, data-base e BDI estão nas 6.** As 6 têm preço sem e com BDI; 3 usam BDI diferente para material e serviço; 2 dão a data-base por linha, 4 no cabeçalho (às vezes uma por tabela: "SINAPI 07/2026, SABESP 01/2026"). Nenhuma SETOP (as de MG não estavam no PNCP).
- **IA necessária em 1 de 6** (Ibaté: texto picotado; nem o modo layout do pypdf junta os números). São Simão não tem preço por serviço: a IA não ajuda.
- **Classificação** (`config/servicos.toml`) nas 4 planilhas parseadas: **5 de 265 linhas** casam um serviço-padrão. Os motivos, em ordem: a descrição não diz água ou esgoto ("TUBO PVC-O DN 250 MM", "Tubo, PVC Ocre, DN 200 - fornec./assent."), porque o contexto está no título da obra ou da seção; "fornec./assent." abreviado é lido como só material (errado: é completo); "Ligação de esgoto completa" não está nos termos (só "ligação predial/domiciliar"); escavação sem faixa ("até 2,20 m", "1ª categoria"); recomposição asfáltica em m³. Com a palavra da linha da obra ("agua"/"esgoto") acrescentada à descrição, 16 de 265, e uma delas errada (perfuração direcional de PEAD 500 casou como assentamento).
- **Volume**: 1.505 concorrências em SP e MG em 30 dias; 19 casam o filtro do radar, cerca de 30 são obra de saneamento lidas uma a uma. Com 2/3 tendo planilha no PNCP e 1/6 dessas pedindo IA: ~20 planilhas por mês, ~3 a 4 por IA.
- **Tamanho**: o ZIP de Santa Bárbara tem 25,6 MB, acima do `LIMITE_BYTES` de 20 MB de `arquivos_pncp`. Editais também se repetem no PNCP (Monlevade 119/120, Amparo 1429/1432: mesmo objeto e valor).

## Custo de IA

Modelo mais barato do `config/ia.toml`: `amazon.nova-lite-v1:0`, US$ 0,06 por milhão de tokens de entrada e US$ 0,24 de saída. Estimativa com caracteres/4 e ~80 tokens de saída por linha extraída:

| Caso | Entrada | Saída | Custo |
|---|---|---|---|
| Ibaté (só as 4 páginas com preço, 6,1 mil caracteres) | ~2,5 mil tokens com o prompt | ~45 linhas, ~3,6 mil | ~US$ 0,001 |
| Planilha grande dentro do edital (13 páginas, 40 mil caracteres) | ~11 mil | ~150 linhas, ~12 mil | ~US$ 0,0035 |
| Pior caso: edital inteiro (250 mil caracteres) | ~63 mil | ~200 linhas, ~16 mil | ~US$ 0,008 |

Por mês, com ~4 planilhas por IA: **~US$ 0,01**. Mesmo se todas as ~30 obras fossem para a IA no pior caso: ~US$ 0,24, 12% do teto de US$ 2. A seleção de páginas por densidade de "R$" (8+ ocorrências na página) achou as páginas da planilha nos dois PDFs de edital e corta a entrada em 35 a 50 vezes.

## Abordagem escolhida **[decisão do Claude]**

### Alvos

Editais `pncp~` do recorte da 7a nas linhas `redes` e `perdas`, modalidades de obra (concorrência; pregão de serviço de engenharia entra se tiver planilha), **na publicação e não só depois de homologados**: a planilha sai com o edital (48 de 49 arquivos na pesquisa), e o "estimado com desconto" é calculado depois, quando a 7a gravar o resultado. Até 10 editais por rodada (config), mais antigos primeiro.

### Achar a planilha

1. Listar os arquivos (cliente de `arquivos_pncp`, mesma cortesia) e abrir ZIPs com os limites de `pacotes` (entradas, tamanho descompactado). Limite do download para esta etapa: 40 MB.
2. Candidatos por nome sem acento: `planilha`, `orcament`, `estimativa de preco`, `po_`, `custo` (fora: `cronograma`, `bdi`, `composic`, `encargos`, `modelo`, `memorial`). XLSX tem preferência sobre o PDF gêmeo; v1 lê só PDF, e openpyxl entra se os evals mostrarem perda no PDF (medido: nenhuma em 2 de 2).
3. Sem candidato por nome: páginas do edital e do TR com 8+ "R$" e cabeçalho com "unid" e "quant".
4. Nada achado: grava o estado `sem_planilha` (para não reprocessar) e conta no log.

### Extrair

- **Determinístico**: linhas do pypdf; uma linha vale quando tem unidade conhecida seguida de quantidade e, no fim, 2 a 7 números em que `quantidade × unitário ≈ total`. O maior total coerente é o com BDI; outro unitário coerente menor é o sem BDI; `%` na linha é o BDI dela. Descrição quebrada: junta as linhas desde o último número de item. Código e fonte por expressão regular conhecida (SINAPI 5–6 dígitos, SABESP `HM\d+`/9 dígitos, CDHU `\d{2}.\d{2}.\d{3}`, SICRO, ORSE, cotação, composição própria). Data-base: coluna da linha ou cabeçalho (mês/ano por tabela).
- **IA**, só se o determinístico achar menos de 5 linhas coerentes nas páginas candidatas: Nova Lite com ferramenta forçada (como a etapa 6), saída `linhas: [{item, codigo, fonte, descricao, unidade, quantidade, preco_sem_bdi, preco_com_bdi, total}]`. A ancoragem não é o trecho literal (o texto vem picotado) e sim a conta: linha sem `quantidade × preço ≈ total` é descartada, e a soma das linhas tem de ficar a ±2% do total da planilha quando ele existir.
- **Teto**: além do teto mensal (`guarda_teto`), teto por edital de US$ 0,02 e por mês da 7b.2 de US$ 0,30 (config). Estimativa acima → não chama, grava `pulado_teto`.
- **Cache**: chave = sha256 da planilha; o mesmo arquivo em outro edital (republicação) não é extraído de novo.

### Classificar

Mudanças em `servicos` (valem também para o TCE-PE; os testes da 7b.1 seguem passando):

- **Contexto da obra**: a linha da obra (água ou esgoto) vem do título da seção da planilha ("EXECUÇÃO DE COLETOR DE ESGOTOS") e, sem ele, do objeto do edital; é passada ao `classificar` como contexto, usado só quando a descrição não cita nem água nem esgoto. Linha que cita os dois continua fora.
- Escopo: "fornec./assent.", "forn. e assent." → completo.
- Ligação: "ligação de água/esgoto" (sem "predial") entra; "completa" não exclui.
- Excluir de rede: "perfuração direcional", "não destrutivo", "MND", "pipe bursting", "furo direcional" (outro preço por metro).
- Recomposição asfáltica em m³ continua fora (outro serviço).

Meta medida nos evals: pelo menos 15% das linhas candidatas com serviço, 0 classificação errada no gabarito.

### Modelo de dados

Reuso de `preco_item`, sem tabela nova de itens:

- `origem = "pncp_planilha"`, no lugar do `planilha_edital` reservado e nunca gravado (migração só troca o CHECK). Snake case como `tce_pe`.
- `chave_origem = "<id_pncp>#<sha256[:12]>#<item>"`; `processo` = id PNCP; `data` = publicação; `data_base` = da linha ou do cabeçalho (dia 1 do mês).
- BDI: `preco_com_bdi` = coluna com BDI (presente nas 6 medidas); `preco_sem_bdi` quando houver; `bdi` = o da linha, senão `com ÷ sem − 1`; só um preço e nenhum BDI declarado → `bdi_desconhecido` (regra da 7b). Compara-se sempre com BDI.
- `fonte_referencia`, `codigo_referencia` da linha; `preco_contratado_com_bdi` sempre nulo.
- Tabela nova pequena `planilha_edital` (id PNCP, sha256, status `extraida|sem_planilha|pulado_teto|falhou`, método `deterministico|ia`, linhas lidas, linhas gravadas, custo) para não reprocessar e para o log.
- **Dedupe**: dentro do edital, uma planilha só (XLSX > PDF separado > PDF do edital; duas cópias no mesmo PDF contam uma vez). Entre editais: mesmo sha256 da planilha, ou mesmo órgão com os mesmos (código, quantidade, preço) em 80%+ das linhas, é republicação: grava uma vez, no edital mais recente. A mediana da 7b.1 já conta processo como caso.
- "Estimado com desconto": calculado na consulta (`preco_com_bdi × homologado ÷ estimado` da 7a), não gravado.

### Agenda e cortesia

`radar precos planilhas` na task do analisar, todo dia às 10:00 (depois do analisar das 09:00). Cortesia igual à etapa 6: UA identificável, 1 s entre requisições, 3 tentativas, sem paralelismo. ~20 editais por mês × (1 listagem + 1–3 downloads) ≈ 80 requisições por mês.

### Tela

Nada novo: a seção "Preços de referência" da 7b.1 passa a ter casos de SP e MG, com a origem dita ("planilhas de editais do PNCP: estimado"). O texto atual ("hoje só PE") muda.

## Ordem

1. Seleção da planilha e parser determinístico, com testes de exemplo das 4 planilhas do spike (trechos curtos, não o arquivo).
2. Mudanças em `servicos` com casos reais do spike e regressão da 7b.1.
3. `planilha_edital`, gravação em `preco_item`, dedupe, migração do CHECK.
4. Evals: 6 planilhas com gabarito rascunho (`conferido: false`), precisão e recall por linha (descrição casada, preço ±1%).
5. IA para o resto, com os tetos; depois agenda e docs (`docs/etapas/07b2-planilhas.md`).

## Riscos

- **Cobertura**: 1/3 das obras não tem planilha no PNCP, e em MG foram 2 de 2. A referência de MG pode ficar fraca. Mostrar sempre o número de casos e a UF.
- **Classificação**: o gargalo é o casamento, não a extração. O contexto da obra aumenta os casos e o risco de erro; os evals precisam ter linhas de esgoto e de água sem a palavra.
- **Layouts**: 6 planilhas são pouca amostra; a coerência aritmética protege contra preço errado, não contra linha perdida. Medir recall por formato na primeira rodada real.
- **Data-base e reoneração**: SINAPI de abril e de julho no mesmo edital; sem correção por índice nesta etapa, então a tela mostra o período da data-base (como a 7b.1).
- **Republicação e retificação**: edital republicado com a mesma planilha infla casos se o dedupe falhar.

## Perguntas para o Augusto

1. **Planilhas fora do PNCP** (3 de 9, as 2 de MG): vale buscar no portal do órgão (1doc, Licitar Digital, site da autarquia) numa etapa seguinte, ou a cobertura do PNCP basta por ora?
2. **Contexto da obra**: num edital de adutora, "Tubo PVC-O DN 250" pode entrar como rede de água para comparar com redes de distribuição, ou adutora deve ser um serviço à parte (preço por metro diferente)?
3. **Planilha do edital aberto**: além das referências, quer ver na ficha os preços do próprio edital ao lado da mediana ("o metro de DN 200 aqui está 18% acima")? Custa o mesmo, já que a planilha é extraída na publicação.

## O que fica com o construtor

Nomes de colunas da `planilha_edital`, regex finas de código e unidade, lista de nomes de arquivo, formato do prompt, testes.

## Apêndice: resumo do spike

- Coleta: API de consulta `/contratacoes/publicacao`, 27/10–26/11/2026, SP e MG, modalidades 4 e 5: 31 páginas, 1.505 contratações; filtro amplo por objeto: 62; obra de saneamento à mão: ~30; amostra: 9 (IDs PNCP `45355575000165-2026-141`, `72962806000171-2026-100`, `44730331000152-2026-110`, `54010863000179-2026-533`, `44239770000167-2026-172`, `45369220000125-2026-113`, `17058108000138-2026-121`, `01613128000193-2026-28`, `56024581000156-2026-529`).
- Documentos: 18 arquivos de primeiro nível, 4 ZIPs (um com RAR dentro, aberto com o bsdtar do Windows), ~90 arquivos depois de abrir; formatos: PDF com texto, XLSX, DOCX, DWG. Nenhum PDF escaneado entre as planilhas; nenhum ODS ou XLS.
- Parser de estudo: pypdf `extract_text()` (o modo `layout` não ajudou no PDF picotado) e openpyxl `read_only, data_only`; cabeçalho achado por "descri" + "unid" + "quant" + "unit"; linha válida por coerência aritmética.
- Classificação: `radar.precos.servicos.classificar` do repositório sobre 265 linhas (40 + 102 + 31 + 92): 5 classificadas sem contexto, 16 com a palavra da linha da obra; 79 linhas com termos de rede, escavação, ligação ou asfalto.
- Arquivos baixados ficaram fora do repositório (pasta temporária); nenhuma chamada de IA, nenhum banco.

## Decisões do coordenador (2026-11-27) e desvios na construção

Respostas às perguntas acima:

1. **Planilhas fora do PNCP**: fora de escopo; etapa posterior.
2. **Adutora**: tubo de adutora ("Tubo PVC-O DN 250" numa adutora) conta como `rede_agua` pelo material e DN, com marca para separar depois: `preco_item.observacao = "adutora"` (pela descrição ou pelo contexto da obra).
3. **Sem tela nova**: os preços do próprio edital não vão para a ficha agora; as linhas novas alimentam as medianas de `/api/precos`, que passam a dizer a origem ("planilhas de editais do PNCP", campo `origens`) e as UFs de onde vêm, nunca "Brasil".
4. **Origem** `pncp_planilha`.

Desvios do construtor:

- Comando `radar planilhas atualizar` (e não `radar precos planilhas`), agenda própria `radar-planilhas-diario` às 10:00 na mesma família e role do analisar, e não na mesma task.
- A seção da etapa ficou em `docs/etapas/07b-precos.md` (e não num `07b2-planilhas.md`).
- Seleção sem nome: só as páginas com 8+ "R$", sem exigir "unid" e "quant" no cabeçalho (a página de Ibaté não tem "quant").
- Os títulos do PNCP vêm sem extensão: além dos de título de planilha, baixam-se os "outros documentos" que não são esclarecimento, aviso, errata e afins (até 6 downloads por edital) para abrir os ZIPs.
- Republicação: as linhas ficam no edital processado por último (a rodada vai do mais antigo ao mais novo), não pelo critério de data de publicação.
- Evals formais (gabarito em `evals/`) ficaram para depois; os trechos das planilhas em `tests/dados/planilhas/` com as classificações esperadas nos testes fazem o papel de gabarito rascunho.
- "Estimado com desconto" não entrou (sem tela nova; fica para a consulta quando houver onde mostrar).
- Migração `0014` com `down_revision = "0012"`: a 0013 é de outro ramo; religar no rebase.

## Revisão final (2026-11-27)

Correções pedidas pelo coordenador depois da revisão: gasto mensal da 7b.2 medido no provedor e anotado por chamada paga (`gasto_planilha`, migração 0014); resposta paga inválida ou cortada em `max_tokens` vira `ia_invalida` (não é comprada de novo); guarda das linhas da IA por números inteiros perto uns dos outros, DN e material da descrição no texto e total da planilha obrigatório; PE fora dos alvos (TCE-PE); BDI só plausível (5% a 45%) e coerente; ZIP com teto de planilhas e de bytes por edital; vigia de transação aberta; republicação simétrica (um lote não apaga a planilha maior); a planilha do próprio edital fora da referência dele; IA desligada → `aguarda_ia`; `falhou` com espera que dobra.
