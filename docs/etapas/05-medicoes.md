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
