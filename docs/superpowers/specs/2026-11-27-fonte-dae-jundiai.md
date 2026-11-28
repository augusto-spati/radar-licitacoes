# DAE S/A Jundiaí: pré-requisitos do coletor (verificado em 2026-11-27)

Amostras reduzidas em `tests/dados/fontes/dae-jundiai-*.html` (as do Compra Eletrônica foram trocadas pelas da seção 6). Foram 17 requisições GET (curl, UA `radar-licitacoes-estudo (github.com/augusto-spati/radar-licitacoes)`, 2 s ou mais entre elas, sem cookie, sem login): 10 a `daejundiai.com.br`, 3 a `compraabertadae.jundiai.sp.gov.br` e 4 a `compraeletronica.jundiai.sp.gov.br`. Nenhum POST.

**Atualização (27/11/2026, mais tarde): o Augusto escolheu a opção 1 e o coletor foi feito** (`src/radar/coleta/dae_jundiai.py`, fonte `dae_jundiai`). A abordagem final está na seção 6; o texto abaixo é o estudo original.

**Resultado do estudo: o coletor não foi implementado.** O portal WordPress indicado pela pesquisa só publica licitações **encerradas**, sem datas, e com atraso. As licitações em andamento estão num segundo sistema (Compra Eletrônica, ASP.NET) cuja lista só passa da página 1 por POST (`__doPostBack`). Pela regra do projeto (só GET), a decisão fica com o Augusto. As opções estão na seção 5.

## 1. Termos de uso, privacidade e robots.txt
- **robots.txt**: `daejundiai.com.br/robots.txt`, `compraabertadae.jundiai.sp.gov.br/robots.txt` e `compraeletronica.jundiai.sp.gov.br/robots.txt` devolvem 404. Não há restrição declarada nem `Crawl-delay`.
- **Termos de uso**: não localizados (a pesquisa de 26/11 também não achou). O Compra Aberta tem política de privacidade (`/politica-de-privacidade/`, LGPD, aviso de cookies do CIJUN); não li cláusula sobre acesso automatizado. NÃO VERIFICADO que não exista.
- Os dados são publicidade obrigatória de licitação (Lei 13.303/2016, regulamento interno da DAE S/A em PDF público). Nas amostras só há objeto, número, processo e nomes de arquivo; nenhum dado pessoal.

## 2. PNCP e CNPJ
- **CNPJ: 03.582.243/0001-73** ("DAE S/A – Água e Esgoto"), no rodapé de todas as páginas de `daejundiai.com.br` (conferido nas amostras de hoje). A pesquisa de 26/11 confirmou 0 contratações desse CNPJ no PNCP nas modalidades 4, 5, 6 e 8, e 0 também no CNPJ antigo da autarquia (44.642.353/0001-60).
- Conclusão: tudo o que a DAE contrata fica fora do PNCP. Uma fonte própria é o único caminho.

## 3. Estrutura

### 3a. Portal WordPress (`daejundiai.com.br/transparencia/licitacoes/`)
**Sem JS e sem bloqueio.** nginx, HTML de servidor, 50 a 70 KB por página, `content-type: text/html; charset=UTF-8`, sem ETag nem Last-Modified.
- **Uma página por modalidade e ano**: `/modo-de-disputa-fechado/modo-de-disputa-fechado-2026/` (MDF), `/modo-de-disputa-aberto/modo-de-disputa-aberto-2026/` (MDA), `/pregao-eletronico/pregao-eletronico-2026/` (PE), `/dispensas/dispensas-2026/`. Os slugs variam (`dispensa-2024`, `inexigibilidade-2025-2`, `modo-de-disputa-aberto-de-2023`); não dá para montar a URL de 2027 com certeza, seria preciso ler o índice.
- **Sem paginação**: o ano inteiro numa página. Hoje: MDF 12 itens (001 a 012/26), MDA 1, PE 34.
- **Itens**: blocos `p.wp-block-paragraph` do editor, texto livre: `Licitação: 007 26<br>Processo nº: 0920/2026<br>Objeto: ...`, seguido de links `MDF 007 2026 – Edital`, `– Ata`, `– Ata 2`..., `– Homologação e Extrato de Contrato`. A marcação muda de item para item: `<strong>` só no número ou cobrindo o `<br>`, links no mesmo parágrafo ou no seguinte, `&nbsp;` soltos, objeto quebrado com `<br>`, número com ano (`007 26`) ou sem (`001`, nos pregões). Um link de homologação aponta para o PDF da Ata 2 (MDF 012/26, erro de cadastro do site).
- **PDFs**: abertos, em `daejundiai.com.br/wp-content/uploads/AAAA/MM/...pdf` ou `intranet.daejundiai.com.br/wp-content/uploads/...`. O mês da pasta é o do upload.
- **Datas**: nenhuma. Nem publicação, nem abertura, nem encerramento. Elas só existem dentro do PDF do edital. A API REST do WordPress (`/wp-json/wp/v2/pages?slug=...`, GET) dá o `modified` da página inteira (MDF 2026: 2026-11-03T10:27:20), não por item.
- **Situação**: **todas as páginas, de 2024 a 2026 e nas três modalidades, têm uma única seção, "Encerradas"** (`<h2>` ou `<p><strong>`). Não existe seção de abertas ou em andamento. A pesquisa de 26/11 dizia "1 de modo aberto": era a contagem da modalidade MDA, não uma licitação aberta.
- **Atraso e lacunas**: o Compra Eletrônica tem MDF 013 a 017/26, publicados de julho a setembro (o 014 já tem quatro atas), e nenhum deles aparece no WordPress em 27/11. O MDF 007/26 tem edital, atas e homologação subidos juntos em 11/2026. O site é um arquivo de resultados preenchido à mão, depois do certame.
- **Identificador**: modalidade + número + ano (`MDF-007/26`). O processo (`0920/2026`) também serve.

### 3b. Compra Aberta e Compra Eletrônica (onde estão as abertas)
O índice do WordPress manda "para consulta à íntegra dos editais" ao **Compra Aberta** (`compraabertadae.jundiai.sp.gov.br`, WordPress do CIJUN). A página `/licitacoes/` dele é só um `iframe` para `https://compraeletronica.jundiai.sp.gov.br/dae/aberto/ConsultaLicitacao.aspx` (IIS 7.5, ASP.NET 4 WebForms, cookie `ASP.NET_SessionId`).

**Lista** `ConsultaLicitacao.aspx`
- GET devolve 200 com a grade `table#gdvCompras`, 10 linhas, e 9 ou mais páginas. Colunas: Unidade, Modalidade (`span[id$=lbldsmodal]`), Licitação (`a[id$=lnkMural]`, texto `2026/00014`, href `CompraNEWeb.aspx?cdcompra=843`), Objeto (`span[id$=lblObjeto]`, caixa alta abreviada ou texto completo), Data/Hora inicial (`dd/mm/aaaa 00:00:00`), Data/Hora Final (vazia nas 10 linhas), Situação (só um ícone, `cp_configuracao.gif` nas 10 linhas).
- Ordem padrão: modalidade em ordem alfabética, depois número decrescente. A página 1 de hoje traz Alienação 1/26, MDA 2 e 1/26 e MDF 017 a 011/26. MDF 010 a 001 e os pregões ficam nas páginas seguintes.
- **Paginação, ordenação e filtros só por POST**: `javascript:__doPostBack('gdvCompras','Page$2')`, com `__VIEWSTATE` e `__EVENTVALIDATION`. Os filtros (Modalidade, Situação com 21 valores como "RECEBENDO PROPOSTAS", Objeto, Exercício, Nº) também são campos do form `method=post`. **Não testei nenhum POST.**

**Detalhe** `CompraNEWeb.aspx?cdcompra={n}` (GET, amostra 843 = MDF 014/26)
- Título `MODO DE DISPUTA FECHADO - 2026/14 - PMJ`, objeto, Processo e Edital (vazios), **Publicação** `22/07/2026`, e "Situação" num `<select>` com "Em Configuracao" selecionado, inclusive no MDF 014 com quatro atas e na Alienação 1/26 já encerrada. A situação do sistema não informa nada.
- **Anexos**: tabela `grdArquivos` com nome, tipo (`EDITAL DA COMPRA`, `ANEXO DA COMPRA`, `PLANILHA`, `ATA`) e nome do arquivo. O download é `__doPostBack('grdArquivos$ctl02$lnkArquivo','')`, ou seja, POST.
- `cdcompra` é sequencial e comum às modalidades do sistema: 832 (MDA 1/26, março) a 847 (Alienação 1/26, outubro). Uns 16 ids em 2026, então os pregões provavelmente correm noutra plataforma (NÃO VERIFICADO).

### Mapa de situação (se um coletor for feito)
| Sinal | Situação normalizada |
|---|---|
| WordPress, seção "Encerradas", com link "Homologação" | `encerrada` (resultado publicado) |
| WordPress, seção "Encerradas", sem homologação | `encerrada` |
| Compra Eletrônica, sem anexo do tipo `ATA` | `aberta` (inferência: a sessão ainda não aconteceu) |
| Compra Eletrônica, com `ATA` e sem homologação no WordPress | `em_julgamento` (inferência) |

As inferências do Compra Eletrônica não foram confirmadas: a data de abertura fica no PDF do edital, e "sem ata" também pode ser licitação deserta, revogada ou só sem ata publicada.

### Formatos de data
- WordPress: nenhum.
- Compra Eletrônica: lista `dd/mm/aaaa 00:00:00` (hora sempre zero); detalhe `dd/mm/aaaa` (Publicação).

## 4. Riscos e cortesia
- **O WordPress não serve ao radar**: o radar existe para mostrar o que está aberto, e esse site só mostra o encerrado, sem datas, com meses de atraso. Um retrato dele encerraria tudo na reconciliação e acharia 0 abertos sempre. Ele serve, no máximo, para o histórico (homologações e extratos de contrato) da etapa 7.
- **O Compra Eletrônica exige POST** para passar da página 1 e para baixar anexos. A política do `ClienteEducado` é só GET. Fazer postback com `__VIEWSTATE` é tecnicamente simples (ASP.NET clássico, sem captcha e sem WAF visto), mas muda a regra do projeto. A decisão é do Augusto.
- O ícone e o `<select>` de situação do Compra Eletrônica não refletem a situação real ("Em Configuracao" em tudo). A situação teria de ser inferida pelos anexos.
- Datas de abertura e prazo de propostas só no PDF. O radar ordena e filtra por `data_encerramento_proposta`, então sem ler o PDF o edital fica sem prazo.
- Se um dia houver coletor: 1 execução por dia; WordPress 3 páginas (MDF, MDA, PE do ano); Compra Eletrônica, página 1 mais detalhes só de ids novos. UA identificável, 3 s entre requisições, parar em 403/429 ou numa página sem `#gdvCompras` / sem "Licitação:".

## 5. Opções para decidir
1. **Compra Eletrônica com POST de paginação** (recomendada se o Augusto aceitar POST de leitura): lista completa do ano, data de publicação e anexos tipados por GET no detalhe; situação inferida pela presença de ata. Exige abrir uma exceção à regra "só GET" no `ClienteEducado` (postback sem efeito colateral, mesma cortesia). O download dos anexos para a análise também é POST.
2. **Compra Eletrônica só com GET**: página 1 da lista (hoje cobre MDA e os MDF mais novos por causa da ordem alfabética) mais detalhes por GET. Não é um retrato completo: um MDF que cair na página 2 some, e a reconciliação o encerraria. Teria de ser uma fonte "por novidade", sem reconciliação, o que o motor de retratos não tem hoje.
3. **WordPress só para histórico**: coletor de resultados (homologação e extrato) para a etapa 7, fora do fluxo de abertos.
4. **Pedir à DAE** um feed ou a liberação explícita, citando a LAI, como sugerido para a Sanasa.

Trabalho futuro, qualquer que seja a opção: a análise (`radar.analise`) ler os PDFs da DAE (edital e termo de referência) para extrair data de abertura e valor.

## 6. Abordagem final (opção 1, implementada)

Aprovado pelo Augusto: POST só de leitura no Compra Eletrônica, com a mesma cortesia. Verificado em 27/11/2026 com mais umas 60 requisições de estudo (3 s entre elas, mesmo UA).

- **O que é POST e o que não é.** Na navegação, só dois postbacks, ambos no `ClienteEducado.postback_leitura`: paginação (`__EVENTTARGET=gdvCompras`, `__EVENTARGUMENT=Page$N`) e a busca por exercício (`btnPesquisar=Pesquisar`, `txtNrexer=<ano>`, os outros filtros zerados). O estado sai do formulário da página anterior (`__VIEWSTATE`, `__VIEWSTATEGENERATOR`, `__EVENTVALIDATION`, campos de texto e `<select>`), no mesmo `ASP.NET_SessionId`. Nenhum botão além da busca. Detalhe (`CompraNEWeb.aspx`) continua GET.
- **Terceiro postback (aprovado pelo Augusto em 28/11/2026): o download do PDF do edital**, `__EVENTTARGET=grdArquivos$ctlNN$lnkArquivo` no `CompraNEWeb.aspx?cdcompra=N`, com o estado do formulário do próprio detalhe (GET logo antes, mesmo cookie). Só o anexo do tipo `EDITAL DA COMPRA` com extensão `.pdf`, só para NE abertos, no máximo 5 PDFs por rodada (`MAX_EDITAIS_PDF`). Nenhum outro anexo é baixado. Desenho e estado na seção 7.
- **A lista é por exercício.** O GET já vem com `txtNrexer=2026`; 2026 tem 9 páginas de 10 linhas e 2025 tem 10. O pager é o numérico do ASP.NET (link `Page$N` para as outras páginas, `<span>` na corrente; com mais de 10 páginas aparece o "..." que também é `Page$N`). Pedir uma página além da última dá HTTP 500. Exercício sem nada responde "Nenhum registro encontrado!" sem a grade.
- **Armadilha vista na rodada real.** A busca guarda o índice de página da grade anterior: buscar 2025 a partir da página 9 de 2026 devolve a página 9 de 2025. O coletor volta à página 1 com `Page$1` e confere a página corrente em todas as páginas.
- **Completude (a reconciliação fecha o que o retrato não lista).** Cada página tem de trazer o `txtNrexer` pedido e todos os números da grade começando por `<exercício>/` (`2025/00001`, `2026/00063`): o WebForms devolve o campo digitado mesmo quando a grade não foi atualizada. "Nenhum registro encontrado!" só vale na página 1 do exercício corrente (o novo, em janeiro). No exercício anterior, busca vazia ganha um `Page$1` (a grade herdada da página 9 pode responder vazia) e, vazia de novo, derruba o retrato. O detalhe tem de ser da compra pedida (`cdcompra` do `action` do formulário e número do título iguais aos da linha) e trazer a grade `grdArquivos` com ao menos um anexo; senão é erro de layout e o retrato cai.
- **Duas numerações.** Os pregões são outro sistema: link `mural.aspx?cdcompra=<n>` (ids 419 a 567), com Data/Hora Final preenchida e ícone de situação real (`prop_preco_env` recebendo propostas, `cp_negociacao`, `intencao_recurso`, `cp_em_adjudicaca`, `adjudicacao`, `cp_homologada`, `cp_cancelada`, `cp_suspensa` com `title`). O `mural.aspx` tem 400 KB e não é lido. Modos de disputa, alienação e chamada pública são `CompraNEWeb.aspx?cdcompra=<n>` (ids 804 a 847), sem Data/Hora Final e com ícone `cp_configuracao` (às vezes `cp_negociacao` preso: o MDF 007/26 já homologado).
- **Id**: `<sigla da modalidade>-<número>`, ex. `MDF-2026/00014`, `PE-2026/00063` (o número só é único dentro da modalidade).
- **Situação do sistema NE, inferida no detalhe**: aviso (`AVISO`) de revogação, anulação ou cancelamento encerra (MDA 2/26), mas não "revogação da suspensão", suspensão nem negação ("não revogada"); com anexo `ATA`, em julgamento até 90 dias da publicação e encerrada depois; sem ata, aberta até 180 dias e encerrada depois (não "outra", que a reconciliação nunca fecha). Tipos de anexo vistos: `EDITAL DA COMPRA`, `ANEXO DA COMPRA`, `ANEXO DA ATA`, `PLANILHA`, `PROJETOS`, `ESCLARECIMENTOS`, `AVISO`, `ATA`.
- **Janela**: os últimos 12 meses pela Data/Hora inicial; lê o exercício corrente e o anterior.
- **Custo por rodada**: 1 GET de lista, uns 19 POST e uns 25 GET de detalhe, cerca de 2,5 min, mais até 5 POST de download de edital (só os NE abertos fora do cache; numa rodada estável, quase sempre zero).
- **Bloqueio**: 403/429 param, e há um marcador genérico de WAF (`<title>Request Rejected|Access Denied|Acesso Negado</title>`); nenhum bloqueio foi visto.
- **Data da sessão dos modos de disputa**: lida do PDF do edital, baixado pelo terceiro postback (seção 7). Sem ela o edital fica sem prazo e fora do filtro de abertos.
- **Fora de propósito, por enquanto**: `dae_jundiai` não está em `metricas.FONTES` (orçamento de métricas em 10/10, então sem `LatenciaRequisicao` dela) nem em `FONTES_ANALISADAS` da análise (os PDFs vêm por postback; trabalho futuro).

## 7. Prazo dos modos de disputa: data da sessão no edital (estudo de 27/11/2026)

**Estado (28/11/2026): implementado.** O Augusto aprovou o POST de download só de leitura, limitado a 5 PDFs por rodada. O desenho abaixo (itens 1 a 6) foi feito como escrito, com as decisões listadas em "Implementação" no fim da seção. O texto de "Por que o download parou" é o de 27/11, mantido como histórico.

**PNCP não é alternativa**: o CNPJ da DAE S/A (03582243000173) não é ente cadastrado no PNCP ("Ente não cadastrado", conferido em 28/11/2026), então o edital não pode vir de lá.

### Onde está a data
Na capa de todo edital MDF/MDA de 2026 lido (MDF 012/26 e MDA 01/26, PDFs públicos do WordPress da DAE, 2 GET com o UA do projeto e 4 s entre eles), sempre neste formato, duas vezes e com a mesma data e hora:

```
RECEBIMENTO DOS ENVELOPES
DATA: 16 de junho de 2026
HORÁRIO: Até as 09:30 horas (horário de Brasília/DF)
...
SESSÃO PÚBLICA
DATA: 16 de junho de 2026
HORÁRIO: Até as 09:30 horas (horário de Brasília/DF)
```

O corpo do edital fala em "sessão de abertura dos envelopes ... conforme data, horário e endereço discriminados neste edital" sem data, e o rodapé de toda página traz "Edital de 12/05/2026" (data do edital, não da sessão). Recebimento e sessão coincidem: são o mesmo prazo (a entrega dos envelopes é até a hora da sessão), então a data serve para `data_abertura_proposta` e `data_encerramento_proposta`, como nos pregões.

### Parser (`dae_jundiai.data_sessao_no_edital`, feito)
Sobre o texto sem acento e com espaços colapsados, só datas logo depois de um rótulo (`SESSÃO PÚBLICA`, `RECEBIMENTO DOS ENVELOPES`, `DATA DA DISPUTA`, `ABERTURA DA SESSÃO/DOS ENVELOPES/DAS PROPOSTAS`), até 40 caracteres sem ponto final; a hora só depois de `HORÁRIO` ou `às`. Datas por extenso ou `dd/mm/aaaa`, hora `HH:MM` ou `HHhMM`. Devolve data e hora em `America/Sao_Paulo` só se houver uma única data e uma única hora entre todos os candidatos; datas ou horas diferentes, ou nenhuma hora, dão None e o log `dae_jundiai_data_sessao_ambigua` (sem métrica nova). Testado no texto inteiro dos dois editais (acerta os dois) e nos trechos da capa guardados em `tests/dados/fontes/dae-jundiai-edital-*-capa.txt` (só o texto, o PDF não foi guardado).

### Por que o download parou
- No Compra Eletrônica o anexo só baixa por `__doPostBack('grdArquivos$ctlNN$lnkArquivo','')`, um POST no detalhe. A exceção aprovada em 27/11 cobre só os dois postbacks de navegação (seção 6: "nenhum download de anexo"), e a tarefa pedia só GET.
- O WordPress da DAE tem os editais por GET (`/wp-json/wp/v2/media?search=edital&after=...` lista MDF 012/26, 06/26, 08/26 etc.), mas só depois do certame: em 27/11 não havia edital de MDF 013 a 017/26, os que ainda podem estar abertos. Não serve para o prazo.
- Não testei nenhum POST nem tentei adivinhar URL de arquivo no Compra Eletrônica.

### Desenho do POST de download (aprovado em 28/11/2026)
1. **Exceção**: um terceiro postback de leitura, `grdArquivos$ctlNN$lnkArquivo` no `CompraNEWeb.aspx?cdcompra=N`, com o estado do formulário do próprio detalhe (GET logo antes, mesmo cookie), só para o anexo de tipo `EDITAL DA COMPRA` e extensão `.pdf`. Mesma cortesia do `ClienteEducado` (UA, 3 s, para em 403/429/bloqueio). Conferir que a resposta é `application/pdf`/`%PDF`; senão é falha do PDF.
2. **Quem**: só linhas NE que ficam `aberta` pela `inferir_situacao_ne` e sem prazo; teto de 5 PDFs por rodada (`MAX_EDITAIS_PDF`), os mais novos primeiro.
3. **Isolamento**: download e leitura num `try` próprio dentro de `coletar`; erro de rede, HTTP 4xx/5xx que não seja 403/429, PDF ilegível ou data ambígua deixam o prazo nulo e logam `dae_jundiai_edital_falhou` (fonte, cdcompra, motivo). 403/429/bloqueio continuam derrubando o retrato, como em qualquer requisição: a lista já lida não pode virar retrato parcial depois de um bloqueio. Uma página com a data (`PaginaColetada(url do detalhe + "#edital=<arquivo>", "texto", "<iso>|<arquivo>")`) é gravada com o retrato, e `extrair` a usa; a lista continua tudo-ou-nada.
4. **Texto do PDF na coleta**: `pypdf` direto em `radar.coleta` (já é dependência). `radar.analise.extracao` não pode ser importado (contrato de independência do import-linter), e o limite de tempo dela (sinal) teria de ser copiado ou o PDF limitado por tamanho e número de páginas (a data está na página 1: ler só as 3 primeiras).
5. **Cache sem migração**: a chave é `cdcompra` + nome do arquivo do edital (`bruto.detalhe.anexos[].arquivo`; o download é posicional, então o nome é a única chave estável) e o valor vai em `bruto.detalhe.data_sessao_edital` (`{"arquivo": ..., "data": iso | null}`) do edital gravado. Quem monta a fonte (cli/processador, que já leem o banco) passaria à `FonteDaeJundiai` um dicionário `{(cdcompra, arquivo): data}` lido do `bruto` dos editais `dae_jundiai` abertos; a fonte só baixa o que não está no dicionário. Edital retificado com outro nome de arquivo baixa de novo. Um resultado nulo também é guardado, para não baixar todo dia um PDF sem data legível.
6. **Testes previstos**: teto, cache (não baixa o que está no dicionário), falha do PDF não derruba o retrato, 403 derruba, ambíguo fica nulo.

### Implementação (28/11/2026)
- **Onde**: `FonteDaeJundiai._paginas_dos_detalhes` em `coletar`. Os detalhes são lidos dos mais novos para os mais velhos (Data/Hora inicial da lista); logo depois do GET de um NE que fica `aberta` pela `inferir_situacao_ne`, o postback de download usa o estado daquele detalhe. Assim "GET logo antes" e "os mais novos primeiro" saem sem requisição a mais.
- **Qual anexo**: `edital_do_detalhe` pega o `EDITAL DA COMPRA` `.pdf` com link `lnkArquivo`; com mais de um (retificação), o último da grade, o mais recente. NE aberto sem edital em PDF loga `dae_jundiai_edital_falhou` e fica sem prazo.
- **Resposta**: vale como PDF só com `%PDF` no primeiro KB (o `content-type` vai no motivo da falha, mas não decide: servidor ASP.NET costuma mandar `application/octet-stream`). Até 30 MB (`MAX_BYTES_PDF`), só as 3 primeiras páginas (`PAGINAS_LIDAS_PDF`) com `pypdf`.
- **Falhas e cache**: o nulo vai para o cache só quando o mesmo arquivo daria o mesmo nulo (PDF ilegível, grande demais, data ausente ou ambígua). Rede, HTTP de erro (fora 403/429) e resposta que não é PDF não são guardados: a rodada seguinte tenta de novo, dentro do teto. 403, 429 e página de bloqueio derrubam o retrato.
- **Página gravada**: `PaginaColetada("<url do detalhe>#edital=<arquivo>", "html", "<iso>|<arquivo>")`, iso vazio para nulo. O formato é `html` (não `texto`): o `ck_pagina_bruta_formato` só aceita `json`, `html` e `js`, e `texto` pediria migração. A página é gravada também para o que veio do cache, e é assim que o valor volta a `bruto.detalhe.data_sessao_edital` a cada retrato (sem ela o cache sumiria depois de uma rodada).
- **Datas**: a capa dá hora de Brasília com fuso; `data_abertura_proposta` e `data_encerramento_proposta` ficam sem fuso, em hora de Brasília, como a "Data/Hora Final" dos pregões. O cache guarda o iso com `-03:00`.
- **Cache vindo do banco**: `radar.editais.datas_sessao_dae_jundiai(sessao)` lê os editais `dae_jundiai` abertos e a cli (`_rodar_retrato`) o entrega à fonte (`usar_datas_edital`) antes do retrato. A Lambda consumidora só roda `extrair`, que lê as páginas `#edital=` e não precisa do cache.
- **Log**: `dae_jundiai_edital_falhou` (fonte, cdcompra, motivo) por falha e `dae_jundiai_editais_pdf` (baixados, adiados pelo teto) por rodada. Sem métrica nova (orçamento em 10/10).
