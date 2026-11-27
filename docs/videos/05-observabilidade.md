# Vídeo 5: observabilidade (traces, métricas e SLOs)

Roteiro em tópicos. Material de apoio: `docs/etapas/05-observabilidade.md` e `docs/etapas/05-medicoes.md`.

## Gancho (30 s)

- Pergunta: "O coletor rodou, o código de saída foi 0 e os editais estão velhos. Como você descobre?"
- Promessa: seguir um trace da coleta até a Lambda que processa o edital e mostrar o dashboard com os SLOs.

## SLIs e SLOs (3 min)

- Cinco SLIs com definição exata: frescor por fonte, taxa de páginas com erro, latência p95, idade da mensagem mais velha, sucesso da execução agendada.
- Alvos provisórios e janela de 28 dias. SLO sem medição por trás é decoração.
- Alarme de frescor: 1 de 1 datapoint diário acima de 36 h, dado ausente não dispara.

## Traces e span links (4 min)

- Spans manuais com a API do OpenTelemetry; SDK importado só com `RADAR_TELEMETRIA=xray`.
- Cada etapa é uma raiz ligada à anterior por link; o `traceparent` viaja na outbox e no atributo da mensagem SQS.
- Amostragem de 25 % por raiz e por que a taxa efetiva é maior.

## Demonstração gravável: seguir um trace (6 min)

1. Mostrar o `RADAR_TRACES_FRACAO=1.0` temporário e invocar o planejador uma vez.
2. Avisar do atraso: a ingestão do Transaction Search levou cerca de 30 min na rodada real. Em vídeo, cortar a espera.
3. Abrir o X-Ray e achar `processar mensagem` da `radar-processador`.
4. Seguir os links: `coleta.planejar`, `coleta.janela`, consumo. Mostrar os atributos dos spans.
5. Colar o `xray_trace_id` do log para ir do trace ao log e voltar.
6. Mostrar que o resto dos traces está em `aws/spans` (indexação de 1 %) e voltar a fração para 0,25.

## Dashboard (3 min)

- Frescor por fonte, p95 de latência, taxa e contagem de páginas com erro, `ExecucaoOk`, idade da mensagem e DLQ, erros da `radar-processador`, estado dos alarmes.
- Métricas EMF chegando: `ExecucaoOk`, `LatenciaRequisicao`, `PaginaComErro`. Orçamento: 9 de 10 alarmes e 9 de 10 métricas.

## O que a rodada real ensinou (4 min)

1. `aws/spans` é reservado: o Terraform não cria; runbook no `.tf`.
2. O flush do SDK 1.45 ignorava o timeout (3,0 s com timeout de 0,5 s): thread com teto.
3. `PutMetricData` com timeouts de 60 s: agora 2 s e 5 s.
4. O alarme de frescor disparava com uma só medição: decisão registrada.
5. Custo medido: +7,4 MiB na imagem da API, cerca de 85 ms na primeira chamada.

## Perguntas (1 min)

Deixar na tela as cinco de `docs/etapas/05-observabilidade.md`.

## Fecho

- Entregue: traces ligados pela fila, métricas, SLOs escritos, dashboard e alarmes.
- Limite honesto: com 1 % de indexação, só uma fração dos traces aparece no X-Ray; o resto exige consulta em `aws/spans`.
- Próximo: tela das exigências (6b) e conferência dos gabaritos.
