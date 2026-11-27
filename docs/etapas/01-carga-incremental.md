# Etapa 1: carga incremental do PNCP

Tema da trilha: sistemas de dados. Fechada em 2026-11-26; a medição do `EXPLAIN` foi feita em 2026-11-27 (abaixo). Faltam as respostas às perguntas de entrevista.

Esta nota serve de estudo e de base para o vídeo. A ideia é que, depois de lê-la, você consiga explicar cada decisão sem consultar o código e defendê-la numa entrevista.

## O que foi feito

O radar agora coleta, guarda e filtra editais do PNCP (a API pública de contratações do governo) sem ninguém acompanhar.

1. **Coleta por janelas.** O trabalho é dividido em janelas de um dia por modalidade (4, 5, 6 e 7: concorrência e pregão, eletrônicos e presenciais). Cada janela é uma linha da tabela `coleta_janela` com status (`pendente`, `em_andamento`, `concluida`, `falha`), número de tentativas, erro, páginas e registros. O código está em `src/radar/coleta/janelas.py`.
2. **Cliente HTTP que aguenta uma fonte ruim.** `src/radar/coleta/pncp.py` usa httpx e tenacity. Repete em 429, 500, 502, 503 e 504, em timeout e em erro de conexão, com espera exponencial e jitter. O 429 do PNCP vem em HTML e sem `Retry-After`, então a espera mínima nele é 12 vezes a espera base.
3. **Bruto e tratado separados.** Cada página devolvida pelo PNCP é guardada inteira, como JSONB, na tabela `pagina_bruta`. A tabela `edital` é derivada dessa página por `src/radar/editais/registro.py`.
4. **Upsert idempotente.** O edital é gravado por `numeroControlePNCP`. Rodar duas vezes a mesma página dá o mesmo resultado, e uma versão mais velha nunca sobrescreve uma mais nova.
5. **Atualização diária com lookback.** `radar atualizar --lookback 10` olha os últimos 10 dias no endpoint de atualização, para pegar retificações e mudanças de situação.
6. **Filtro por busca de texto em português.** Coluna gerada `busca` (tsvector) com índice GIN, termos por linha de serviço e UFs em `config/filtro.toml`. O código está em `src/radar/editais/filtro.py` e a migração em `migrations/versions/0002_busca_texto.py`.
7. **Auditoria de cobertura.** `radar auditar` compara os dias e modalidades esperados com o que as janelas realmente cobrem e sai com código 1 se faltar algo.

Comandos: `radar carga-inicial`, `radar atualizar`, `radar processar`, `radar auditar`, `radar filtrar` (ver `README.md`).

## Conceitos

### Idempotência

Uma operação é idempotente quando executá-la uma vez ou dez dá o mesmo estado final. Isso importa aqui porque tudo pode ser interrompido e repetido: a rede cai, o notebook suspende, a coleta roda de novo por cima do lookback.

Onde aparece no código:
- `planejar_janelas` usa `ON CONFLICT DO NOTHING` na chave única `(endpoint, data_ini, data_fim, modalidade)`. Planejar duas vezes não duplica janelas.
- `_gravar_pagina` usa upsert na chave `(janela_id, numero_pagina)`. Coletar a mesma página de novo troca o conteúdo e limpa `processada_em`, o que manda a página para ser reprocessada.
- `registrar` usa upsert por `numero_controle_pncp`.

### Upsert com a versão mais nova vencendo

Um upsert simples (`ON CONFLICT DO UPDATE`) sobrescreve sempre. Isso é perigoso aqui: se uma página antiga for processada depois de uma nova, o edital voltaria ao estado velho. Em `registro.py` o `DO UPDATE` leva uma condição:

```
where=Edital.data_atualizacao < excluded.data_atualizacao
```

Só atualiza se o que chegou é mais novo. Dentro do mesmo lote também há duplicatas (o mesmo edital em duas páginas), e `_validar` fica com a de `data_atualizacao` maior.

Para contar o que foi inserido e o que foi atualizado, o código lê `xmax = 0` na cláusula `RETURNING`. No Postgres, uma linha recém-inserida tem `xmax` zero e uma linha atualizada não. É um truque conhecido, mas depende de um detalhe interno do Postgres; vale saber que existe e por quê.

### Janela e checkpoint

Pedir "tudo dos últimos 30 dias" numa chamada só não funciona com uma fonte lenta (cerca de 5,5 s por página nas medidas, e até 20 s nos testes do desenho) e instável. A solução é cortar o trabalho em pedaços pequenos e registrar o andamento de cada um.

- O pedaço é a janela (um dia, uma modalidade).
- O checkpoint é gravar cada página com `commit` assim que ela chega (`_gravar_pagina`). Se o processo morre na página 7 de 12, as 6 primeiras estão no banco.
- A retomada é automática: ao começar, `_coletar_todas` devolve para `pendente` qualquer janela que ficou `em_andamento` (só pode ser sobra de uma execução interrompida) e volta a pegar as pendentes e as falhas.
- Um advisory lock do Postgres (`pg_try_advisory_lock`) garante um coletor por vez. Sem ele, duas execuções simultâneas poderiam achar que a mesma `em_andamento` é sobra e pisar uma na outra.

### Falha que não vira vazio

Esta é a regra central da etapa e está escrita no topo de `janelas.py`: uma janela que falha fica com status `falha` e o erro registrado, nunca `concluida` com zero registros.

Dia sem edital e falha de coleta são coisas diferentes. Se a falha virasse "vazio", o buraco ficaria invisível para sempre: a tela mostraria menos editais e ninguém saberia por quê. Os casos tratados:

- **HTTP 200 com corpo malformado** (não é JSON, ou não tem `data` e `totalPaginas`): `_interpretar` em `pncp.py` trata como falha retentável, nunca como página vazia.
- **Total de páginas que muda no meio da janela:** a janela falha com a mensagem "pagina N inconsistente".
- **Menos registros distintos do que o `totalRegistros` anunciado:** a janela falha com "incompleta: X de Y registros". Isso pega o caso de uma página tardia vir vazia e truncar a janela em silêncio.
- **Exceção inesperada em uma janela:** é isolada, a janela vira `falha` e a coleta segue para as outras.
- **429 e 500 persistentes:** esgotadas as tentativas, vira `ErroPNCP` e a janela vira `falha`.

Sobre a divisão da janela (pergunta 1): no código, `_dividir` só é chamada quando o erro vem na página 1, a janela tem mais de um dia e nenhuma página dela foi gravada (`_sem_paginas`). Nesse caso nada se perde ao trocar a janela por duas metades menores, e o pedido menor pode passar. Em página profunda já há páginas gravadas, então a janela só é marcada como `falha` e a próxima rodada a retoma. É o padrão da Base dos Dados. Observação: `planejar_janelas` cria janelas de um dia, então na carga real a divisão não chegou a ser acionada; ela é coberta pelos testes de integração (`tests/integration/test_janelas.py`).

### Lookback

O endpoint de atualização devolve os editais alterados em um intervalo. Uma janela de "hoje" pode ser fechada antes de o dia acabar, e o PNCP ainda pode alterar editais naquele dia. Por isso:
- as janelas de atualização concluídas antes de o dia acabar (em Brasília) voltam a ser elegíveis (`_proxima_janela`);
- a atualização diária olha 10 dias para trás, para pegar retificações e mudanças de situação feitas depois da publicação.

O lookback é uma margem de segurança contra atraso e correção tardia, não uma garantia. O que ele não cobre (ficar mais do que 10 dias sem rodar) é a pergunta 3. O endpoint de atualização é o que a Base dos Dados usa, mas ele **não foi testado por nós** em operação diária; vale ter isso em mente.

### Coluna gerada e índice GIN

Buscar texto em português com `LIKE '%termo%'` é lento (não usa índice) e pobre (não conhece plural, acento nem radical). A solução do Postgres tem três peças:

- `to_tsvector('portuguese', texto)` transforma o texto em lexemas: minúsculas, sem palavras vazias ("de", "a"), com as palavras reduzidas ao radical (o stemmer).
- Uma **coluna gerada** (`GENERATED ALWAYS AS (...) STORED`) guarda esse tsvector na própria linha. O banco a recalcula quando o `objeto` muda; o código nunca escreve nela (por isso `registro.py` a exclui do `DO UPDATE`).
- Um **índice GIN** (índice invertido: lexema para a lista de linhas que o contêm) deixa o operador `@@` achar as linhas sem varrer a tabela.

A busca usa `phraseto_tsquery`: cada termo da configuração é uma frase (as palavras têm de aparecer em sequência), e as frases de uma mesma linha de serviço são unidas com `||` (ou). Frase importa por causa do que aconteceu com "leitura" (ver "Números reais").

### unaccent e IMMUTABLE

`unaccent()` tira acentos, para que "ligação" case com "ligacao". Mas a função da extensão é apenas `STABLE`. O Postgres exige função `IMMUTABLE` em coluna gerada e em índice de expressão, porque o valor guardado só continua correto se a função der sempre o mesmo resultado para a mesma entrada. Uma função `STABLE` pode depender de configuração (aqui, o dicionário no `search_path`) e mudar entre sessões; um índice construído com ela poderia ficar inconsistente em silêncio.

A migração `0002` cria `radar_unaccent(text)`, que fixa o dicionário (`public.unaccent('public.unaccent', $1)`) e se declara `IMMUTABLE`. Isso é uma **promessa nossa ao banco**: se o dicionário fosse alterado, os valores já guardados ficariam velhos e o banco não perceberia. Aqui o dicionário não muda, então a promessa é segura.

### Bruto versus tratado

- **Bruto** (`pagina_bruta.conteudo`): a resposta do PNCP exatamente como veio, e quando veio.
- **Tratado** (`edital`): campos escolhidos, tipados e validados com Pydantic.

Guardar o bruto custa espaço, mas permite: reprocessar sem chamar o PNCP de novo (o que leva horas) quando o tratamento muda ou tem bug; ver o que a fonte realmente mandou; e separar "o que a fonte disse" de "o que eu entendi". Na carga de 30 dias o banco chegou a 139 MB com os brutos. Registros que não passam na validação são contados como inválidos e vão para o log com o motivo; nunca são descartados em silêncio (na carga: zero).

### Auditoria de cobertura

`radar auditar` é separado da coleta de propósito: quem coleta não deve ser o único a dizer que coletou bem. Ele gera a lista de pares (dia, modalidade) esperados e confere quais têm janela `concluida`. Os que têm janela `falha` aparecem com o erro; os que não têm janela nenhuma aparecem como faltando. Sai com código 1 se algum par não estiver coberto, então serve no agendador e no CI.

## Trocas

| Decisão | O que se ganhou | O que se pagou |
|---|---|---|
| Janela de um dia por modalidade | Retomada barata, falha localizada | Muitas chamadas (120 janelas em 30 dias) |
| Guardar o bruto | Reprocessar sem rede, rastrear a fonte | Espaço em disco (e no Neon, tratado na etapa 2) |
| Postgres e não SQLite (D2) | Upsert condicional, advisory lock, tsvector, JSONB | Precisa de Docker local |
| Sem orquestrador como Prefect ou Dagster (D4) | A lógica incremental fica visível no código, que é o conteúdo do tema | Agendamento e alerta ficam por nossa conta |
| Dispensa fora das modalidades | A fonte deu 500 no teste e a modalidade raramente cobre este serviço | Pode perder um edital raro |
| Carga de 30 dias e não 60 | Cabia numa noite | Histórico curto |
| Coletar o Brasil inteiro e filtrar no banco | Mudar os termos não exige coletar de novo | Guarda 38 mil editais, quase todos irrelevantes |

### O ORM (D5) e o que ele esconde aqui

A escolha do SQLAlchemy 2.0 com ORM foi sua, contra a recomendação inicial de SQL à mão (`docs/decisoes/0005-sqlalchemy-orm.md`). O motivo não foi registrado; vale registrá-lo para a entrevista.

Onde o ORM ajudou: mapeamento de tabela e coluna, sessão e transação, migrações com Alembic, testes.

Onde ele esconde coisas que importam neste projeto:
- **Os pontos de commit.** O checkpoint é um `sessao.commit()` num lugar preciso (`_gravar_pagina`). Com ORM, "quando o dado foi gravado de verdade" depende de `flush` e `commit`, e é fácil não perceber. Aqui isso é a segurança da retomada.
- **O upsert.** O ORM comum não tem upsert. Foi preciso usar `insert(...).on_conflict_do_update` do dialeto Postgres, com `where` e `RETURNING (xmax = 0)`. No fundo é SQL escrito em Python.
- **A coluna gerada e o operador de busca.** `Computed(...)` descreve a coluna, mas quem a cria é a migração `0002`, junto com a função `IMMUTABLE`. A busca usa `.op("@@")` e `phraseto_tsquery`: o ORM só empacota SQL que você precisa entender.
- **O plano de execução.** O ORM não diz se a consulta usa o índice. Só o `EXPLAIN` diz.

Como compensar (combinado em D5): ligar `RADAR_LOG_SQL=true` para ver o SQL gerado e rodar `EXPLAIN ANALYZE` antes e depois de cada índice (seção abaixo).

## Números reais

Medidos em 2026-11-26.

**Teste de 1 dia.** 4 janelas, 40 páginas, 1.929 editais, 0 inválidos, cerca de 4 minutos (cerca de 5,5 s por página).

**Carga de 30 dias.**

| Medida | Valor |
|---|---|
| Janelas concluídas | 120 de 120 |
| Janelas com falha no fim | 0 |
| Editais | 38.382 |
| Registros inválidos | 0 |
| Retentativas automáticas | 11 (o servidor derrubou a conexão) |
| Duração | 2 h 40 |
| Tamanho do banco (com brutos) | 139 MB |

Leitura: as 11 retentativas mostram a fonte instável e o retry funcionando, e nenhuma janela ficou com falha no fim. A carga levou 2 h 40; o desenho estimava cerca de 6 h.

**Filtro, antes e depois.** Com os termos iniciais, o filtro mostrava 36 editais abertos. Ao conferir à mão, o termo "leitura" sozinho trazia ruído: fitas de borda, bobinas, livros. O motivo é que o stemmer português reduz "leitura" e "leiturista" ao mesmo radical, então qualquer objeto que fale de leitura casa. Trocado por frases específicas ("leitura de hidrometro", "leitura de medidores", "leitura e entrega de contas"), caiu para **24 abertos**. O commit é `a2d5723`. A lição: filtro de texto se valida olhando os resultados, não só rodando testes.

**Cobertura de Pernambuco.** Em 30 dias o PNCP trouxe 948 editais de PE, quase nenhum de saneamento. A Compesa aparece como compradora em apenas 3. Conclusão: companhias estaduais de saneamento publicam em portal próprio, não no PNCP. Hoje o radar cobre bem prefeituras e consórcios e mal as companhias estaduais; os portais próprios estão nas não metas da v1.

### EXPLAIN com e sem o índice GIN

**Medido em 2026-11-27, no banco de produção (Neon), com 58.825 editais do PNCP** (tabela `edital` com 73 MB e 18 MB de índices). A consulta medida é a que o `filtrar` realmente executa (todos os termos das quatro linhas de serviço, UFs, modalidades e só abertos), capturada com um listener do SQLAlchemy e rodada com `EXPLAIN (ANALYZE, BUFFERS)`:

| | Tempo de execução | Blocos lidos (shared) | Plano |
|---|---|---|---|
| Com o GIN | 14 ms | 1.182 (hit) | `Bitmap Index Scan` em `ix_edital_busca` |
| Sem o GIN | 81 ms | 3.248 hit + 7.147 read | varredura sequencial |

O índice deixa a lista ~6 vezes mais rápida e lê ~9 vezes menos blocos. Com 38 mil linhas, na carga de 30 dias, o ganho seria menor; com o histórico crescendo, maior.

**Como "sem o GIN" foi medido.** Em produção não rodei o `DROP INDEX` do procedimento abaixo: ele pega um lock exclusivo na tabela durante a transação, e o coletor grava nela o dia todo. O GIN só é usado por bitmap scan, então `SET LOCAL enable_bitmapscan = off` dentro de uma transação de leitura (desfeita depois) mostra o mesmo plano sem índice, sem travar nada. No banco local, o `DROP INDEX` em transação continua valendo.

As consultas do histórico do órgão (etapa 7a) custaram menos de 0,1 ms cada nesta medição porque ainda não havia resultados gravados; elas entram de novo quando a carga histórica terminar.

O procedimento original, para repetir no banco local:

1. Capturar a consulta que o `filtrar` realmente executa:

```
RADAR_LOG_SQL=true uv run radar filtrar
```

2. Abrir o psql:

```
docker compose exec db psql -U radar -d radar
```

3. Com o índice, rodar `EXPLAIN (ANALYZE, BUFFERS)` seguido da consulta copiada do passo 1. Para uma versão curta, de um só termo:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT numero_controle_pncp FROM edital
WHERE busca @@ phraseto_tsquery('portuguese', radar_unaccent('hidrometro'));
```

4. Sem o índice. `DROP INDEX` é transacional no Postgres, então dá para medir e desfazer sem risco:

```sql
BEGIN;
DROP INDEX ix_edital_busca;
EXPLAIN (ANALYZE, BUFFERS)
SELECT numero_controle_pncp FROM edital
WHERE busca @@ phraseto_tsquery('portuguese', radar_unaccent('hidrometro'));
ROLLBACK;
```

5. Registrar aqui os dois planos (tipo de varredura, por exemplo `Bitmap Index Scan` com índice e `Seq Scan` sem) e os tempos de execução.

Com 38 mil linhas o planejador pode escolher varredura sequencial mesmo com o índice, porque a tabela é pequena. Se isso acontecer, é um resultado legítimo: registre e explique em vez de "corrigir". Vale repetir com um termo raro e um comum, porque o índice pesa diferente em cada caso.


## O que as revisões encontraram

Cada item foi achado numa revisão e corrigido antes do fechamento:

- **200 malformado virando página vazia** (commit `469d4fb`): um HTTP 200 sem os campos esperados era lido como "sem registros". Agora é falha retentável.
- **Página tardia vazia truncando a janela** (commit `b44e49f`): uma página no meio da janela que vinha vazia encerrava a coleta como se tivesse acabado. Agora a contagem de registros distintos contra `totalRegistros` e a conferência do total de páginas derrubam a janela para `falha`.
- **Exceção inesperada travando as reexecuções** (commit `b44e49f`): uma janela que estourava ficava `em_andamento` e atrapalhava as rodadas seguintes. Agora o erro é isolado, a janela vira `falha`, e janelas `em_andamento` órfãs voltam para `pendente`. O advisory lock garante um coletor só.
- **Página com erro no processamento** (commit `24bc7a6`): uma página que falha ao registrar é isolada, o resto segue e o código de saída do comando reflete a falha.

## Perguntas de entrevista

Responda com suas palavras, citando o arquivo ou o comportamento. A etapa só fecha (tag) quando estiverem respondidas.

**1. Por que a janela é dividida no erro 500 da página 1 e não nas páginas seguintes?**

Resposta:

**2. Por que guardar o JSON bruto se a tabela `edital` já tem os campos?**

Resposta:

**3. O que acontece se o PNCP ficar fora do ar por 15 dias, com lookback de 10?**

Resposta:

**4. Por que a função de unaccent precisa ser `IMMUTABLE`?**

Resposta:

**5. Onde a fila vai entrar no tema 2 e que problema ela resolve que a `cli` não resolve?**

Resposta:
