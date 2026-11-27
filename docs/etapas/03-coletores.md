# Etapa 3: coletores Sanepar e Compesa

Até aqui o radar lia só o PNCP. Esta etapa traz as licitações publicadas nos portais próprios da Sanepar (Obras e Engenharia) e da Compesa para o mesmo banco, o mesmo filtro, a mesma API e a mesma tela. Para isso o modelo passa a ser **um edital, várias fontes**.

## O que foi feito

- **Modelo multi-fonte.** A chave do `edital` passa a ser `(fonte, id_na_fonte)`. A situação vira um valor normalizado: `aberta`, `em_julgamento`, `encerrada` ou `outra`.
- **Motor de retratos.** A coleta lê a lista inteira da fonte uma vez por dia. O cliente educado pausa 3 s entre requisições, se identifica pelo User-Agent e para no primeiro 403 ou 429. Cada fonte tem um mínimo de registros, a reconciliação encerra os editais que saíram da lista, cada fonte tem sua trava e a retenção de páginas brutas é limitada.
- **Sanepar.** Lê o HTML da lista e do detalhe. A situação pedida na URL vale como a situação do edital. Uma página de detalhe com erro cai para os dados da linha da lista.
- **Compesa.** Lê a grade em JavaScript sem executá-la. Pede `Accept-Language: pt-BR` e cobre só os últimos 12 meses.
- **Front.** O cartão ganhou o selo da fonte, e o id público passou a ser `fonte~id`.

## Números da rodada real

| Fonte | Editais | Abertos | Casam com o filtro | Tempo |
|---|---|---|---|---|
| PNCP | 38.382 | — | 24 | (carga de 30 dias, etapa 1) |
| Sanepar | 92 | 57 | 15, sendo 4 de pesquisa ou detecção de vazamentos | cerca de 14 min (detalhes a 8 s cada) |
| Compesa | 283 (últimos 12 meses) | 11 abertos e 49 em julgamento | 0 | 37 s |

A segunda rodada seguida deu **0 editais alterados** nas duas fontes. Isso mostra que o hash não acusa mudança quando nada mudou. Na primeira rodada, os 2 alterados da Sanepar eram anexos novos de verdade no site.

## O que a rodada real ensinou (e os testes não pegaram)

1. **O formato da data da Compesa segue o idioma pedido, não a página.** A pesquisa baixou a página 1 com um navegador, que pede pt-BR, e a página 2 com `curl`, que não pede idioma. Daí veio o modelo errado de "página 1 em dd/MM e as seguintes em MM/dd". A trava de datas impossíveis (mais de 20% numa página) falhou alto, em vez de gravar dia e mês trocados. A correção foi pedir sempre pt-BR.
2. **A lista da Compesa é o histórico inteiro**, desde 2012, em ordem decrescente. O limite de 50 páginas disparou, e a correção foi a janela de 12 meses.
3. **Uma lista vazia pode ser layout quebrado.** A revisão final achou um caso que passava por todas as travas: "Abertura" vazia na Sanepar com "Julgamento" cheio. Isso encerraria todos os editais abertos da fonte. Agora "Abertura" vazia é falha.

## Defeito achado em produção (etapa 4a)

**Sintoma.** Na primeira rodada no Fargate contra o Neon, a cli logou `retrato_concluido` da Sanepar e logo depois `retrato_erro_inesperado`: o `pg_advisory_unlock` do fim falhou com `IdleInTransactionSessionTimeout`.

**Causa.** A `trava_fonte` segurava um advisory lock numa conexão dedicada, e a primeira consulta dela abria uma transação que ficava ociosa durante o retrato inteiro (uns 14 min na Sanepar). O Neon encerra sessões assim por `idle_in_transaction_session_timeout`. A sessão caiu no meio da coleta, o lock sumiu junto, e a exclusão mútua acabou sem aviso: só o unlock percebeu. Pior, a `RADAR_DATABASE_URL` de produção passa pelo pooler do Neon (PgBouncer em modo transação), onde um lock de sessão não é confiável nem quando a conexão não cai. O lock do coletor do PNCP (`coletar_pendentes`) tinha o mesmo desenho. Os testes não pegaram porque o Postgres do testcontainers não tem esse timeout nem pooler.

**Correção.** Lease em tabela (`trava_fonte`, migração `0006`, módulo `coleta/trava.py`): uma linha por fonte com dono e prazo. Obter é um `INSERT ... ON CONFLICT DO UPDATE ... WHERE expira_em < now()` numa transação curta, commitada na hora, numa conexão separada da sessão. Liberar é um `DELETE` que só apaga se o dono for o mesmo, então um processo que passou do prazo não apaga o lease de quem o tomou. Nenhuma conexão fica presa entre as duas pontas. O prazo é de 30 min, e tanto o retrato quanto o coletor do PNCP (chave `pncp`, que pode levar horas) renovam o lease a cada página, sem mudar `obtida_em`. Quem perde o lease para: o retrato termina em falha ("perdeu a trava da fonte") e a janela do PNCP também, contada no `resultado:`. O custo: um processo morto deixa a fonte presa até o prazo vencer.

Na etapa 4b o PNCP deixou de usar a chave `pncp`: cada janela tem o próprio lease (`travada_por`/`travada_ate` em `coleta_janela`, renovado a cada página), tanto na Lambda coletora quanto no `coletar_pendentes` local, e duas execuções em paralelo só disputam janela por janela. A `trava_fonte` segue valendo para os retratos.

## DAE S/A Jundiaí (fonte `dae_jundiai`, 27/11/2026)

O site `daejundiai.com.br` só publica as encerradas, sem datas. As licitações em andamento estão no **Compra Eletrônica** do CIJUN (`compraeletronica.jundiai.sp.gov.br/dae/aberto/`), um ASP.NET WebForms que só passa da página 1 por `__doPostBack`. O Augusto aprovou a exceção à regra "só GET" para o postback de leitura (nota `docs/superpowers/specs/2026-11-27-fonte-dae-jundiai.md`, opção 1).

- **Postback no `ClienteEducado`.** Método próprio, `postback_leitura`, com a mesma cortesia do GET (3 s, User-Agent, sem retentativa, 403/429/bloqueio param). O coletor só faz dois postbacks: a paginação da grade (`gdvCompras`, `Page$N`) e a busca por exercício (`btnPesquisar` com `txtNrexer`). O estado (`__VIEWSTATE`, `__EVENTVALIDATION` e os campos do formulário) vem sempre da página anterior, no mesmo cookie.
- **Retrato completo ou falha.** A lista vem por exercício; o retrato lê o corrente e o anterior inteiros e fica com os últimos 12 meses pela data inicial (como a Compesa, com "fora da janela"). Cada página tem de trazer o exercício pedido e o número pedido no paginador, e um id novo. Na rodada real, a primeira tentativa falhou de propósito: a busca do exercício anterior herdava a página 9 da grade (o índice fica no viewstate) e a página "1" repetiu a 9. Nenhum edital foi gravado; a correção é um `Page$1` depois da busca, e a página 1 agora também é conferida.
- **Situação.** Pregões (`mural.aspx`): o ícone da grade é o estado real e a "Data/Hora Final" é o prazo. Modos de disputa e alienação (`CompraNEWeb.aspx`): o sistema diz "Em Configuracao" para tudo, então a situação é inferida no detalhe (aviso de revogação encerra; ata publicada = sessão feita, em julgamento até 90 dias e encerrada depois; sem ata, aberta até 180 dias da publicação). A data da sessão desses só está no PDF do edital: o prazo fica nulo e o filtro de abertos (que exige prazo futuro) ainda não os mostra.
- **Anexos.** O detalhe traz os anexos tipados (edital, planilha, ata, projetos, aviso); vão para `anexos` e para `bruto.detalhe.anexos`. O download é outro postback e fica para a análise.
- **Migração `0012`.** O `ck_edital_fonte` ganha `dae_jundiai` (vem depois da `0011` dos preços).

| Rodada local (27/11/2026) | Editais | Abertos | Abertos com prazo futuro | Requisições | Tempo |
|---|---|---|---|---|---|
| 1ª (completa) | 92 (12 meses) | 7 (5 pregões e 2 MDF) | 5 pregões | 45 (1 GET de lista, 19 POST, 25 detalhes) | 2 min 25 s |
| 2ª seguida | 92 | 7 | 5 | 45 | cerca de 2 min 30 s |

A segunda rodada deu 0 inseridos e 0 alterados. Também há 7 em julgamento, 77 encerrados e 1 pregão suspenso ("outra").

## Perguntas de entrevista

Responda com suas palavras, citando o arquivo ou o comportamento. A etapa só fecha quando estiverem respondidas.

1. Como você trocou a chave primária de uma tabela com 38 mil linhas, e por que a consulta ficou mais lenta logo depois da migração mesmo com os índices no lugar?

   Resposta:

2. Qual a diferença entre coletar por janela de datas (PNCP) e por retrato (Sanepar, Compesa)? Quando cada uma faz sentido?

   Resposta:

3. A Sanepar não informa quando um edital é retificado. Como o radar detecta a mudança, e como você provou que isso não gera alterações falsas?

   Resposta:

4. O que significa "cortesia com a fonte" no código, e o que o coletor faz quando o site devolve 403 ou 429?

   Resposta:

5. Por que um retrato com zero resultados, ou com "Abertura" vazia, conta como falha e não como "não há editais"? O que aconteceria se contasse como sucesso?

   Resposta:

## Migração da chave

### O que mudou

A migração `0003_multifonte` troca a chave de `edital`, que era `numero_controle_pncp`, por `(fonte, id_na_fonte)`.

- **Preenchimento:** os 38.382 editais existentes viram `fonte='pncp'` e recebem `id_na_fonte = numero_controle_pncp`. O `modalidade_nome` sai de `bruto->>'modalidadeNome'`. A situação segue o mapa do PNCP: 1 vira `aberta`; 2 e 3 viram `encerrada`; 4 e qualquer outro código viram `outra`.
- **Colunas:** `situacao_nome` é renomeada para `situacao_original`. Entram `situacao_normalizada`, com CHECK nos quatro valores, além de `visto_em` e `hash_conteudo`. Também há um CHECK em `fonte`.
- **Nulos:** `modalidade_id`, `situacao_id`, `orgao_cnpj` e `data_publicacao` passam a aceitar nulo, porque só o PNCP garante esses campos.
- **Índice novo:** `ix_edital_situacao_encerramento (situacao_normalizada, data_encerramento_proposta)`.
- **Downgrade:** volta ao esquema 0002 e apaga os editais que não são do PNCP, porque o esquema antigo não tem como guardá-los.
- **Transação:** tudo roda numa transação só. No banco local, `alembic upgrade head` levou 4,5 s.

Resultado do `alembic check` depois do upgrade: `No new upgrade operations detected`. O ORM e o banco batem.

### O PNCP continua igual

`uv run radar filtrar` (config/filtro.toml), no mesmo banco:

| | Editais abertos | Saída |
|---|---|---|
| Antes (0002) | 24 | — |
| Depois (0003) | 24 | idêntica, linha a linha (`diff` vazio) |

`radar auditar` depois da migração: 120 esperadas, 120 concluídas, nada faltando, sem falhas.

Situação normalizada depois da migração: 37.280 `aberta`, 329 `encerrada`, 773 `outra`. "Aberta" aqui é só a situação; com o prazo, são bem menos.

### EXPLAIN (ANALYZE, BUFFERS) da consulta do `filtrar`

Medido com a consulta que o `filtrar` gera (`_montar_consulta`, 4 linhas de serviço, 4 UFs, `somente_abertos`), em três momentos. Cada número vale para a terceira execução seguida, com o cache quente.

| Momento | Execução | Buffers | Blocos de heap | Plano |
|---|---|---|---|---|
| Antes (0002) | 2,5 ms | 366 | 127 | BitmapAnd(prazo, OR de 4 GIN); UF, modalidade e `situacao_id = 1` no Filter |
| Logo depois da 0003 | 30,4 ms | 1.586 | 124 | BitmapAnd(prazo, UF, OR de 4 GIN). Os índices devolvem o dobro de linhas (37.790 no prazo, contra 18.935) |
| Depois de `VACUUM (ANALYZE)` | 4,6 ms | 419 | 62 | O mesmo plano, com os índices limpos |

**Leitura:**

1. **O UPDATE da migração duplica todas as linhas.** No Postgres, UPDATE é delete mais insert: cada um dos 38 mil editais ganhou uma versão nova, e a antiga virou tupla morta. Logo depois da migração, os índices ainda apontavam para as duas versões. A varredura do índice de prazo devolveu 37.790 entradas para 18.935 editais, e o GIN, 351 para 176. Daí a consulta 12 vezes mais lenta e com 4 vezes mais buffers. O heap ficou com 92 MB, e o VACUUM não devolve esse espaço; só um `VACUUM FULL` ou um `pg_repack` devolveria.
2. **O autovacuum resolveu sozinho**, poucos segundos depois da medição (às 15:22:33, contra 15:22:31 do EXPLAIN). Mesmo assim, o certo é rodar `VACUUM (ANALYZE) edital` logo depois de uma migração que reescreve a tabela, ou incluí-lo no roteiro do deploy. O ANALYZE também atualiza as estatísticas das colunas novas, e sem elas o planejador estima no escuro.
3. **Depois do VACUUM, a consulta ficou em 4,6 ms contra 2,5 ms.** É ruído nessa escala, e a forma do plano é a mesma. O filtro de modalidade virou `fonte <> 'pncp' OR modalidade_id IN (...)`, e o planejador passou a cruzar também o índice de UF no BitmapAnd. Os blocos de heap lidos caíram pela metade (62 contra 127), porque as linhas reescritas ficaram juntas em páginas novas.
4. **O índice novo `(situacao_normalizada, data_encerramento_proposta)` não foi escolhido.** Quase todos os editais estão `aberta` (37 mil de 38 mil), então a primeira coluna não filtra quase nada, e o índice só de prazo dá o mesmo resultado. Ele passa a valer quando houver muitos encerrados, por exemplo depois de meses de coleta ou com a Compesa, que traz os últimos 12 meses com todas as situações. Fica para reavaliar na Task 6, com dados das três fontes.

### Se fosse produção

Com a tabela grande e o sistema no ar, a migração não seria feita numa transação só, porque o `ALTER ... PRIMARY KEY` e o UPDATE de tudo travam a tabela. O roteiro seria:

1. criar as colunas novas como nulas (operação só de catálogo);
2. preencher em lotes, com commit a cada lote;
3. criar o índice único novo com `CREATE UNIQUE INDEX CONCURRENTLY`;
4. trocar a chave numa janela curta com `ALTER TABLE ... ADD PRIMARY KEY USING INDEX`;
5. rodar `VACUUM (ANALYZE)` no fim.

Nesse meio-tempo, o código teria de aceitar os dois formatos de id.

## Duplicatas entre o PNCP e as fontes próprias

Conferido em 2026-11-26, só leitura, no banco local (PNCP: 38.382 editais publicados de 27/10 a
25/11/2026, modalidades 4 a 7; Sanepar: 92; Compesa: 283, aberturas de 26/11/2025 a 24/11/2026).

| Órgão | CNPJ | No PNCP | Sobreposição com a fonte própria |
|---|---|---|---|
| Compesa | 09769035000164 | 0 editais | nenhuma |
| Sanepar | 76484013000145 | 67 editais, todos `Pregão - Eletrônico` | nenhuma |

- **Compesa:** nenhum edital do PNCP com o CNPJ 09.769.035/0001-64, nem com `orgao_nome` contendo
  "COMPESA" ou começando por "COMPANHIA PERNAMBUCANA" (o único "Companhia Pernambucana" é a Copergás,
  CNPJ 41025313000181, 3 editais). O CNPJ foi confirmado fora do banco (cadastro da Receita, sede
  em Recife). Como o PNCP não traz a Compesa nas modalidades coletadas, não há o que deduplicar: o
  coletor só passa a gravar `orgao_cnpj = 09769035000164` nos editais da Compesa, para que um
  cruzamento futuro seja por CNPJ.
- **Sanepar:** os 67 editais do PNCP são pregões (compras e serviços comuns: material de laboratório,
  locação de detectores, TI). Os 92 da fonte própria são OE (obras e engenharia, `LICITACAO` e
  `LICITACAO ELETRONICA`). Cruzando por data de encerramento no mesmo dia, os pares têm objetos sem
  relação ("AQUIS DE MATERIAL PARA CROMATOGRAFIA" contra "EXECUCAO DE OBRAS DE AMPLIACAO DO SISTEMA
  DE ESGOTO"); pelos primeiros 40 caracteres do objeto, 0 coincidências.

**Decisão:** sem sobreposição, nenhuma regra de exclusão no `filtrar` (a ideia era esconder do PNCP
os CNPJs cobertos por portal próprio). Se um dia o PNCP passar a trazer OE da Sanepar ou licitações
da Compesa, a regra entra com uma lista de CNPJs cobertos fora do `filtro.toml` das linhas e UFs.

## Retenção das páginas brutas

Cada retrato grava todas as páginas (a Sanepar, umas 100 por rodada com os detalhes). Depois de um
retrato concluído e registrado, `limpar_brutos` apaga as páginas dos retratos mais antigos da
mesma fonte: ficam as dos 3 concluídos mais novos e as dos 10 em falha mais novos (a falha é o que
se quer diagnosticar). As linhas de `coleta_retrato` ficam todas. O log é `brutos_removidos`.

## A Compesa "não respondia" da AWS: era o nosso firewall (etapas 4a e 6d)

Na primeira rodada na nuvem, o portal da Compesa deu `ConnectTimeout` duas vezes seguidas a partir de us-east-2, e da conexão do Augusto, no Brasil, respondeu em 0,6 s. A primeira leitura foi bloqueio por origem do IP, e a Compesa saiu do agendamento (PR #10).

Estava errado. Em 27/11/2026 o portal (`portalscl.compesa.com.br:8743`) e a nuvem dela responderam de 58 de 59 servidores do check-host.net, inclusive nos EUA. A causa era a regra de saída do security group do coletor, que só liberava 443 e 5432: a porta 8743 do portal era descartada na saída, e o `ConnectTimeout` é exatamente o sintoma de pacote descartado. A regra `coletor_compesa` libera a 8743, e a Compesa voltou aos agendamentos de coleta (`sanepar,compesa`) e de análise.

Lição: um timeout de conexão é ambíguo (quem descartou, nós ou eles?). Antes de culpar o outro lado, confira a saída do próprio lado e teste de uma terceira origem.
