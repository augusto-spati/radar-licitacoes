# Etapa 5: medições (rascunho; a nota final incorpora e este arquivo sai)

## Antes (main com a 4b, build local no Docker 29.2, Windows)

| Imagem | Tamanho (`docker image inspect -f {{.Size}}`) |
|---|---|
| `radar-api:antes` (Lambda: API, processador, coletora, planejador) | 656.895.327 B (626,5 MiB) |
| `radar-coletor:antes` (Fargate) | 237.964.881 B (226,9 MiB) |

`python -X importtime` dentro de `radar-api:antes` (acumulado, 1ª execução do container):

| Módulo | Acumulado |
|---|---|
| `radar.processador.lambda_handler` | 798 ms |
| `radar.processador.lambda_coletora` | 805 ms |
| `radar.processador.lambda_planejador` | 925 ms |
| `radar.api.lambda_handler` | 1270 ms |

`Init Duration` em produção (7 dias): a medir pelo coordenador (Task 8).

## Depois (branch `etapa-obs`, mesmo Docker, OTel 1.45.2)

| Imagem | Antes | Depois | Diferença |
|---|---|---|---|
| `radar-api` | 656.895.327 B | 664.605.203 B | +7.709.876 B (+7,4 MiB) |
| `radar-coletor` | 237.964.881 B | 245.783.210 B | +7.818.329 B (+7,5 MiB) |

Aceite do spec (+ ≤ 40 MB): ok nas duas.

`-X importtime` acumulado, 3 execuções cada (ms), mesma máquina:

| Módulo | Antes | Depois |
|---|---|---|
| `radar.processador.lambda_handler` | 848 / 791 / 673 | 715 / 686 / 699 |
| `radar.processador.lambda_coletora` | 688 / 705 / 720 | 698 / 691 / 680 |
| `radar.api.lambda_handler` | 1143 / 1008 / 1068 | 1089 / 1088 / 1084 |

Diferença dentro do ruído: o handler importa só a API do OTel (`opentelemetry.trace`); o SDK, o
exportador, `requests` e `protobuf` ficam em `radar.comum.telemetria_sdk`, importado só com
`RADAR_TELEMETRIA=xray` na primeira invocação. Custo marginal desse import (depois do handler
carregado): 89 / 83 / 87 ms, que entram no `Init Duration` da primeira chamada das Lambdas com
telemetria (aceite: + ≤ 300 ms). A `radar-api` nunca liga a telemetria.

## Notas da revisão final

- **Amostragem efetiva acima dos 25 %.** `RADAR_TRACES_FRACAO = 0.25` vale para cada raiz
  sem link amostrado; raiz com link amostrado é sempre amostrada (`AmostradorDoRadar`, regra
  2). Na cadeia do PNCP cada etapa é uma raiz nova ligada por link à anterior
  (`coleta.planejar` → `coleta.janela` → `processar mensagem`), então a chance de uma etapa
  ser amostrada é 1 − 0,75ᵏ, com k = número de raízes até ela: ~44 % nas fatias (k = 2) e
  ~58 % no consumo (k = 3). Como as `coletar_janela` do dia nascem no contexto do
  planejador, um planejador amostrado leva junto todas as fatias e consumos do dia ("tudo
  ou nada por dia", via link); sem ele, cada fatia sorteia por conta própria e arrasta os
  seus consumos. O custo de ingestão no `aws/spans` deve ser estimado com essas taxas, não
  com 25 %.
- **`ExecucaoOk` vem também do `radar analisar`.** A métrica não tem dimensão (o orçamento é
  de 10 métricas; o total segue 9), então o `Minimum` diário no dashboard mistura três
  emissores: `atualizar`, planejador e `analisar`. Um 0 no dia pede olhar o log
  `metrica_registrada` ou os spans raiz (`coleta.planejar`, `analise.rodada`) para saber
  qual falhou. `analisar` emite 1 quando outra rodada já está com a trava (não é falha).
- **Frescor (ruling do coordenador).** O SLI é "medição > 36 h". O alarme
  `radar-frescor-estourado` passa a ser `Maximum` diário (86.400 s) > 36, 1 de 1 datapoint,
  `treat_missing_data = "notBreaching"`: dispara uma vez por dia com medição estourada; dia
  sem medição não dispara (o coletor parado é coberto por `radar-coletor-nao-rodou`). Isso
  substitui o "6 de 6 períodos de 6 h, ausente = violação" do spec e do plano.
- **`auditar` só imprime o frescor** (`frescor <fonte>: <h> h`), sem `FrescorHoras`: é
  manual, e uma execução avulsa não deve disparar nem calar o alarme.
