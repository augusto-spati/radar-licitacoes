# Etapa 7b.1: preço unitário de referência pelo TCE-PE

O preço unitário (R$/m de rede, R$/ligação, R$/m³ de escavação) é o número que o dono da empresa mais entende e compara com o próprio custo. Esta etapa traz, dos dados abertos do Tribunal de Contas de Pernambuco, o preço **estimado** pelo órgão e o preço **contratado**, item a item, e mostra na ficha do edital os "Preços de referência" das linhas de serviço dele. Desenho: `docs/superpowers/specs/2026-11-27-etapa-07b-precos-design.md`; plano: `docs/superpowers/plans/2026-11-27-etapa-07b1-tcepe.md`.

## Por que o TCE-PE

- No PNCP o preço unitário **contratado** de obra não aparece: em 0 de 11 obras da amostra o contrato traz a planilha do vencedor (cita a proposta e não a anexa). O **estimado** está na planilha do edital em cerca de metade das obras, em PDF (fica para a 7b.2, com IA).
- O TCE-PE tem as duas coisas **estruturadas**: `Remessa_ProcessoContratacaoOrcamento` (estimado por item, com BDI, fonte e código de referência e data-base) e `Remessa_InstrumentoJuridicoItens` (contratado por item, mesma descrição e quantidade). A soma dos itens do contrato bate com o homologado. Cobre prefeituras de PE e a **Compesa**, que não publica no PNCP.
- A pesquisa dizia que o servidor podia recusar IP estrangeiro. Antes de construir, a sonda `radar tcepe sondar` rodou numa task do Fargate em Ohio: HTTP 200 em 2,5 s.

## O que foi feito

- **Cliente** (`src/radar/coleta/tcepe.py`): ISO-8859-1, pausa de cortesia, tentativas, e duas defesas contra armadilhas da API: parâmetro inválido devolve 100 mil registros em silêncio (o cliente confere que cada registro respeita o filtro) e o número do processo é casado como **substring** ("44" traz "144"; o cliente descarta o que não bate exato).
- **Serviços-padrão** (`config/servicos.toml`, `src/radar/precos/servicos.py`): oito serviços comparáveis. Rede de água e de esgoto por **material, DN e escopo** (só o tubo, só o assentamento, ou fornecimento e assentamento); escavação mecânica de vala por **faixa de profundidade** (até 1,5 m, 1,5 a 3 m, 3 a 6 m); ligações, hidrômetro, recomposição asfáltica e pesquisa de vazamento. A classificação é conservadora: na dúvida o item **não** é gravado (transporte, remanejamento, conserto de ramal, escavação com reaterro, linha que cita água e esgoto juntos ficam de fora). Unidades convertidas para a canônica.
- **BDI**: no TCE-PE o `ValorUnitario` **já inclui o BDI** (em Pedra a soma dos itens dá exatamente o valor global); o campo `BDI` vem como fração na Compesa (0,2467) e como percentual em Pedra (20,97) e é guardado só como informação, em fração.
- **Contrato ↔ processo**: o contrato não aponta para o processo; é escolhido pelo valor (±2% do homologado, pela soma dos itens ou pelo total) e cada item casa por descrição normalizada, quantidade e unidade. Grupo repetido com preços diferentes (o mesmo serviço em dois lotes de vencedores diferentes) fica **sem** preço contratado: preço errado é pior que nenhum. Razão contratado ÷ estimado fora de [0,3; 1,5] marca o item como suspeito.
- **Coleta** (`radar tcepe atualizar`, domingo às 04:00): processos do ano corrente e do anterior com termos próprios de saneamento (os da 7a pegavam 14 processos em 2026; os do TCE-PE pegam 170, 120 homologados), commit por processo, disjuntor após 5 falhas seguidas.
- **Referências** (`GET /api/precos/{id}`): por serviço das linhas do edital, mediana do estimado e do contratado (com o estimado dos mesmos itens ao lado), número de processos e de itens e período da data-base. Caso é processo: a mediana é a das medianas por processo, e os cortes contam processos (UF do edital com 5+, rede com 3+, "poucos casos" abaixo de 3). Com menos de 5 processos na UF do edital, usa as UFs que têm dados e diz quais (`ufs`; hoje só PE, a base é o TCE-PE), nunca "Brasil".
- **Tela**: seção "Preços de referência" na ficha do edital.

## Números da verificação real (9 processos homologados de PE)

- 1.797 itens de orçamento; 79 classificados: escavação 34 (29 até 1,5 m), rede de água 29, rede de esgoto 14, ligação de esgoto 2.
- 16 itens com preço contratado; Compesa (manutenção de esgoto GNR Sertão) 73/73, Pedra (rede de esgoto) 20/20 e Camocim (sistema de água) 79/79 itens do contrato casados; o contrato de Palmares foi recusado pelo valor (R$ 372,8 mil contra R$ 10,3 mi homologados).
- 0 suspeitos.

## O que fica para depois

- **7b.2**: preço estimado das planilhas dos editais do PNCP (fora de PE): ver a seção abaixo.
- ~~Ligar os processos da Compesa ao "Histórico do órgão" da 7a~~: feito na 7a.3 (`docs/etapas/07-historico.md`, seção "Compesa pelo TCE-PE"), na mesma rodada semanal.
- Rever as listas de termos com mais dados reais (a primeira rodada semanal).

## Perguntas de entrevista

1. Por que testar o TCE-PE a partir da AWS antes de escrever a coleta?
2. Como você sabe se o preço unitário já inclui o BDI, e o que acontece se errar?
3. Por que um preço contratado errado é pior que nenhum, e como o código evita atribuir o preço do lote errado?
4. Por que a escavação precisa da faixa de profundidade na chave, e a rede precisa do escopo?
5. Que armadilhas da API do TCE-PE viraram teste (filtro ignorado, número por substring, encoding)?

# Etapa 7b.2: preço estimado das planilhas orçamentárias do PNCP

A 7b.1 só cobre Pernambuco. Em SP e MG, onde a FR atua, o preço por item que existe é a planilha orçamentária anexada ao edital no PNCP: o **estimado** do órgão, com código de referência (SINAPI, SABESP, CDHU), data-base e BDI. Desenho: `docs/superpowers/specs/2026-11-27-etapa-07b2-planilhas-design.md` (com as decisões do coordenador no fim).

## O que foi feito

- **Alvos** (`radar.editais.alvos_planilha`): editais `pncp~` das linhas `redes` e `perdas`, nas UFs e modalidades do filtro, publicados nos últimos 120 dias, do mais antigo ao mais novo, até 10 por rodada. Na publicação, não só depois de homologados: a planilha sai com o edital.
- **Achar a planilha** (`radar.analise.planilhas`): os títulos no PNCP vêm sem extensão ("Processo_completo_revisado..."), então baixa os de título de planilha e os "outros documentos" que não são esclarecimento, aviso, errata e afins (até 6 downloads por edital), abre os ZIPs e pega os PDFs com nome de planilha ("Planilha Orçamentária", "Estimativa de Preços", "PO_"; fora: BDI, cronograma, composição, encargos, modelo, memorial; "Planilha Orçamentária_Cronograma_BDI" entra). Sem nome, o edital e o termo de referência, só as páginas com 8+ "R$". Teto de download de 40 MB só nesta etapa (o ZIP de Santa Bárbara tinha 25,6 MB); o `LIMITE_BYTES` da análise continua 20 MB. XLSX fica de fora na v1 (os dois do spike tinham PDF gêmeo com as mesmas linhas).
- **Leitura determinística** (`radar.precos.planilha_leitura`): uma linha vale com unidade conhecida seguida da quantidade e 2 a 7 números no fim em que `quantidade × unitário ≈ total` (0,3%). Do maior total coerente sai o com BDI; um unitário menor coerente (ou logo antes, até 60% abaixo) é o sem BDI; `%` na linha é o BDI dela. Código, fonte e data-base por linha ou pelo cabeçalho/rodapé ("SINAPI ref. ago/26"). Descrição quebrada em várias linhas é juntada desde o último número de item; páginas de composição de preço unitário ficam de fora; cópia repetida no mesmo PDF conta uma vez.
- **IA só no resto**: com menos de 5 linhas coerentes, Nova Lite com ferramenta forçada nas páginas com R$ (até 60 mil caracteres). A ancoragem é a conta e o texto: linha sem `quantidade × preço ≈ total` cai; quantidade, preço e total precisam estar no texto como números inteiros (um "1.088,69" não casa dentro de "11.088,69"), perto uns dos outros (250 caracteres), lidos com espaços ou sem eles (o texto vem picotado: "R$\n1.08\n8,69"); o DN e o material da descrição precisam estar no texto logo antes desses números (um "TUBO PVC DN 200" colado no preço e no total de outra linha cai). O total da planilha é obrigatório: tem de estar no texto e a soma das linhas tem de ficar a ±2% dele; sem ele nada é gravado. Saída cortada em `max_tokens` é recusada (paga, nunca vira cache). Antes de chamar, a estimativa do pior caso passa pelos tetos: US$ 0,02 por edital e US$ 0,30 por mês da 7b.2 (`pulado_teto`), além do teto mensal de US$ 2 da IA. O gasto do mês da 7b.2 é medido no provedor (custo antes e depois de cada edital) e anotado em `gasto_planilha` a cada chamada paga: resposta inválida, edital que falhou depois da chamada e refeito com `--refazer` contam certo (acerto de cache custa zero). O cache é o do provedor: a mesma planilha em outro edital não paga de novo. Testes só com provedor falso.
- **Classificação** (`config/servicos.toml`, vale também para o TCE-PE): o contexto da obra (título da seção da planilha, senão o objeto do edital) entra quando a descrição não cita água nem esgoto; "fornec./assent." é completo; "Ligação de água/esgoto" sem "predial" entra; perfuração direcional, método não destrutivo, MND e pipe bursting ficam fora da rede; "(NÃO INCLUI O FORNECIMENTO)" do SINAPI é assentamento (era lido como completo). Rede de água de adutora é rede de água pelo material e DN, com `observacao = "adutora"` para separar depois.
- **Gravação** (`radar.precos.planilhas`): só linhas com serviço vão para `preco_item`, origem `pncp_planilha`, chave `<id_pncp>#<sha256[:12]>#<item>`, `processo` = id do edital (é o caso das medianas). BDI da linha ou `com ÷ sem − 1`, só se plausível (5% a 45%) e, com os dois preços, coerente com eles: o "Peso (%)" da curva ABC e o par material + mão de obra (que somam o unitário) não viram BDI nem preço sem BDI; um preço só, ou BDI implausível → `bdi_desconhecido`. Estado por edital em `planilha_edital` (`extraida`, `sem_planilha`, `pulado_teto`, `falhou`, `republicada`, `ia_invalida`, `aguarda_ia`), com método, sha256, documento, linhas, custo e falhas seguidas. Republicação (mesmo sha256, ou do mesmo órgão 80%+ das linhas das duas planilhas com mesmo código, quantidade e preço; um lote só de uma planilha maior não conta): as linhas ficam uma vez, no edital processado por último.
- **Comando** `radar planilhas atualizar` (`--limite`, `--edital`, `--refazer`), agenda `radar-planilhas-diario` às 10:00, depois do analisar, na mesma família e role (Bedrock). Trava própria, disjuntor após 5 falhas seguidas do PNCP; `falhou` volta depois de 7, 14, 28 e 56 dias (dobra a cada falha seguida), `pulado_teto` no mês seguinte, `aguarda_ia` (pede IA e ela está desligada) quando a IA for ligada, e `ia_invalida` (paga e inútil) não volta sem `--refazer` ou `--edital`. **PE fica fora**: o TCE-PE (7b.1) já tem o estimado item a item de lá, e contar os dois seria o mesmo processo duas vezes na mediana. Nos ZIPs, até 5 PDFs de planilha e 100 MB descompactados por edital; entrada grande demais é pulada, sem derrubar o edital. Nenhuma leitura fica aberta durante a rede e a IA (teste com vigia, como o do TCE-PE). A última linha é `planilhas: ...`, nunca `resultado:`.
- **Tela**: nada novo. A planilha do próprio edital não entra na referência dele. As referências de `/api/precos` passam a ter casos de SP e MG; cada uma diz de onde vem o estimado (`origens`: "SP (planilhas de editais do PNCP)", "PE (TCE-PE)") e de quais UFs, nunca "Brasil".
- **Migração 0014** (parte de 0012; religar a 0013 no rebase): CHECK da origem troca `planilha_edital` (nunca gravada) por `pncp_planilha`, `preco_item.observacao`, tabela `planilha_edital`.

## Números medidos nas planilhas do spike (PDFs reais, sem IA, sem banco)

| Edital (UF) | Onde | Linhas lidas | Com termo de serviço | Classificadas |
|---|---|---|---|---|
| Cosmópolis, adutora (SP) | ZIP, "Planilha_orcamentaria_assinado.pdf" | 40 | 11 | 3 |
| Santa Bárbara d'Oeste, adutora (SP) | ZIP, "Planilha Orçamentária_Cronograma_BDI.pdf" (25 páginas, composições fora) | 105 | 35 | 7 |
| Ribeirão Preto, coletor (SP) | ZIP, "Anexo IV/Planilha Orçamentária.pdf" | 90 | 20 | 4 |
| Araraquara, coletor (SP) | ZIP, "Anexo III - Escopo de Fornecimento e Estimativa de Preços.pdf" | 31 | 15 | 2 |
| Ibaté, adutora (SP) | edital, 4 páginas com R$, texto picotado | 0 → IA (estimativa US$ 0,0013) | | |
| São Simão (SP) | edital, páginas com R$ (composições) | 75 | 5 | 0 |
| Votuporanga, João Monlevade, Vargem Alegre | sem planilha no PNCP | 0 | | |

Total: 341 linhas, 86 com termo de serviço, **16 classificadas (19%)** contra 5 de 265 antes das mudanças. Regressão da 7b.1: os orçamentos de `tests/dados/tcepe` classificam igual (6 de 26) e todos os casos de `test_servicos.py` seguem passando.

## Depois do deploy: reprocessar o TCE-PE

A correção "(NÃO INCLUI O FORNECIMENTO)" → assentamento (antes lido como completo) vale também para as linhas do TCE-PE já gravadas. `radar tcepe atualizar` refaz a classificação de todo processo homologado do ano corrente e do anterior a cada rodada (o orçamento é buscado e classificado de novo, e `gravar_precos` troca as linhas), então a rodada de domingo 04:00 aplica a correção sozinha. Para aplicar logo, rode uma task avulsa com o comando `tcepe atualizar` (ou, local, `uv run radar tcepe atualizar`). Processos de anos anteriores ao par corrente/anterior ficam como estavam: `uv run radar tcepe atualizar --anos 2024,2025` os refaz.

## Limites conhecidos

- "JUNTA FLANGEADA" cai na exclusão `flange` da rede de água (o tubo de FoFo flangeado de Ibaté fica fora).
- PEAD e PVC PBA às vezes vêm em DE ("DE=60 MM"), guardado como `dn` (só se compara dentro do material, como na 7b.1).
- A data-base por fonte do cabeçalho não pega formatos fora de "FONTE - mm/aaaa", "FONTE ref. mês/aa" e "FONTE (mm/aaaa)".
- Republicação vale para o edital processado por último, não para o de publicação mais recente quando um `--edital` antigo é refeito.
- "Estimado com desconto" ainda não está na consulta.

## Perguntas de entrevista

1. Por que a guarda aritmética (`quantidade × unitário ≈ total`) e não o trecho literal ancora a IA neste caso?
2. Como o código decide qual número é o preço com BDI quando a linha tem cinco números?
3. Por que o contexto da obra só vale quando a descrição não cita água nem esgoto?
4. Como os dois tetos da 7b.2 convivem com o teto mensal da IA e com o cache?
5. Como uma republicação no PNCP deixaria de inflar a contagem de casos da mediana?
