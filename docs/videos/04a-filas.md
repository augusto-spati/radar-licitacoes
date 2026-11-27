# Vídeo 4a: filas entre a coleta e os editais

Roteiro em tópicos. Material de apoio: `docs/etapas/04a-filas.md`.

## Gancho (30 s)

- Pergunta: "A coleta grava a página e o edital no mesmo processo. O que acontece quando uma mensagem quebra o consumidor?"
- Promessa: mostrar uma mensagem envenenada indo até a DLQ, o alarme e o reprocessamento, e o defeito de produção que nenhum teste pegou.

## Outbox e relay (3 min)

- Dual write: gravar no banco e depois enviar ao SQS deixa uma janela em que um dos dois se perde.
- Outbox: página e linha da outbox na mesma transação. O relay publica depois e marca `publicada_em`.

## Consumidor idempotente (3 min)

- Entrega pelo menos uma vez: a mesma mensagem pode chegar duas vezes.
- Página: upsert "a versão mais nova vence" e `processado_em`.
- Retrato inteiro como mensagem: registra todas as páginas e só então reconcilia. Retrato obsoleto é descartado.
- Mostrar a rodada real: 647 páginas publicadas e processadas, PNCP de 38.382 para 53.798 editais, retrato Sanepar 9 com 99 páginas e 92 registros, DLQ 0.

## Demonstração gravável: mensagem envenenada (6 min)

1. Enviar a mensagem envenenada à `radar-editais` (mostrar o comando e o id).
2. Mostrar no CloudWatch os recebimentos com erro a cada 12 min (visibility timeout de 6 vezes o timeout do consumidor). Na rodada real: 00:46, 00:58, 01:10, 01:22 e 01:34 UTC. Em vídeo, cortar a espera e mostrar os logs.
3. Quinto recebimento (`maxReceiveCount = 5`): a mensagem vai para a DLQ. Mostrar a contagem da DLQ.
4. Mostrar o alarme `radar-editais-falhas-com-mensagens` e o e-mail.
5. Rodar `radar reprocessar-falhas`: "1 mensagens sendo movidas para a fila principal".
6. Dizer o limite: a mensagem é envenenada, então volta à DLQ. Esvaziar com PurgeQueue.

## O que a rodada real ensinou (4 min)

1. Advisory lock morto pelo `idle_in_transaction_session_timeout` do Neon e não confiável pelo pooler. Solução: lease em tabela (PR #9).
2. A linha `resultado:` que o alarme contava foi removida. Hotfix (PR #8) e teste estático.
3. A Compesa não responde a partir da AWS. Saiu do agendamento (PR #10).
4. O perfil `radar` com login novo exige `botocore[crt]`; credenciais exportadas.

## Perguntas (1 min)

Deixar na tela as cinco de `docs/etapas/04a-filas.md`.

## Fecho

- Entregue: fronteira assíncrona com outbox, SQS, consumidor idempotente, DLQ e reprocessamento.
- Limite honesto: o consumidor só é tão bom quanto a idempotência que os testes provam.
- Próximo: a coleta do PNCP por janela (4b).
