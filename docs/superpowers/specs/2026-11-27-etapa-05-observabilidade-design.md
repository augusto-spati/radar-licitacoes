# Etapa 5: observabilidade (traces, métricas, SLOs)

Data: 2026-11-27. Decisões do Claude com autonomia dada pelo Augusto; revisar. Tema 3 da trilha
(observabilidade na AWS). Pesquisa com fontes em `~/carreira/projetos/tema3-observabilidade.md`; decisões
de base em `~/carreira/projetos/radar-decisoes-madrugada.md` (seção "Tema 3").

## Por que

O radar é um projeto de estudo público; a etapa existe para que o Augusto entenda e defenda, em entrevista
e em vídeo, **traces distribuídos com OpenTelemetry, propagação de contexto por uma fila, amostragem,
métricas baratas e SLOs escritos com SLIs concretos**. "Observabilidade" aparece nas vagas junto de
"eventos na AWS", e a etapa 4a deixou o caminho pronto para isso: coleta → outbox → relay → SQS → Lambda.
Utilidade para o usuário final (o tio) não muda; muda a capacidade de dizer, com números, se o radar está
fresco e saudável.

Hoje o radar só tem logs JSON (`comum/logs.py`) e quatro alarmes:

- `radar-coletor-nao-rodou` (filtro de métrica sobre a linha `resultado:` do log do coletor);
- `radar-editais-falhas-com-mensagens` (DLQ > 0);
- `radar-editais-mensagem-antiga` (`ApproximateAgeOfOldestMessage` > 3600 s);
- `radar-processamento-falhou` (filtro de métrica sobre `processamento_falhou`).

Mais a regra do EventBridge `radar-coletor-falhou` (não é alarme do CloudWatch).

**Achado ao ler o código:** o commit `063d57a` (etapa 4a, cli) removeu a linha `resultado: ...` que a CLI
imprimia no fim do `atualizar`. O filtro `radar-coletor-execucoes` não casa mais nada, e o alarme
`radar-coletor-nao-rodou` (dado ausente conta como violação) vai disparar todo dia, mesmo com o coletor
rodando. Esta etapa devolve a linha e a prende num teste (contrato entre CLI e infra). Se a 4b for
implementada antes, o conserto pode ir num PR à parte.

Não há como responder hoje: quanto tempo leva uma página do PNCP no p95? Que fração das páginas falha
depois das retentativas? Uma mensagem que chegou à DLQ veio de qual página, de qual janela, de qual
execução? Quão velho está o dado de cada fonte agora?

## Objetivos

1. **Traces** OpenTelemetry das unidades de trabalho da coleta e do consumo, exportados por OTLP/HTTP
   direto para o X-Ray (sem sidecar, sem collector), com amostragem.
2. **Contexto atravessa a outbox e o SQS**: o span que gravou a página (ou concluiu o retrato) é ligado,
   por *span link*, ao span que a processou na Lambda, mesmo dias depois e em outro processo.
3. **Métricas** customizadas para cinco SLIs, dentro de 10 métricas customizadas no total (contando as 2
   existentes), sem custo além do plano grátis.
4. **SLOs escritos** (abaixo) com SLI, medição, alvo e janela; 1 dashboard; no máximo 10 alarmes no total.
5. Instrumentação **nas funções de coleta**, não no processo: vale igual para a coleta no Fargate
   (`radar atualizar`) e para a Lambda coletora da etapa 4b.
6. Logs passam a carregar `trace_id` e `span_id` (correlação log ↔ trace).
7. Testes sem rede (exporters em memória).
8. Tamanho da imagem e cold start da Lambda medidos antes e depois, com números na nota.
9. A etapa fecha com a nota `docs/etapas/05-observabilidade.md` (números, lições, 5 perguntas de
   entrevista) e o roteiro `docs/videos/05-observabilidade.md`.

## Não objetivos

- Instrumentar a API (FastAPI/Mangum) e o front. A API continua igual; só a imagem dela cresce (mesma
  imagem do processador) e isso é medido.
- Application Signals (exige sidecar no Fargate e cobra por sinal), Container Insights, CloudWatch agent,
  ADOT Collector, X-Ray SDK (em manutenção desde 25/02/2026).
- Auto-instrumentação do processo (`opentelemetry-instrument`, instrumentações de SQLAlchemy, psycopg,
  httpx, botocore). Ver "Abordagens".
- Tail sampling (exige collector).
- Métricas OTLP (`monitoring.<região>.amazonaws.com/v1/metrics`): ficam para depois; EMF e PutMetricData
  bastam e cabem no plano grátis.
- Relatório automático de SLO (GetMetricData não está no 1 milhão grátis de chamadas). O cálculo do mês é
  feito no dashboard e com as consultas salvas do Logs Insights.

## Antiobjetivos (falharia mesmo funcionando)

- **Telemetria derrubando a coleta**: exportador lento, X-Ray fora do ar, credencial sem permissão ou
  PutMetricData recusado fazendo `atualizar` falhar, atrasar mais que segundos, ou mudar o código de saída.
- Custo fora do plano grátis: 100% dos traces sem amostragem, dimensão de alta cardinalidade (id de
  janela, URL), métrica número 11, alarme número 11, Application Signals ligado por engano.
- Vazar contexto para fora: cabeçalho `traceparent` nas requisições ao PNCP, à Sanepar ou à Compesa.
- Span com dado sensível: URL do banco, corpo de página, conteúdo de edital.
- SLO decorativo: alvo sem medição concreta por trás, ou medição que só existe quando o sistema está bem
  (frescor medido só por quem acabou de coletar com sucesso).
- Alarme que dispara todo dia sem nada errado (é o caso do `radar-coletor-nao-rodou` hoje).

## Restrições

- AWS: projeto da nova experiência, plano FREE, **us-east-2**, SCPs gerenciadas.
- Grátis por mês (pesquisa, seção 2): 10 métricas customizadas, 10 alarmes padrão, 3 dashboards, 1 milhão
  de requisições de API, 5 GB de logs, 100 mil traces gravados. Cada combinação métrica + dimensões é uma
  métrica cobrada.
- O endpoint OTLP de traces (`https://xray.us-east-2.amazonaws.com/v1/traces`) exige **Transaction
  Search** ligado; com ele os spans vão para o log group `aws/spans` e a ingestão é cobrada a US$ 0,35/GB
  (1% indexado grátis). Estimativa abaixo; revisar na primeira semana (o Budget de US$ 1 já existe).
- Endpoint OTLP: só HTTP, protobuf, gzip ou nada, assinatura SigV4 (serviço `xray`).
- **Nenhum sidecar.** Lambda em imagem container **não aceita layer**: tudo que for preciso entra na
  imagem pelas dependências do `pyproject.toml` (os Dockerfiles já instalam pelo `uv export`).
- Neon: nenhuma consulta nova em laço; as consultas de frescor rodam duas vezes por execução.
- A pausa de 3 s entre requisições das fontes próprias continua; a medição de latência não a inclui.
- Arquivos guardados pelo contrato do front e `.github/workflows/*` exigem o trailer
  `Contrato-Front: aprovado`. **Esta etapa não muda nenhum deles** (o deploy atual já atualiza as duas
  Lambdas e o coletor; variáveis de ambiente vêm do Terraform).
- Merge na main só por PR, com os checks obrigatórios.
- A etapa 4b (coleta do PNCP por janela em Lambda, fila `radar-janelas`, planejador agendado) é
  implementada **antes** desta. Esta etapa não depende dos nomes exatos da 4b: depende das funções de
  coleta (`_coletar_janela`, `ClientePNCP`, `enfileirar`) que a 4b reutiliza. Onde a 4b criou handlers
  novos, eles ganham o mesmo envoltório de telemetria da Lambda processadora (ver "Lambdas").

## Abordagens consideradas

Para os traces (escolhida: **B**):

- A. `aws-opentelemetry-distro` com auto-instrumentação (`opentelemetry-instrument`). Era a decisão da
  madrugada. Traz dezenas de pacotes de instrumentação e instrumenta o **processo** (toda consulta SQL e
  toda chamada boto3 vira span), o que multiplica spans e custo e contraria o objetivo 5. Na Lambda em
  imagem, seria o layer copiado à mão para `/opt`.
- **B. SDK do OpenTelemetry + exportador OTLP/HTTP + assinatura SigV4 própria (~30 linhas com
  `botocore.auth.SigV4Auth`), spans manuais nas funções.** Poucas dependências, spans só onde há decisão
  (janela, página, retrato, lote do relay, mensagem). Mostra o mecanismo inteiro; o que a AWS mudaria no
  distro (exportador assinado) fica visível em um arquivo. Escolhida. **Desvio consciente da decisão da
  madrugada (que citava o distro); revisar.**
- C. X-Ray SDK. Em modo de manutenção. Descartada.

Para a propagação pela fila (escolhida: **B**):

- A. Injetar o contexto do **relay** no atributo da mensagem. Simples, mas liga o consumo ao relay, que é
  só o carteiro; a página foi coletada em outro momento, às vezes em outra execução.
- **B. Guardar o `traceparent` na linha da outbox, no momento em que ela nasce** (mesma transação da
  coleta), e levá-lo no atributo `traceparent` da mensagem SQS. O consumidor cria um span novo de raiz com
  **link** para esse contexto. Liga produtor e consumidor de verdade, sobrevive a container morto entre
  coletar e publicar, e é o tipo de detalhe que separa "usei OTel" de "entendi outbox". Escolhida.
- C. `AWSTraceHeader` (atributo de sistema do SQS) com tracing ativo da Lambda e propagador X-Ray. Liga
  como pai, não como link, e só no formato X-Ray. Descartada.

Para as métricas (escolhida: **C**):

- A. Só EMF (log com `_aws`). Automático na Lambda. No Fargate com o driver `awslogs`, a documentação só
  descreve extração via CloudWatch agent (sidecar) ou PutLogEvents com cabeçalho; não está verificado que
  funcione sem eles.
- B. Só PutMetricData. Funciona nos dois, mas na Lambda cada envio é chamada de rede no caminho da
  mensagem.
- **C. Uma interface, dois emissores**: EMF na Lambda (processador e, na 4b, a coletora); PutMetricData
  agregado no Fargate (lista de valores e contagens, que preserva percentis), descarregado no fim de cada
  janela e de cada retrato. Escolhida.

## Desenho

### Telemetria (`radar.comum.telemetria`)

- Um `TracerProvider` do próprio módulo (não o global do OTel, que só aceita ser definido uma vez e
  atrapalha os testes). `tracer()` devolve o tracer `radar`; sem configuração, é no-op.
- `RADAR_TELEMETRIA`: `desligada` (padrão: notebook, CI, testes) ou `xray`.
- `xray`: `BatchSpanProcessor` + `OTLPSpanExporter` (HTTP, protobuf, gzip, timeout 10 s) para
  `https://xray.<região>.amazonaws.com/v1/traces`, com uma `requests.Session` que assina cada requisição
  com SigV4 (`xray`, credenciais da role pela cadeia padrão do boto3, renovadas a cada assinatura).
- Recurso: `service.name` (`radar-coletor` no Fargate, `radar-processador` na Lambda, o nome da função na
  4b), `service.version` (`radar.__version__`), `deployment.environment=prod`, `cloud.region`.
- **Amostragem** (`RADAR_TRACES_FRACAO`, padrão 0,25 em prod; 1,0 nos testes): amostrador próprio
  `AmostradorDoRadar`:
  1. com pai: segue o pai (parent-based);
  2. sem pai e com algum link amostrado: amostra (o consumo de uma página cuja coleta foi amostrada também
     é, para a cadeia ficar inteira);
  3. senão: `TraceIdRatioBased(fração)`.
- `descarregar(timeout_s=2)`: `force_flush` limitado; erro vira log `telemetria_erro`, nunca exceção.
  `encerrar()`: `shutdown` no fim do processo da CLI.
- Ids de trace: os aleatórios do W3C. (O X-Ray aceita ids W3C pelo endpoint OTLP; se a rodada real mostrar
  rejeição, entra `AwsXRayIdGenerator` do `opentelemetry-sdk-extension-aws`.)
- Helpers de propagação: `traceparent_atual() -> str | None` (W3C do span corrente, se válido);
  `link_de(traceparent) -> Link | None`; `atributos_sqs(traceparent) -> dict` (formato `MessageAttributes`
  do `SendMessage*`); `traceparent_do_registro(registro_lambda) -> str | None` (lê
  `messageAttributes.traceparent.stringValue` do evento do event source mapping).
- **Nada injeta contexto em requisições HTTP para as fontes.**

### Spans

Nome, tipo e atributos principais (prefixo `radar.` para os nossos; convenções semânticas do OTel para
HTTP e mensageria):

| Span | Tipo | Onde | Atributos |
|---|---|---|---|
| `coleta.janela` | INTERNAL, raiz | `janelas._coletar_janela` | `radar.janela.id`, `.endpoint`, `.modalidade`, `.data_ini`, `.data_fim`, `.paginas`, `.registros`, `radar.desfecho` (`concluida`, `falha`, `dividida`) |
| `pncp.buscar` | INTERNAL | `ClientePNCP.buscar` | `radar.pagina`, `radar.tentativas` |
| `GET` | CLIENT | `ClientePNCP._requisitar`, `ClienteEducado.get` (um por tentativa) | `http.request.method`, `server.address`, `http.response.status_code`, `error.type`, `radar.fonte` |
| `coleta.retrato` | INTERNAL, raiz | `retratos._executar` | `radar.fonte`, `radar.retrato.id`, `.paginas`, `.registros`, `radar.desfecho` |
| `relay.lote` | PRODUCER | `relay.publicar_outbox` (um por lote) | `messaging.system=aws_sqs`, `messaging.destination.name`, `messaging.batch.message_count`, `radar.recusadas`; links para os `traceparent` das linhas |
| `processar mensagem` | CONSUMER, raiz | `processador.lambda_handler` e `FilaLocal` | `messaging.message.id`, `radar.outbox_id`, `radar.tipo`, `radar.desfecho`; **link** para o `traceparent` da mensagem |
| `editais.registrar`, `editais.reconciliar` | INTERNAL | `processador.consumidor` | contagens (`inseridos`, `atualizados`, `invalidos`, `encerrados`) |

- Raiz por **unidade de trabalho** (janela, retrato, lote, mensagem), não por execução do `atualizar`: um
  trace de 4 h com milhares de spans não serve para nada e a amostragem por execução seria tudo ou nada.
  Na Lambda coletora da 4b, cada invocação abre `coleta.janela` (com link para o `traceparent` da mensagem
  da janela, se houver) e continua a partir do checkpoint; o atributo `radar.janela.pagina_inicial`
  distingue as continuações.
- Erro: `span.record_exception` + status `ERROR` nos pontos onde o código já faz `logger.exception` ou
  `_falhar`.
- Sem spans de SQL nem de boto3.

Volume estimado: ~1.000 páginas/dia no PNCP (atualização de ~4 h a ~15 s/página) × ~3 spans + ~1.000
mensagens × ~2 spans + retratos (~150 requisições) ≈ 5.500 spans/dia; com fração 0,25, ~1.400/dia, ~45
mil/mês, ~45 MB/mês a ~1 KB/span ≈ **US$ 0,02/mês**. A medir.

### Outbox leva o contexto (migração `0006_outbox_traceparent`)

- `outbox.traceparent text null`.
- `enfileirar` grava `traceparent_atual()` na linha. Todos os produtores (PNCP, retratos e os da 4b, se
  usarem `enfileirar`) ganham isso sem mudar.
- `Mensagem` ganha `traceparent: str | None = None` (último campo). O corpo JSON (`para_corpo`/`de_corpo`)
  **não muda**: o contexto vai no atributo, para mensagens em voo durante o deploy continuarem válidas.
- `publicar_outbox` copia `outbox.traceparent` para a `Mensagem`; `FilaSQS.enviar_lote` põe
  `MessageAttributes={"traceparent": ...}` quando houver (1 dos 10 atributos permitidos).
- `FilaLocal` passa a `Mensagem` inteira ao consumidor, então o link existe também localmente.
- A fila `radar-janelas` da 4b usa o mesmo `atributos_sqs` ao enviar.

### Métricas (`radar.comum.metricas`)

- Catálogo fechado no código (`CATALOGO`), namespace `Radar` (o mesmo dos filtros existentes). Métrica fora
  do catálogo ou dimensão fora do permitido é `ValueError` (é erro de programação).
- `RADAR_METRICAS`: `desligada` (padrão), `emf` (Lambda: uma linha JSON com `_aws` pelo `logging`, que o
  `FormatadorJson` já emite como objeto de topo) ou `cloudwatch` (Fargate: agrega em memória e chama
  `PutMetricData` com `Values`/`Counts` em `descarregar()`; falha vira log `metricas_erro`).
- `registrar(nome, valor, dimensoes={}, propriedades={})`; `descarregar()`. Propriedades (ex. `fonte`
  do frescor) só existem no EMF e no log, nunca como dimensão.
- Toda medição também sai como linha de log (`metrica_registrada`, com `fonte` e o valor): é o que o Logs
  Insights usa para o recorte por fonte sem pagar dimensão.

Orçamento de métricas customizadas (máximo 10):

| Métrica | Dimensões | Conta | Origem |
|---|---|---|---|
| `RadarColetorExecucoes` | — | 1 | existente (filtro de log) |
| `RadarProcessamentoFalhou` | — | 1 | existente (filtro de log) |
| `FrescorHoras` | — (fonte é propriedade) | 1 | nova |
| `PaginaComErro` (0 ou 1) | — | 1 | nova |
| `LatenciaRequisicao` (s) | `fonte` ∈ {pncp, sanepar, compesa} | 3 | nova |
| `ExecucaoOk` (0 ou 1) | — | 1 | nova |
| **Total** | | **8** | sobram 2 para a 4b |

`FrescorHoras` sem dimensão: cada fonte manda o seu valor na mesma métrica, e o `Maximum` é o da pior fonte
(é o que o alarme quer). `PaginaComErro` 0/1: o `Average` é a taxa de erro e o `SampleCount` é o número de
páginas, numa métrica só.

### SLIs e SLOs

Vocabulário do SRE Workbook (capítulo de pipelines de dados): frescor e corretude medidos de ponta a
ponta. Janela de todos os SLOs: **28 dias corridos**. Alvos são **provisórios**: a primeira semana mede a
linha de base e a nota registra se mudaram.

| SLI | Definição exata | Medição | SLO | Alarme |
|---|---|---|---|---|
| **Frescor por fonte** | `agora − instante do dado mais novo da fonte que já chegou a edital`. Retratos: `iniciado_em` do retrato `concluido` mais novo **com `processado_em`**. PNCP: `concluida_em` da janela de atualização concluída mais nova **sem nenhuma página com `processada_em` nulo**. Fonte sem dado nenhum: 9999 h. | `frescor_por_fonte(sessao, agora)` no **começo** e no **fim** de cada `atualizar` e em todo `auditar`; `FrescorHoras` por fonte + log `frescor_medido` | Em pelo menos 26 de 28 dias, todas as medições de todas as fontes ≤ 36 h | `radar-frescor-estourado`: `Maximum` > 36 h, período 6 h, 6 de 6 períodos, dado ausente = violação (sem medição por 36 h também é frescor estourado) |
| **Taxa de páginas com erro** | páginas cuja busca falhou depois das retentativas ÷ páginas tentadas. PNCP: cada `ClientePNCP.buscar`; retratos: cada `ClienteEducado.get` | `PaginaComErro` 0/1 por página | `Average` em 28 dias ≤ 5% (orçamento de erro: 5% das páginas) | `radar-paginas-com-erro`: `Average` > 10% em 24 h com `SampleCount` ignorado; dado ausente = ok |
| **Latência p95 por fonte** | duração de cada tentativa HTTP, do envio ao corpo lido, sem a pausa de cortesia nem a espera da retentativa | `LatenciaRequisicao{fonte}` | p95 diário ≤ 40 s no PNCP (respostas típicas de 20 a 26 s, timeout 90 s) e ≤ 10 s na Sanepar e na Compesa, em 26 de 28 dias | sem alarme (painel) |
| **Idade da mensagem mais velha** | `ApproximateAgeOfOldestMessage` da `radar-editais` (a `radar-janelas` da 4b espera de propósito e fica fora) | métrica da AWS, grátis | 99% dos intervalos de 5 min ≤ 900 s | existente `radar-editais-mensagem-antiga` (> 3600 s) |
| **Sucesso da execução agendada** | execução agendada que terminou com código 0. Hoje: `radar atualizar` no Fargate; na 4b, também o planejador | `ExecucaoOk` 1/0 no fim de cada execução agendada | 27 de 28 dias com pelo menos uma execução e todas com 1 | existentes `radar-coletor-nao-rodou` (consertado) e a regra `radar-coletor-falhou` |

Orçamento de alarmes (máximo 10): 4 existentes + 2 novos (`radar-frescor-estourado`,
`radar-paginas-com-erro`) = **6**; a 4b tem até 4. Um teste estático conta os `aws_cloudwatch_metric_alarm`
de `infra/` e falha acima de 10; outro confere que os nomes do `CATALOGO` mais os `metric_transformation`
do Terraform somam no máximo 10 métricas.

### Dashboard e consultas

- `aws_cloudwatch_dashboard` `radar` (um só), com linhas de 36 h em anotação horizontal onde houver SLO:
  frescor (máximo) e, ao lado, tabela do Logs Insights com o frescor por fonte; p95 de latência por fonte;
  taxa de páginas com erro e número de páginas; `ExecucaoOk`; idade da mensagem mais velha e DLQ visíveis;
  erros, duração e throttles da `radar-processador`; estado dos alarmes.
- `aws_cloudwatch_query_definition` (grátis para guardar): frescor por fonte, p95 por fonte a partir do log
  `requisicao_http`, taxa de erro por execução, "tudo de um trace" (`filter trace_id = ...` nos log groups
  do coletor e do processador).

### Logs

- `FormatadorJson` acrescenta `trace_id` (32 hex) e `span_id` (16 hex) quando há span válido, e
  `xray_trace_id` (`1-<8 hex>-<24 hex>`) para colar no console do X-Ray.
- Log novo `requisicao_http` por tentativa (fonte, status, duração, tentativa, sem URL com query nem corpo).
- A linha `resultado: ...` volta ao fim de `atualizar`, `carga-inicial` e `processar` (com ou sem falhas),
  com teste dizendo que o filtro do Terraform depende dela.

### Lambdas

- Envoltório `com_telemetria(nome_servico)` (decorador em `radar.comum.telemetria`) para handlers Lambda:
  configura telemetria e métricas a partir das variáveis na primeira chamada; no `finally`, `descarregar`.
  Usado pela `radar-processador` e, na 4b, pelos handlers novos (planejador e coletora).
- Inicialização preguiçosa (na primeira chamada), como o engine. Importar o módulo do handler não importa
  o exportador.
- `tracing_config` da Lambda fica `PassThrough` (padrão): sem segmentos do serviço Lambda; os spans são só
  os nossos.
- `RADAR_TELEMETRIA=xray`, `RADAR_METRICAS=emf`, `RADAR_TRACES_FRACAO=0.25` no Terraform.

### Fargate (CLI)

- `atualizar`, `carga-inicial` e `processar` rodam dentro de `telemetria_do_processo("radar-coletor")`
  (context manager: configura e, na saída, descarrega métricas e encerra o provider com limite de 5 s).
- `ExecucaoOk` no fim do `atualizar` (1 sem falhas, 0 com).
- Métricas descarregadas no fim de cada janela e de cada retrato (as funções de coleta chamam
  `metricas.descarregar()`; no modo `emf` é no-op), então um container morto perde no máximo uma janela.
- Variáveis no task definition: `RADAR_TELEMETRIA=xray`, `RADAR_METRICAS=cloudwatch`,
  `RADAR_TRACES_FRACAO=0.25`.

### Infra (`infra/prod/observabilidade.tf`)

- Roles do coletor e do processador: política gerenciada `AWSXrayWriteOnlyAccess`; coletor ganha
  `cloudwatch:PutMetricData` com condição `cloudwatch:namespace = Radar`.
- Log group `aws/spans` com retenção de 7 dias, criado pelo Terraform **antes** de ligar o Transaction
  Search (o X-Ray aceita o log group existente).
- Transaction Search é configuração da conta: ligado uma vez, à mão, pelo coordenador
  (`aws xray update-trace-segment-destination --destination CloudWatchLogs` e regra de indexação em 1%),
  com o comando registrado em comentário no `.tf` e na nota. `OTEL`/Application Signals não é ligado.
- Os 2 alarmes novos no tópico `radar-alertas` (e a política do tópico atualizada com os ARNs), o dashboard
  e as consultas salvas.
- Variáveis de ambiente novas no task definition do coletor e na `radar-processador`.
- `.github/workflows/*` não mudam. Depois do `terraform apply` (que cria uma revisão nova do task
  definition), o coordenador roda o deploy de novo ou usa `-var tag_coletor=<sha>`, como o comentário do
  deploy já pede.

### Imagem e cold start

- Dependências novas: `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`
  (traz `requests` e `protobuf`). Nada de layer; os Dockerfiles não mudam.
- Medir **antes** (main) e **depois** (branch): tamanho das imagens `radar-api` e `radar-coletor`
  (`docker image inspect`); tempo de import do handler do processador dentro da imagem
  (`python -X importtime`); `Init Duration` da `radar-processador` e da `radar-api` em produção (média e
  máximo das linhas `REPORT` de 7 dias antes e de pelo menos 10 cold starts depois).
- Aceite: imagem + ≤ 40 MB; `Init Duration` do processador + ≤ 300 ms; `radar-api` sem piora acima de
  50 ms (ela não importa o OTel). Acima disso, a nota registra e a etapa não fecha sem decisão do Augusto.

## Testes (o que provar)

- Sem rede: `InMemorySpanExporter` e um emissor de métricas em memória, por fixture que troca o provider do
  módulo; o exportador SigV4 é testado assinando uma requisição preparada, sem enviar.
- Propagação: a página gravada leva o `traceparent` do span `coleta.janela` para a outbox; o relay leva-o
  para o atributo SQS; o consumidor cria `processar mensagem` em **trace diferente** com link para o
  `span_id` original. Mensagem sem atributo (em voo no deploy) processa normalmente, sem link.
- Amostragem: link amostrado força amostragem; sem pai e sem link, a fração vale (fração 0 → nada).
- Nenhum `traceparent` nas requisições às fontes (cliente `httpx` falso confere os cabeçalhos).
- Telemetria quebrada não muda nada: exportador que levanta exceção e PutMetricData que falha deixam
  `atualizar` com o mesmo código de saída e os mesmos editais.
- Métricas: catálogo recusa métrica ou dimensão desconhecida; EMF gerado é válido (estrutura `_aws`);
  agregação do PutMetricData junta valores repetidos em `Values`/`Counts`.
- Frescor: as definições acima, incluindo retrato concluído sem `processado_em` (não conta) e janela com
  página não processada (não conta).
- Logs: `trace_id`/`span_id` aparecem dentro de um span e não aparecem fora.
- Infra estática: ≤ 10 alarmes, ≤ 10 métricas, um dashboard, `aws/spans` com retenção.
- Rodada real: um trace completo coleta → outbox → SQS → Lambda visível no X-Ray; métricas no CloudWatch
  (inclusive a extraída do EMF); um alarme novo disparado de propósito; números de custo depois de 7 dias.

## O que fica com quem constrói

- Nomes internos, divisão em módulos dentro de `radar.comum`, mensagens de log além das citadas.
- Como a fixture de telemetria em memória é montada (desde que não use `trace.set_tracer_provider`).
- Layout exato do dashboard.
