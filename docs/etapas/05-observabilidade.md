# Etapa 5: observabilidade (traces, métricas e SLOs)

Até a etapa 4b o radar já coletava, enfileirava e processava, mas responder "está saudável?" exigia abrir logs. Esta etapa acrescenta traces que atravessam a fila, um catálogo pequeno de métricas, SLOs escritos com SLI concreto, um dashboard e alarmes. O desenho completo está em `docs/superpowers/specs/2026-11-27-etapa-05-observabilidade-design.md`; as medições de custo estão em `docs/etapas/05-medicoes.md`.

## O que foi feito

- **Traces com spans manuais.** A API do OpenTelemetry é usada direto no código. O SDK e o exportador ficam em `radar.comum.telemetria_sdk`, importado de forma preguiçosa só com `RADAR_TELEMETRIA=xray`; um teste prova que o handler não importa SDK nem `requests`. Notebook, CI e testes rodam com a telemetria desligada.
- **Cadeia ligada por links.** O relay, a janela de coleta e o consumidor são raízes de trace separadas, ligadas por span links. O contexto viaja no `traceparent` da outbox (migração `0009_outbox_traceparent`) e no atributo da mensagem SQS. O `traceparent` leva só o bit `sampled`.
- **Amostragem.** `RADAR_TRACES_FRACAO = 0,25` vale para cada raiz sem link amostrado; raiz com link amostrado é sempre amostrada. A taxa efetiva por etapa é maior que 25 % (ver `05-medicoes.md`).
- **Métricas.** Um catálogo fechado, emitido por EMF (uma linha de log com `_aws`) na Lambda e por `PutMetricData` no Fargate: `FrescorHoras`, `PaginaComErro`, `LatenciaRequisicao{fonte}` e `ExecucaoOk`, somadas às métricas de filtro de log que já existiam. Total: 9 de 10 métricas customizadas.
- **Logs correlacionados.** O formatador JSON acrescenta `trace_id`, `span_id` e `xray_trace_id` quando há span válido, e há um log `requisicao_http` por tentativa, sem URL com query nem corpo.
- **Infra (`infra/prod/observabilidade.tf`).** Permissões do X-Ray nas roles, `PutMetricData` restrito ao namespace `Radar`, dashboard `radar`, consultas salvas do Logs Insights e os alarmes novos. Total: 9 de 10 alarmes nesta etapa; o 10º veio depois (abaixo).
- **Telemetria que não atrapalha.** Exportador ou `PutMetricData` que falham não mudam o código de saída nem os editais gravados (teste com ambos levantando exceção).

- **Auditoria diária com alarme (depois, em 2026-11-27).** `radar auditar --endpoint atualizacao` roda às 11:00 (EventBridge Scheduler, mesma task do coletor) e confere janelas do PNCP, retratos e fila. Com problema, loga `auditoria_falhou` com as contagens; o filtro de log `radar-auditoria-falhou` vira a métrica `RadarAuditoriaFalhou` e o alarme do mesmo nome avisa no primeiro caso. Sem dado não dispara (quem cobre "não rodou" é `radar-coletor-nao-rodou`). Com ele, o radar usa 10 de 10 alarmes e 10 de 10 métricas customizadas do plano grátis.

## SLIs e SLOs

Janela de 28 dias corridos. Os alvos são provisórios: a primeira semana mede a linha de base.

| SLI | Medição | SLO | Alarme |
|---|---|---|---|
| Frescor por fonte (idade do dado mais novo que já virou edital) | `FrescorHoras` por fonte | em pelo menos 26 de 28 dias, medições de todas as fontes ≤ 36 h | `radar-frescor-estourado` |
| Taxa de páginas com erro | `PaginaComErro` 0/1 por página | `Average` em 28 dias ≤ 5 % | `radar-paginas-com-erro` (> 10 % em 24 h) |
| Latência p95 por fonte | `LatenciaRequisicao{fonte}` | PNCP ≤ 40 s; Sanepar e Compesa ≤ 10 s, em 26 de 28 dias | sem alarme (painel) |
| Idade da mensagem mais velha | `ApproximateAgeOfOldestMessage` da `radar-editais` | 99 % dos intervalos de 5 min ≤ 900 s | `radar-editais-mensagem-antiga` |
| Sucesso da execução agendada | `ExecucaoOk` 1/0 | 27 de 28 dias com execução e todas com 1 | `radar-coletor-nao-rodou` |

O alarme de frescor ficou como decisão do coordenador na revisão final: `Maximum` diário (86.400 s) acima de 36 h, 1 de 1 datapoint, dado ausente tratado como `notBreaching`. Ele dispara uma vez por dia com medição estourada; o coletor parado é coberto por `radar-coletor-nao-rodou`. O `auditar` só imprime o frescor, sem emitir a métrica, para que uma execução manual não dispare nem cale o alarme. `ExecucaoOk` não tem dimensão e vem de três emissores (`atualizar`, planejador e `analisar`); um 0 no dia pede olhar o log `metrica_registrada` ou os spans raiz para saber qual falhou.

## Números reais

Medições de imagem (Docker local, antes e depois, em `05-medicoes.md`):

| Imagem | Antes | Depois | Diferença |
|---|---|---|---|
| `radar-api` | 656.895.327 B | 664.605.203 B | +7,4 MiB |
| `radar-coletor` | 237.964.881 B | 245.783.210 B | +7,5 MiB |

O `importtime` dos handlers não mudou além do ruído. O import marginal do SDK com o exportador foi de 83 a 89 ms, pago só na primeira chamada das Lambdas com telemetria. A suíte fechou em 821 testes passando e 3 pulados (os mesmos pulos da main).

Rodada real de 2026-11-27 (UTC):

| Medida | Resultado |
|---|---|
| Transaction Search | ligado pela API; plano FREE aceito; estado ACTIVE; indexação 1 % |
| Retenção de `aws/spans` | 7 dias, definida pela CLI |
| Métricas EMF no CloudWatch | `ExecucaoOk`, `LatenciaRequisicao`, `PaginaComErro` (além das 3 de filtro de log) |
| Alarmes e métricas customizadas | 9 de 10 e 9 de 10 |
| Primeiro trace no X-Ray | `processar mensagem` da `radar-processador`, cerca de 30 min após o deploy |

## O que a rodada ensinou

1. **`aws/spans` é reservado.** O Terraform não consegue criá-lo: `CreateLogGroup` recusa o prefixo `aws/`. O grupo fica com o X-Ray, e o `.tf` traz o runbook (inclusive a nota de `terraform import`).
2. **A ingestão atrasa.** O primeiro trace apareceu cerca de 30 min depois do deploy. Com 1 % de indexação, só uma fração dos traces aparece em `GetTraceSummaries`; o resto fica consultável em `aws/spans`. Para provar a cadeia, o planejador foi invocado uma vez com `RADAR_TRACES_FRACAO=1.0`, e a configuração voltou a 0,25.
3. **O flush do SDK 1.45 ignorava o timeout.** Medido: 3,0 s com timeout de 0,5 s. Correção: `force_flush` numa thread daemon com teto, e o teste usa um exportador lento de 3 s e exige `descarregar(0.5)` abaixo de 1 s.
4. **`PutMetricData` tinha timeouts padrão de 60 s.** Agora conexão de 2 s, leitura de 5 s e 2 tentativas; a falha é engolida e logada.
5. **O alarme de frescor disparava com uma só medição**, ao contrário do que o comentário dizia. A decisão está na seção de SLOs.
6. **A revisão achou o que o teste unitário não achava.** Os três defeitos acima passavam nos testes e só apareceram medindo ou relendo o contrato real do SDK e do boto.

## Perguntas de entrevista

Responda com suas palavras, citando o arquivo ou o comportamento. A etapa só fecha quando estiverem respondidas.

1. Como a amostragem funciona quando cada etapa da cadeia é uma raiz de trace ligada por span links? Por que a taxa efetiva fica acima de 25 % e o que isso muda na estimativa de custo?

   Resposta:

2. Por que spans manuais com a API do OpenTelemetry, e não auto-instrumentação? O que cada abordagem custa em imagem, em tempo de inicialização e em controle sobre o que vai para o span?

   Resposta:

3. EMF ou `PutMetricData`? O que cada um assume sobre o ambiente (Lambda, Fargate com `awslogs`), sobre latência e sobre custo?

   Resposta:

4. A telemetria não pode atrasar nem derrubar o trabalho. Como o flush com teto, os timeouts do `PutMetricData` e o teste de exportador quebrado garantem isso? O que se perde quando a telemetria falha?

   Resposta:

5. Por que o catálogo de métricas é fechado em 10 e por que `FrescorHoras` não tem dimensão de fonte enquanto `LatenciaRequisicao` tem? O que acontece com o custo se alguém acrescentar um identificador de alta cardinalidade como dimensão?

   Resposta:
