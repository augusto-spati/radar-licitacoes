# Compesa: de onde o portal público tira os dados (2026-11-26)

Complementa compesa.md. Método: Playwright (1 carga da lista) + 13 requisições curl (UA `radar-licitacoes-estudo (github.com/augusto-spati/radar-licitacoes)`, 3 s entre elas). Sem login, cadastro ou captcha; nada disso foi pedido.

## Resposta curta
Dá para coletar sem navegador. A lista é servida por um endpoint HTTP GET do próprio WebRun (`gridEdit.do`) que devolve texto JavaScript (não JSON) com os dados embutidos: 100 linhas por página, em uma única requisição, sem login. Isso é mais simples do que o "shell com iframe" visto antes.

## 1. Requisições relevantes (base `https://portalscl.compesa.com.br:8743/webrunstudio`)
Lista pública: `form.jsp?sys=SCL&action=openform&formID=7&...` (shell, define cookie) > `openform.do?...formID=7...` (HTML do formulário, 24 KB) > duas chamadas XHR:
- `GET gridEdit.do?sys=SCL&action=gridEdit&param=refresh&formID=7&comID=817041&gridComID=-1&gt=-1`: grade 1, 4 linhas (processos em destaque/ativos: "ACESSAR", número, objeto, abertura, hora). 47 KB.
- `GET gridEdit.do?sys=SCL&action=gridEdit&param=refresh&formID=7&comID=817140&gridComID=-1&gt=-1&WFRInput817141=`: grade principal "TMAKERGRID", 100 linhas, 1,09 MB. `WFRInput817141` é o campo de filtro (vazio = tudo).
- Resposta: `text/html; charset=ISO-8859-1`, mas o corpo é JS: `cols_817140 = [...]` (colunas) e `data_817140 = [{...},...]` (linhas), com `setGridPageIni(0)` / `setGridPageEnd(100)`. Parseável com regex/parser de literais JS; aspas simples e escapes `\/`, `\-`, `\x27`.
- Paginação (estado no servidor, por sessão): `GET navigate.do?sys=SCL&formID=7&componentID=817140&action=navigate&param=next&inner=true&gt=-1` e em seguida `GET gridEdit.do?...param=refreshpage&formID=7&comID=817140&gridComID=-1&gt=-1`. Testado: devolveu linhas 100-200 (`setGridPageIni(100)`). Valores de `param` para anterior/primeira/última: não testei (provável prior/first/last; NÃO VERIFICADO). Total de registros: NÃO VERIFICADO (mais de 200).
- Filtro: `gridEdit.do?sys=SCL&param=filter&formID=7&comID=817140&filter=...` (visto no JS `HTMLGrid.filter`; não testei).
- `POST executeRule.do` (disparado na carga): quando repetido por mim fora do navegador não rodei; o que a ferramenta capturou foi "Request Rejected" de um WAF (F5-like, cookies TS01...). Não é necessário para a lista.

## 2. Reprodução com curl
Sim. Resultado dos testes:
- `gridEdit.do` refresh funcionou até sem cookie (200, 100 linhas).
- Fluxo completo com cookie: GET `form.jsp` (recebe `JSESSIONID`, `TS0157363c`, `TS0146df9a`) > GET `openform.do` (Referer = form.jsp) > GET `gridEdit.do refresh` > `navigate.do next` > `gridEdit.do refreshpage`. Tudo 200 com UA identificável. A paginação exige o cookie (estado na sessão).
- Cuidados: manter o mesmo cookie jar e enviar Referer; o WAF rejeita POSTs fora de contexto e pode bloquear por taxa. 1 página custa ~1 MB; 1 coleta diária de 2-3 páginas é leve.
- Armadilha de formato: a data vem `dd/MM/yyyy` no refresh (`24/11/2026`) e `MM/dd/yyyy` no refreshpage (`03/13/2026`). Normalizar pela página de origem ou usar o `lc_chave` como referência e validar (dia > 12 denuncia o formato).

## 3. Campos e identificador
Colunas visíveis: ANEXOS (botão), NUMERO PROCESSO (ex. `LC 213/2026 CEL-1`), OBJETO, "+" (botão detalhe), ABERTURA (data), HORA, ACOMPANHAMENTO (botão), SITUAÇÃO (Habilitação 40, Contrato 39, Abertura 11, Fracassada 5, Suspensa 2, Revogada, Homologação, Deserta, na página 1).
Campos ocultos por linha (dentro dos botões): `lc_chave` (ex. `2026LC213CEL-1`), Número do Processo (`4791/2026`), Número do P.A. (`RP 4791/2026`), Órgão Solicitante (GAL), Documento Gerador (`CI 504/2026 GAL`), Custo Estimado (`2856395,49`), Origem de Recursos (COMPESA E OUTROS), Prazo (365), Responsável CPL, Município, Região de Desenvolvimento, Classificação do Objeto (Aquisição etc.), observações, GED.
Identificador estável: `lc_chave` (ano + tipo + número + comissão) ou o NUMERO PROCESSO. Também há o filtro de detalhe `licitacao.lc_numero=LC 213/2026 CEL-1`. Há valor estimado, o que o texto de agregadores não tinha.
Os links de anexo e acompanhamento são botões que abrem outros formulários (form 10 e 11, `openForm`); não os acionei, então se exigem login/cadastro: NÃO VERIFICADO.

## 4. robots.txt e termos
- `portalscl.compesa.com.br:8743/robots.txt`: 404 (Tomcat), sem restrições declaradas.
- `compesa.com.br/robots.txt`: só bloqueia caminhos de calendário.
- `servicos.compesa.com.br/robots.txt`: bloqueia /wp-admin/ e uploads de WPForms; sitemap WP.
- Termos de uso: não localizei em nenhum dos domínios (NÃO VERIFICADO que não existam).
Dados de licitação pública (Lei 13.303 e LAI) tendem a ser públicos; ainda assim, ser educado: UA identificável, 1x/dia, pausas, sem anexos.

## 5. Amostras (`amostras/compesa/`)
gridEdit_817140.html (página 1, 100 linhas), gridEdit_refreshpage_pagina2.html (página 2), gridEdit_817041.html (grade 1), executeRule_REJEITADO_waf.txt, robots dos 3 hosts.

## 6. Recomendação
Usar requests direto, sem navegador: cookie jar + `form.jsp` > `openform.do` > `gridEdit refresh` (+ `navigate next`/`refreshpage` para as páginas seguintes), parser dos literais `data_817140`, chave `lc_chave`, gravar snapshot diário e diferenças (situação, abertura). Playwright só como plano B se o WAF passar a bloquear.
Riscos:
- Endpoint interno do WebRun, sem contrato: IDs de componente (817140, 817141) e formato podem mudar; WAF (TS01 cookies) pode passar a bloquear por IP/taxa.
- Paginação depende de sessão e a data muda de formato entre chamadas.
- Termos não localizados; anexos podem exigir cadastro (fora do escopo).
- Volume e aderência: a concessão reduz demanda em comercial e perdas (ver compesa.md); o portal serve a Compesa, não às concessionárias.
- Parecer: vale implementar (baixo custo) como fonte primária da Compesa.
