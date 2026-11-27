# Etapa 4b: coleta do PNCP por janela, em Lambda

A 4a separou a coleta do processamento. A 4b tira o PNCP do container Fargate e o coloca em fila, uma mensagem por janela de datas. O desenho está em `docs/superpowers/specs/2026-11-27-etapa-04b-coleta-por-janela-design.md`.

## Por que

A atualização do PNCP rodava num laço único no Fargate e levava cerca de 4 h: o pregão tem em torno de 80 páginas por dia, a cerca de 15 s por página. Uma janela de 1 dia leva cerca de 20 min e não cabe numa Lambda de 15 min. A saída é quebrar a janela em fatias com checkpoint, e não esticar o limite.

## O que foi feito

- **Planejador.** Lambda `radar-planejador`, disparada pelo EventBridge Scheduler às 06:00 de Brasília. Planeja as janelas do recuo e enfileira as elegíveis na fila `radar-janelas`.
- **Fila de janelas.** Uma mensagem `coletar_janela` por janela, com `janela_id` e `lote`, publicada pela outbox. Falha esgotada vai para a DLQ `radar-janelas-falhas`, com alarme.
- **Fatias com orçamento.** A Lambda `radar-coletora` coleta páginas até um orçamento de 540 s, gravando o checkpoint (`proxima_pagina`) a cada página. Toda fatia pede ao menos uma página, para que um orçamento pequeno não trave a cadeia.
- **Lease com fencing.** Reivindicar a janela é um `UPDATE ... RETURNING` atômico com o relógio do banco. O commit de cada página só vale se o dono do lease ainda for o mesmo; senão a página sofre rollback e a invocação para (`lease_perdida`). O `lote` da mensagem é o fencing token: mensagem velha vira `obsoleta` e é confirmada sem chamar o PNCP.
- **Continuação.** Se faltam páginas, a fatia grava a próxima mensagem na outbox na mesma transação que solta a janela, com `lote + 1`. Total de páginas e de registros esperados ficam no banco, para a checagem de completude valer entre fatias.
- **Desistência por falhas.** A coluna `falhas` sobe a cada falha e zera na conclusão. Com 5 falhas seguidas o planejador deixa de enfileirar a janela.
- **Cortesia.** `MaximumConcurrency = 2` na coletora: no máximo duas janelas contra o PNCP ao mesmo tempo.
- **Fargate só com retratos.** O agendamento do Fargate ficou com `sanepar`. O PNCP vai pelo planejador e pelas Lambdas.

## Números da primeira rodada

Planejador manual, 2026-11-27 (UTC): 14 janelas enfileiradas. Com duas coletoras simultâneas, o PNCP respondeu HTTP 429, e o cliente esperou 60 s antes de tentar de novo.

| Medida | Resultado |
|---|---|
| Janelas enfileiradas pelo planejador | 14 |
| HTTP 429 com 2 coletoras | sim; o cliente espera 60 s |
| Tempo total da primeira rodada | a medir (2026-12-03) |
| Custo da semana (Lambda, SQS) | a medir (2026-12-03) |

A acompanhar: se o 429 levar janelas à DLQ, a saída é uma pausa entre páginas, como a do cliente educado, ou concorrência reservada 1 na coletora.

## O que a rodada real ensinou

1. **O `aws:SourceArn` do Scheduler não é o da agenda.** A criação da agenda falhou porque a trust usava o ARN da própria agenda. O valor que a AWS documenta para essa condição é o do schedule-group (`schedule-group/default`). Corrigido e reaplicado.
2. **`maximum_concurrency` tem custo de polling.** Com ele no event source mapping, o Lambda não reduz os pollers de uma fila ociosa; com duas filas, o polling passaria de 1 milhão de requisições por mês. Ficou só na `radar-janelas`, onde a cortesia com o PNCP exige. A `radar-editais` voltou a não ter limite. A medir: `NumberOfEmptyReceives` em 24 h.
3. **Lease por janela no lugar do lock global do PNCP.** A trava de chave `pncp` do PR #9 seria redundante e não excluiria a Lambda, que não a pega. Cada janela tem o próprio lease, renovado a cada página, no `coletar_pendentes` local e na Lambda.

## Deriva da lista de atualização

**Fato (produção, 2026-11-27).** Todas as janelas que falharam eram de pregão (modalidade 6, mais de 4.000 registros) no endpoint `atualizacao`, com erros como "incompleta: 4435 de 4440 registros" ou "pagina 62 inconsistente: total_paginas=88, esperado 89". A leitura de uma janela leva dezenas de minutos (fatias, pausas, HTTP 429) e a lista muda nesse meio-tempo. Recoletar desde a página 1 voltava a falhar até a desistência (5 falhas).

**Decisão.** Só no endpoint `atualizacao` (`coleta/fatias.py`):

- na conferência final, se faltarem no máximo max(10, 0,5% do total) registros, a janela conclui e loga `janela_concluida_com_deriva` (warning, com esperado, obtido e diferença), além do `janela_concluida` de sempre;
- se o total de páginas mudar no meio em ±1 página (e o de registros dentro do mesmo limite), o total novo é adotado e gravado no checkpoint, com log `janela_total_mudou`.

Fora disso, o comportamento é o de antes: falha e recoleta. A `publicacao` (carga inicial) continua estrita.

**Por que não se perde registro.** Um registro some da lista de atualização porque foi atualizado durante a leitura. A `dataAtualizacao` dele muda, e ele entra na janela de um dia posterior, que será coletada.

## Perguntas de entrevista

Responda com suas palavras, citando o arquivo ou o comportamento. A etapa só fecha quando estiverem respondidas.

1. O que é um fencing token e por que o `lote` e o `travada_por` evitam que uma invocação atrasada regrida o checkpoint?

   Resposta:

2. Como a continuação que reenfileira a si mesma evita um laço infinito? O que garante que cada fatia avança e que a cadeia termina?

   Resposta:

3. Qual a relação entre o orçamento (540 s), o timeout da Lambda (900 s) e o visibility timeout da fila? Mostre a conta do pior caso de uma página começada no fim do orçamento.

   Resposta:

4. Por que o lease precisa vencer antes do visibility timeout? O que acontece com a mensagem de uma invocação que morreu?

   Resposta:

5. O PNCP devolveu 429 com 2 coletoras. Como cortesia (MaximumConcurrency) e retry se combinam, e o que você mediria para decidir entre pausa entre páginas e concorrência 1?

   Resposta:
