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
