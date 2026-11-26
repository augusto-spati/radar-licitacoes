# Sanepar: pré-requisitos do coletor (verificado em 2026-11-26)

Amostras cruas em `amostras/sanepar/`. Foram 10 requisições ao site da Sanepar (curl, UA `radar-licitacoes-estudo (github.com/augusto-spati/radar-licitacoes)`, 3 s entre elas) e cerca de 20 ao PNCP.

## 1. Termos de uso, privacidade e robots.txt
- **robots.txt** (https://www.sanepar.com.br/robots.txt, modificado em 20/11/2026): `User-agent: *` padrão Drupal. Bloqueia `/core/`, `/profiles/`, `/admin/`, `/search/`, `/user/login|register|password|logout`, `/node/add/`, `/comment/reply/`, `/media/oembed`, `/index.php/...` e READMEs. Não bloqueia `/licitacoes`, `/licitacao/detalhe/` nem `/licitacao/arquivo/`. Não há `Crawl-delay` nem `Sitemap`.
- **Termos de uso**: não existe página de termos de uso. `/termos-de-uso` devolve 404 e nenhum link de rodapé da home aponta para termos.
- **Política de privacidade**: https://www.sanepar.com.br/privacidade-e-protecao-de-dados (LGPD, controlador Sanepar, CNPJ 76.484.013/0001-45). Procurei "automatiz", "raspag", "scrap", "robô", "crawler", "direitos autorais", "uso indevido". Nada aparece. Os únicos trechos relevantes são a definição de "Tratamento de Dados" (inclui "coleta... reprodução") e o aviso de Cookies. Nenhuma cláusula proíbe ou regula acesso automatizado.
- Conclusão: não há vedação expressa. Os dados são publicidade obrigatória de licitação (Lei 13.303/2016). Os dados do edital são de empresa, não pessoais, mas ocorrências e anexos podem conter nomes de servidores: não republicar anexos, guardar só metadados e links.

## 2. PNCP
- CNPJ: **76.484.013/0001-45** (aparece no rodapé da home, da página de privacidade e do detalhe, e é o `orgaoEntidade.cnpj` no PNCP: "COMPANHIA DE SANEAMENTO DO PARANA SANEPAR").
- A API aceita o parâmetro **`cnpj`** (não `cnpjOrgao`): `https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao?dataInicial=20260901&dataFinal=20261125&codigoModalidadeContratacao=6&cnpj=76484013000145&pagina=1&tamanhoPagina=50`. Funcionou de primeira (HTTP 200); uma consulta jan-ago deu reset (a API é instável). `tamanhoPagina=50` funciona, 204 = sem resultado.
- **Publica sim, mas só uma parte.** De 01/09 a 25/11/2026 há **198 contratações, todas modalidade 6 (Pregão Eletrônico)**, situação "Divulgada no PNCP". As modalidades 1-5, 7-13 devolveram 204 (vazio). Os `numeroCompra` vão de 146026 a 166926, isto é, o número do portal Sanepar de Bens e Serviços (1460/26 a 1669/26) com o ano colado: **BS tem cobertura quase total** (cerca de 210 no portal contra 198).
- **Obras e Engenharia (série 3xx/26, "Licitação Eletrônica"/"Licitação") não aparecem**: nenhum `numeroCompra` de até 5 dígitos. Das 198, só ruído de palavras-chave (vistoria de ligações prediais, barreira de absorção). Nenhuma de vazamento, redes ou projetos.
- Campos úteis do PNCP: `numeroControlePNCP` (ex. `76484013000145-1-000048/2026`), `numeroCompra`, `objetoCompra` (texto completo e legível), `valorTotalEstimado` (muitas vezes 0 = sigiloso/orçamento sigiloso), `dataAberturaProposta`, `dataEncerramentoProposta`, `dataAtualizacao`, `linkSistemaOrigem` (Licitações-e BB).
- Conclusão: **o PNCP não substitui o portal próprio** para o recorte do radar (obras e serviços de engenharia). Serve como fonte complementar de BS, com valor estimado e API JSON.

## 3. Estrutura para o coletor
**Sem JS e sem bloqueio.** curl simples devolve 200 com a lista inteira renderizada no servidor (Drupal 10, ~250 KB). O CleanTalk só injeta cookies `apbct_*` e um script de detector; nenhuma resposta de captcha/bloqueio nas 10 requisições. Não foi preciso enviar cookies.

**Lista** `https://www.sanepar.com.br/licitacoes?Tipo=OE&Situacao=Abertura` (GET; form `method=get`)
- Parâmetros: `Tipo` (OE, BS, PPP), `Situacao` (Abertura, Julgamento, Homologacao, Concluido, Suspenso), `Modalidade` (códigos como PE, PD, ...), `AnoEdital`, `Objeto`, `DtPublicaInicial/Final`, `DtAberturaInicial/Final`, `numero` (consulta específica, número com ano sem barra).
- **Paginação zero-indexada**: `&page=1` é a **segunda** página (a primeira é sem `page` ou `page=0`). 20 linhas por página. Contador: `<span class="edt-results-count">57 editais encontrados</span>` (57 abertos de OE hoje, 3 páginas). Paginador `nav.edt-pager` com links `?Tipo=OE&Situacao=Abertura&page=N`. As páginas 1 e 2 não se sobrepõem.
- Linhas: `tbody > tr`; colunas na ordem Número, Objeto, Modalidade, Data de Publicação, Data de Abertura, Situação, Detalhes. Seletores: `a.edt-link` (href `/licitacao/detalhe/{n}/{aa}`, texto `352/26`), `td.edt-col-objeto` (caixa alta, sem acento, pode ter quebras de linha), `td.edt-col-modalidade` ("Licitação Eletrônica", "Licitação"...), os dois `td.edt-col-date` (publicação `dd/mm/aaaa`; abertura `dd/mm/aaaa às HHhMM`), `span.edt-badge--abertura` (situação), `a.edt-btn-detail`. Valor estimado não aparece na lista.

**Detalhe** `https://www.sanepar.com.br/licitacao/detalhe/{numero}/{ano2d}` (amostras 345/26 e 349/26, pesquisa de vazamentos)
- `h2.sli-contract-title` ("LICITACAO ELETRONICA No 345/26"), `.sli-status-badge` (situação), pares `.sli-info-label` / `.sli-info-value`: Objeto, Modalidade, Data de Publicação, Data de Abertura, Propostas, Disponibilidade.
- Anexos: `.sli-occurrence-link`: `/licitacao/arquivo/{id}` (edital, aviso, notice em inglês) e `/licitacao/documento?nome=...Edital_Elementos.zip`. O texto do link é só o nome do arquivo, sem data.
- Itens por lote: acordeão `.sli-accordion` com tabela `.sli-col-item|desc|qtd|un`. Em serviço, quase sempre 1 item "1,00". Sem preço. O "Exportar CSV" é JS (`sliExportCsv`), não testado.
- `a.sli-plataforma-link`: disputa no Licitações-e BB (link genérico, sem id do processo) e `licitacoes.sanepar.com.br/SLI11140.aspx?numpro=34526` (esclarecimentos/impugnações).

**Identificador estável**: `numero/ano2d` (ex. `345/26`) é único e fica na URL; chave natural `(tipo_série, numero, ano)`. O `numpro` do canal de impugnação é o mesmo número com o ano colado (`34526`), idêntico ao `numeroCompra` do PNCP nas licitações de BS (`146626` = 1466/26). Os ids `/licitacao/arquivo/{id}` são crescentes e servem de chave por anexo. Atenção: o número é sequencial por série, então OE 345/26 e BS 345/26 podem coincidir; incluir `Tipo` na chave.

**Alterações/retificações**: a página **não tem campo de "atualizado em"**, histórico nem rótulo de retificação (busquei retifica/adiada/revogada/alterada: nada nas amostras). Sinais indiretos: mudança em data de abertura/propostas, situação (Abertura para Julgamento, Suspenso...), e novos itens em Ocorrências (novo id de arquivo). Estratégia: guardar hash dos campos e do conjunto de ids de anexos por licitação e registrar diffs.

## 4. Riscos e cortesia
- Frequência: 1 execução diária (de manhã). A lista de Abertura OE tem 3 páginas, então cerca de 3 requisições + detalhes só de licitações novas ou com mudança na linha (data/situação). BS: 150 editais/mês, filtrar por `Objeto=` ou aceitar o PNCP.
- Cache: `cache-control: max-age=2764800` mas `expires` de 1978; usar ETag/`If-None-Match` (a lista tem `etag: W/"...-gzip"`). Guardar HTML crú com data para auditoria. Nunca baixar anexos em massa: só o link.
- Identificação: UA como o usado, 3 s entre requisições, parar em 403/429/captcha ou página sem `.edt-results-count`, sem retentar em laço.
- Riscos: layout novo (classes `edt-*` / `sli-*`, migração recente, o sistema antigo ASP.NET segue no ar), CleanTalk poderia passar a bloquear, objeto da lista pode vir truncado (detalhe traz o completo), sem aviso formal de termos (autorização implícita; manter volume baixo), página de detalhe sem data de atualização.
- PNCP: API instável (resets), usar timeout e até 3 tentativas com espera.
