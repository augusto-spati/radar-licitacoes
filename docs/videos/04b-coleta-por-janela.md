# Vídeo 4b: coleta do PNCP por janela

Roteiro em tópicos. Material de apoio: `docs/etapas/04b-coleta-por-janela.md`.

## Gancho (30 s)

- Pergunta: "Uma janela de 1 dia leva cerca de 20 min. A Lambda aguenta 15. Como coletar assim?"
- Contexto: o laço único no Fargate levava cerca de 4 h.

## O desenho (3 min)

- Planejador às 06:00 enfileira uma mensagem por janela na `radar-janelas`.
- A coletora trabalha até um orçamento de 540 s, grava o checkpoint por página e, se falta página, enfileira a continuação.
- `MaximumConcurrency = 2` por cortesia com o PNCP.

## Lease e fencing (4 min)

- Reivindicar com `UPDATE ... RETURNING` e relógio do banco; lease de 960 s.
- Fencing a cada página: se o token não é mais o dono, rollback e para.
- `lote` como token da mensagem: mensagem velha vira `obsoleta`.
- Por que o lease vence antes do visibility timeout.

## Demonstração gravável: a cadeia de fatias (5 min)

1. Disparar o planejador manualmente: 14 janelas enfileiradas.
2. No CloudWatch Logs da `radar-coletora`, seguir uma janela longa: `janela_continua` (fatia 1), `janela_continua` (fatia 2) e `janela_concluida`, com o `lote` subindo.
3. Mostrar `radar auditar` com "(página p/t)" durante a cadeia.
4. Mostrar o HTTP 429 do PNCP com 2 coletoras e a espera de 60 s do cliente.
5. Tempo total e custo: dizer "a medir em 2026-12-03".

## Achados (3 min)

- `aws:SourceArn` do Scheduler é o do schedule-group, não o da agenda.
- `maximum_concurrency` impede o Lambda de reduzir pollers em fila ociosa; tirado da `radar-editais`. Medir `NumberOfEmptyReceives`.

## Perguntas (1 min)

Deixar na tela as cinco de `docs/etapas/04b-coleta-por-janela.md`.

## Fecho

- Entregue: PNCP por fatias com checkpoint, lease com fencing e cortesia.
- Limite honesto: tempo total e custo ainda a medir; o 429 pode exigir pausa entre páginas.
