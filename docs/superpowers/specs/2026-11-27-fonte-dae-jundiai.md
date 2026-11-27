# DAE S/A Jundiaí: pré-requisitos do coletor (verificado em 2026-11-27)

Amostras reduzidas em `tests/dados/fontes/dae-jundiai-*.html`. Foram 17 requisições GET (curl, UA `radar-licitacoes-estudo (github.com/augusto-spati/radar-licitacoes)`, 2 s ou mais entre elas, sem cookie, sem login): 10 a `daejundiai.com.br`, 3 a `compraabertadae.jundiai.sp.gov.br` e 4 a `compraeletronica.jundiai.sp.gov.br`. Nenhum POST.

**Resultado: o coletor não foi implementado.** O portal WordPress indicado pela pesquisa só publica licitações **encerradas**, sem datas, e com atraso. As licitações em andamento estão num segundo sistema (Compra Eletrônica, ASP.NET) cuja lista só passa da página 1 por POST (`__doPostBack`). Pela regra do projeto (só GET), a decisão fica com o Augusto. As opções estão na seção 5.

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
