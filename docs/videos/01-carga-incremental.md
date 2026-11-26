# Vídeo 1: carga incremental de uma fonte instável

Roteiro em tópicos. Material de apoio: `docs/etapas/01-carga-incremental.md`. Estado do código para mostrar: tag `etapa-01-carga-incremental` (ainda a criar, só depois das respostas às perguntas).

## Gancho (30 s)

- Pergunta: "Se uma API pública cai no meio da coleta, como você sabe que não perdeu dados?"
- Mostrar o número: 38.382 editais em 30 dias, 120 janelas, 11 retentativas automáticas, nenhuma janela perdida.
- Promessa: no fim, uma auditoria que acha buraco de propósito.

## O problema: uma fonte lenta e instável (2 min)

- O PNCP é público e sem chave, mas: cerca de 5,5 s por página (e até 20 s), no máximo 50 registros por página, HTTP 500 intermitente, 429 em HTML sem `Retry-After`.
- Uma chamada gigante não serve. Pedaços pequenos com registro de andamento servem.
- Mostrar a tabela `coleta_janela` (um dia por modalidade, status, tentativas, erro).

## Como se constrói confiança (4 min)

- Checkpoint: cada página é gravada na hora. Matar o processo no meio e rodar de novo: retoma de onde parou (demonstrar com Ctrl+C).
- Regra central: falha nunca vira vazio. Mostrar o topo de `janelas.py`.
  - Os três jeitos de uma falha se disfarçar de dia vazio: 200 malformado, página tardia vazia, total que muda no meio.
  - Contar que as três foram achadas em revisão (commits `469d4fb` e `b44e49f`).
- Idempotência e versão mais nova vencendo: o `where data_atualizacao < excluded.data_atualizacao` do upsert, em `registro.py`.
- Bruto separado do tratado: por que guardar a página inteira.

## Demo: a auditoria achando um buraco (3 min)

- Estado inicial: `uv run radar auditar --endpoint publicacao --dias 30` sai com código 0.
- Provocar o buraco: marcar uma janela como `falha` ou apagar uma linha de `coleta_janela` (fazer num banco de teste, não no da carga).
- Rodar a auditoria de novo: lista o dia e a modalidade que faltam (ou a janela com o erro) e sai com código 1.
- Rodar `uv run radar processar`: refaz só o que falta. Auditar de novo: código 0.
- Ponto a fixar: quem coleta não é o único a dizer que coletou bem.
- Antes de gravar, ensaiar os comandos e conferir a saída real; o roteiro não traz a saída porque ela depende do banco naquele dia.

## O antes e depois do filtro (3 min)

- Antes: 36 abertos. Mostrar os intrusos: fitas de borda, bobinas, livros.
- Por quê: o stemmer português reduz "leitura" e "leiturista" ao mesmo radical.
- A correção: frases específicas em `config/filtro.toml` ("leitura de hidrometro" etc.), busca com `phraseto_tsquery`. Depois: 24 abertos.
- Ponto a fixar: filtro de texto se valida olhando o resultado.
- Contar a descoberta de Pernambuco: 948 editais no PNCP em 30 dias, quase nenhum de saneamento, Compesa em só 3. Companhias estaduais publicam em portal próprio. Limite da fonte, não bug.

## O índice GIN: antes e depois do EXPLAIN (2 min)

- Explicar em 30 s: coluna gerada, tsvector, índice GIN, `unaccent` e por que precisa ser `IMMUTABLE`.
- Mostrar os dois planos lado a lado (`EXPLAIN (ANALYZE, BUFFERS)` com e sem o índice, usando `BEGIN; DROP INDEX ...; ROLLBACK;`).
- **A medir:** os planos e os tempos ainda não existem. Gravar esta parte só depois de rodar o procedimento da nota. Se o planejador escolher varredura sequencial na tabela de 38 mil linhas, contar isso como resultado.

## O que o ORM escondeu (1 min)

- A escolha do ORM (D5) foi contra a recomendação inicial. Mostrar o SQL com `RADAR_LOG_SQL=true` e o `xmax = 0` no upsert.
- Ponto a fixar: o ORM esconde onde está o commit, o upsert e o plano de execução, e justamente isso é o conteúdo do tema.

## Perguntas (1 min)

Deixar na tela, uma por vez, e responder no vídeo:

1. Por que dividir a janela no 500 da página 1 e não nas seguintes?
2. Por que guardar o JSON bruto se a tabela `edital` já tem os campos?
3. O que acontece se o PNCP ficar fora do ar por 15 dias, com lookback de 10?
4. Por que a função de unaccent precisa ser `IMMUTABLE`?
5. Onde a fila entra no tema 2 e que problema ela resolve que a `cli` não resolve?

## Fecho

- O que a etapa entregou: carga que se retoma, não perde dado em silêncio e se audita.
- Próximo: API e tela (etapa 2), depois filas (tema 2).
