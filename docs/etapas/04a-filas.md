# Etapa 4a: filas entre a coleta e os editais

Até a etapa 3, quem coletava também gravava o edital, no mesmo processo. Esta etapa separa as duas pontas com uma fila: a coleta grava a página bruta e avisa; um consumidor, noutro processo, transforma a página em editais. O desenho completo está em `docs/superpowers/specs/2026-11-26-etapa-04-filas-design.md`.

## O que foi feito

- **Outbox.** A página bruta e a linha da tabela `outbox` são gravadas na mesma transação. Não existe o caso "gravei a página e a mensagem se perdeu", nem o contrário. O pgmq não existe no Neon, por isso a fila é o SQS e a outbox faz a ponte.
- **Relay.** Lê as linhas da outbox ainda sem `publicada_em`, envia ao SQS em lotes e marca como publicadas. Uma linha recusada guarda `tentativas` e `erro`, e o relay segue pelo cursor, sem prender o lote inteiro.
- **SQS e Lambda consumidora.** Fila `radar-editais`, Lambda `radar-processador` e fila de mensagens mortas (DLQ) com `maxReceiveCount = 5`. O visibility timeout é 6 vezes o timeout do consumidor.
- **Consumidor idempotente.** Mensagem repetida não duplica nem altera nada. O processamento marca `processado_em` e o upsert do edital continua sendo "a versão mais nova vence".
- **Retrato inteiro como mensagem.** Para Sanepar e Compesa, a mensagem `retrato_coletado` aponta para o retrato todo. O consumidor registra todas as páginas e só depois reconcilia. Um retrato que já não está `concluido` (obsoleto) é descartado sem gravar nem reconciliar.
- **DLQ e `radar reprocessar-falhas`.** Falha esgotada vai para a DLQ, dispara o alarme `radar-editais-falhas-com-mensagens`, e o comando move as mensagens de volta para a fila principal.
- **Local sem AWS.** `FilaLocal` entrega a mensagem no próprio processo, com a mesma função de processamento, para testes e para o notebook.

## Números da rodada real

Rodada de 2026-11-27 (UTC), no Neon e na AWS, com `RunTask atualizar --fontes sanepar,compesa`.

| Medida | Resultado |
|---|---|
| Linhas pendentes na outbox quando a migração `0005` rodou | 647 páginas do PNCP (de uma coleta manual interrompida) |
| Relay | 647 publicadas, 0 recusadas |
| Processamento | todas processadas pela Lambda; 0 pendentes no banco |
| Editais do PNCP | 38.382 para 53.798 |
| Retrato Sanepar 9 | 99 páginas, 92 registros, entregue por `retrato_coletado` e registrado sem mudanças (0 novos, 0 alterados, 0 encerrados) |
| DLQ na rodada normal | 0 mensagens |

A coleta manual que ficou aberta antes da migração foi parada de propósito: páginas gravadas pela imagem antiga ficariam sem linha na outbox. As janelas em andamento voltaram a `pendente` e o checkpoint por página retomou o trabalho.

## O que a rodada real ensinou (e os testes não pegaram)

1. **Advisory lock não serve para essa coleta no Neon.** A `trava_fonte` segurava um lock numa conexão com transação aberta. O Neon encerra sessões ociosas por `idle_in_transaction_session_timeout`, a sessão caiu durante os cerca de 14 min da Sanepar, e o `pg_advisory_unlock` do fim falhou com `retrato_erro_inesperado`. A exclusão mútua tinha acabado sem aviso. Além disso, a URL de produção passa pelo pooler (PgBouncer em modo transação), onde um lock de sessão não é confiável nem sem queda. Correção, PR #9: lease em tabela, com dono e prazo, obtido e liberado em transações curtas (detalhes em `docs/etapas/03-coletores.md`). O Postgres do testcontainers não tem esse timeout nem pooler, por isso os testes passavam.
2. **A linha `resultado:` tinha sumido.** A 4a removeu da CLI a linha que o alarme "coletor não rodou" contava. Sem ela o alarme dispararia cerca de 30 h depois, com o coletor saudável. Hotfix, PR #8, e um teste estático que amarra o filtro do alarme ao texto da CLI.
3. **A Compesa "não respondia" a partir da AWS.** `ConnectTimeout` duas vezes, de us-east-2; do Brasil o mesmo endereço respondeu. A falha foi registrada corretamente, e a Compesa saiu do agendamento da nuvem (PR #10). Depois se viu que o bloqueio era nosso: o security group só liberava a saída nas portas 443 e 5432, e o portal usa a 8743 (ver `03-coletores.md`).
4. **O perfil `radar` com login novo exige `botocore[crt]` no boto3.** Para o `reprocessar-falhas` local, as credenciais foram exportadas para o ambiente.

## A demonstração da DLQ

Uma mensagem envenenada (`81c4a473`) foi enviada às 00:46:44 UTC. O consumidor falhou em cada recebimento, a cada 12 min, que é o visibility timeout:

| Recebimento | Horário (UTC) |
|---|---|
| 1 | 00:46 |
| 2 | 00:58 |
| 3 | 01:10 |
| 4 | 01:22 |
| 5 | 01:34 |

Depois do quinto, a mensagem foi para a DLQ (cerca de 01:47). O alarme `radar-editais-falhas-com-mensagens` estava a conferir no momento do registro. Então `radar reprocessar-falhas` respondeu "1 mensagens sendo movidas para a fila principal". Como a mensagem continua envenenada, ela volta à DLQ por volta de 02:50 UTC, e o passo final é esvaziar a DLQ (PurgeQueue).

## Perguntas de entrevista

Responda com suas palavras, citando o arquivo ou o comportamento. A etapa só fecha quando estiverem respondidas.

1. Por que gravar a página e a linha da outbox na mesma transação, em vez de gravar no banco e depois enviar ao SQS (dual write)? O que cada falha possível deixaria para trás nos dois desenhos?

   Resposta:

2. O SQS entrega pelo menos uma vez. Como o consumidor fica idempotente para uma página e como fica para um retrato inteiro? Onde mora a garantia?

   Resposta:

3. Por que um retrato obsoleto não reconcilia? O que aconteceria se reconciliasse com um retrato velho?

   Resposta:

4. Por que o visibility timeout é 6 vezes o timeout do consumidor, e o que muda se o `maxReceiveCount` for 1, 5 ou 50? Mostre a conta dos 5 recebimentos a cada 12 min da demonstração.

   Resposta:

5. Por que um lease em tabela funciona onde o advisory lock falhou? O que cada um assume sobre a conexão e sobre o pooler, e qual o custo do lease quando um processo morre?

   Resposta:
