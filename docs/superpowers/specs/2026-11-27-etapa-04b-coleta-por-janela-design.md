# Etapa 4b: coleta do PNCP por janela, em Lambda, com continuação

Data: 2026-11-27. Decisões do Claude com autonomia dada pelo Augusto; revisar. Tema 2 da trilha (eventos e
mensageria na AWS), segunda metade. Base: a decisão "Etapa 4b" em
`~/carreira/projetos/radar-decisoes-madrugada.md` e a pesquisa em `~/carreira/projetos/tema2-sqs-lambda.md`.

## Por que

A 4a deixou a fronteira coleta → editais assíncrona (outbox, SQS, consumidor idempotente). A coleta do PNCP
continua sendo um laço único num container Fargate: `radar atualizar` percorre todas as janelas elegíveis
em sequência, sob um advisory lock global (`CHAVE_COLETOR`). Medido: a atualização leva **~4 h** (pregão
tem ~80 páginas por dia a ~15 s por página). Uma janela de um dia leva ~20 min e **não cabe** numa Lambda
de 15 min.

A etapa existe para o Augusto entender e defender, em entrevista e em vídeo, **trabalho longo quebrado em
fatias com checkpoint, mensagem de continuação, lease com fencing token, concorrência limitada por cortesia
com a fonte e DLQ por tipo de trabalho**. Para o usuário final (o tio), nada muda, exceto que as páginas
viram editais durante a coleta, e não só depois de 4 h.

O que já existe e vale aproveitar (`src/radar/coleta/janelas.py`):

- **Checkpoint por página**: `_gravar_pagina` faz upsert em `pagina_bruta` e insere `outbox(pagina_coletada)`
  num commit por página. A página sobrevive a qualquer falha posterior.
- **Estado da janela** em `coleta_janela` (`pendente | em_andamento | concluida | falha`, `tentativas`,
  `erro`), com a invariante "falha nunca vira concluída com zero registros".
- **Checagens de completude**: `total_paginas` da página 1 tem de se repetir nas seguintes; no fim, os
  `numeroControlePNCP` distintos vistos têm de alcançar `totalRegistros`. Hoje ambos vivem **em memória**
  (`total`, `vistos`) e se perdem entre execuções.
- **Divisão**: janela de mais de um dia cujo primeiro pedido falha, sem nenhuma página, vira duas metades
  (`_dividir`). As janelas do `atualizar` são todas de um dia, então a divisão quase nunca roda; ela
  continua existindo e precisa continuar correta.

Achado ao ler o código para esta etapa: a 4a removeu a linha `resultado: ...` que a cli imprimia no fim
(commit `063d57a`), e o alarme `radar-coletor-nao-rodou` (`infra/prod/coletor.tf`) conta exatamente essa
linha. Sem ela, o alarme dispara ~30 h depois do primeiro deploy, com o coletor rodando bem. A 4b devolve
a linha (o Fargate continua existindo para os retratos).

## Objetivos

1. Cada janela do PNCP vira trabalho de fila: uma mensagem `coletar_janela` na fila `radar-janelas`.
2. A Lambda `radar-coletora` processa uma janela por invocação **até um orçamento de tempo**, grava o
   checkpoint (próxima página) a cada página e, se não terminou, enfileira a continuação na mesma transação
   que solta a janela.
3. Mensagem duplicada, atrasada ou reentregue **nunca** coleta a mesma janela em paralelo nem regride o
   checkpoint.
4. A Lambda `radar-planejador`, disparada pelo EventBridge Scheduler às 06:00 de Brasília, planeja as
   janelas do recuo e enfileira as elegíveis.
5. `MaximumConcurrency = 2` na coleta: no máximo duas janelas em andamento contra o PNCP.
6. Falha que esgota as tentativas vai para a DLQ `radar-janelas-falhas` e dispara alarme.
7. O Fargate passa a coletar **só os retratos** (Sanepar ~14 min, Compesa ~40 s).
8. Local e testes continuam sem AWS: `uv run radar atualizar` no notebook coleta o PNCP no próprio processo.
9. Fecha com `docs/etapas/04b-coleta-por-janela.md` (números, lições, 5 perguntas de entrevista) e
   `docs/videos/04b-coleta-por-janela.md`.

## Não objetivos

- Uma mensagem por página do PNCP (fan-out por página): alternativa C abaixo, descartada.
- Step Functions, EventBridge Pipes, FIFO, Kafka.
- Mover os retratos para Lambda (Sanepar passa de 14 min com pausa de 3 s; fica no Fargate).
- Mudar API, front, filtro, o consumidor de `pagina_coletada`/`retrato_coletado` ou o modelo de `edital`.
- Observabilidade nova além dos alarmes desta etapa (é o Tema 3).

## Antiobjetivos (falharia mesmo funcionando)

- **Duas invocações coletando a mesma janela ao mesmo tempo** (dobra a carga no PNCP e corre o checkpoint).
- Janela marcada `concluida` sem a checagem de completude, porque `total` e `vistos` foram perdidos entre
  fatias.
- Janela parada em silêncio: `em_andamento` para sempre, sem mensagem viva, sem alarme e sem aparecer no
  `radar auditar`.
- Mais de 2 invocações simultâneas contra o PNCP, ou retentativa em laço curto contra um PNCP instável.
- Advisory lock de sessão através do pooler do Neon (o modo transação não o garante).
- Custo fora do plano grátis, recurso fora de us-east-2, credencial fixa.

## Restrições

- AWS: projeto da nova experiência, plano FREE, **us-east-2** só, SCPs gerenciadas. SQS, Lambda (com event
  source mapping), EventBridge Scheduler e CloudWatch estão na lista do plano grátis.
- **Teto da Lambda: 900 s (15 min)**, mesmo com a documentação atual permitindo 1.800 s: o desenho não
  depende do limite maior. Orçamento por invocação **540 s** (~9-10 min) para *começar* páginas novas.
  Conta do pior caso: uma página começada aos 540 s, com o cliente PNCP da Lambda em 3 tentativas de 90 s
  mais as esperas (~5 s e ~10 s), termina por volta de 830 s, e sobram ~70 s para commit e relay.
- Free tier da Lambda: 400.000 GB-s/mês. 4 h/dia × 256 MB ≈ 3.600 GB-s/dia ≈ 108.000/mês (~27%). Com 512 MB
  seria ~54%. Memória da coletora: 256 MB, revista na rodada real.
- Neon: endpoint com pooler e `NullPool`; nenhum advisory lock de sessão no caminho da Lambda.
- Cortesia com o PNCP: `MaximumConcurrency = 2` (o mínimo que o SQS aceita); nova tentativa só depois do
  visibility timeout.
- Tudo com Terraform em `infra/prod`; a mesma imagem `radar-api` para as Lambdas novas, com outro
  `image_config.command`.
- Arquivos guardados (`frontend/scripts/contrato-guardado.mjs`, `GUARDADOS`) exigem o trailer
  `Contrato-Front: aprovado`.
- **Autorização do Augusto (estendida da 4a):** "atualizar funções Lambda novas no deploy". Nesta etapa o
  agente pode usar o trailer `Contrato-Front: aprovado` **só** no commit que muda `.github/workflows/deploy.yml`
  para atualizar também `radar-coletora` e `radar-planejador` com a imagem nova. Nenhum outro arquivo
  guardado é tocado.
- Merge na main só por PR, com os checks obrigatórios.
- Pré-requisito: a rodada real da 4a feita (filas `radar-editais` no ar).

## Abordagens consideradas

Decidido: **A**.

- **A. Uma mensagem por janela, fatias com orçamento de tempo e mensagem de continuação.** Cada invocação
  coleta páginas até o orçamento, grava o checkpoint por página e, se faltar página, enfileira
  "continuar janela" pela outbox. Reaproveita o checkpoint por página e o estado da janela que já existem.
  Escolhida.
- B. Step Functions com laço (Map ou Choice + Wait) carregando a página atual. Mostra o fluxo num console,
  mas troca a fila por outro serviço, gasta transições por página e esconde o mecanismo que a etapa quer
  ensinar.
- C. Uma mensagem por página. Paraleliza mais do que a cortesia permite, exige saber `total_paginas` antes
  de enfileirar e espalha a checagem de completude por dezenas de mensagens (barreira, como na alternativa
  B da 4a).
- D. Lambda com timeout de 30 min e uma invocação por janela. Ainda não cabe no pior caso (um dia de pregão
  com PNCP lento), prende o desenho a um limite e não ensina continuação.
- E. Deixar o PNCP no Fargate. Funciona; não ensina nada novo e mantém um laço de 4 h sem paralelismo.

## Desenho

### Dados (migração `0006_janela_fatiada`)

- `coleta_janela` ganha:
  - `proxima_pagina int not null default 1`: o checkpoint, avançado no mesmo commit de cada página.
  - `total_paginas int null` e `esperados int null`: o `totalPaginas` e o `totalRegistros` da página 1,
    persistidos para valer entre fatias.
  - `lote int not null default 0`: o número da fatia, que serve de **fencing token** da mensagem.
  - `travada_por text null` e `travada_ate timestamptz null`: o **lease** da invocação que está coletando.
- `outbox.tipo` aceita `coletar_janela`; carga `{"janela_id": n, "lote": n}`.
- Preenchimento: janelas `em_andamento` existentes voltam a `pendente` (eram sobra de execução interrompida).
- `em_andamento` passa a significar "coleta começada e não terminada", inclusive entre fatias. Quem está
  coletando agora é dito pelo lease, não pelo status.

### Lease e fencing (o centro da etapa)

- **Reivindicar** é um `UPDATE ... RETURNING` atômico:
  `SET status='em_andamento', travada_por=:token, travada_ate=now() + 960 s, tentativas = tentativas + 1`
  `WHERE id=:id AND lote=:lote AND status IN ('pendente','falha','em_andamento') AND (travada_ate IS NULL OR travada_ate < now())`.
  O relógio é o do banco (`now()`), não o de cada Lambda. 960 s = timeout da Lambda + 60 s de folga.
  `iniciada_em` só é carimbada quando `proxima_pagina = 1`.
- Se o `UPDATE` não pega linha, o motivo decide o desfecho, e **todos confirmam a mensagem** sem chamar o
  PNCP: janela inexistente (`inexistente`), já concluída (`concluida`), `lote` diferente (`obsoleta`:
  mensagem velha, a janela já avançou ou foi reaberta) ou lease vivo (`ocupada`: duplicata concorrente).
- Confirmar a `ocupada` é seguro porque quem segura o lease tem a própria mensagem em voo, ainda não
  apagada. Se essa invocação morrer, a mensagem dela reaparece depois do visibility timeout (5.400 s), quando
  o lease (960 s) já venceu, e retoma do checkpoint.
- **Fencing a cada página**: o commit de cada página atualiza `proxima_pagina = n + 1`
  `WHERE id=:id AND lote=:lote AND travada_por=:token`. Zero linhas quer dizer lease perdido (outra
  invocação assumiu depois do vencimento): rollback da página, para de coletar e confirma a mensagem
  (`lease_perdida`).
- O `lote` só muda em dois lugares: no fim de uma fatia que não terminou (`lote + 1`, junto com a
  continuação) e quando o planejador reabre uma janela concluída cedo demais (`lote + 1`). Reivindicar não
  muda o `lote`. Por isso a mensagem de uma invocação que morreu continua válida.

### Fatia (`coletar_fatia`)

1. Reivindica (acima).
2. Coleta a partir de `proxima_pagina`. Na página 1 grava `total_paginas` e `esperados`; nas seguintes,
   `total_paginas` diferente do gravado é falha `inconsistente`. Cada página: upsert em `pagina_bruta` +
   `outbox(pagina_coletada)` + `proxima_pagina` com fencing, num commit. `total_paginas = 0` é dia sem
   registros, como hoje.
3. Antes de começar cada página, confere o orçamento (relógio monotônico). Sem orçamento (modo laço da cli),
   vai até o fim.
4. **Terminou** (`proxima_pagina > total_paginas`): conta no banco os `numeroControlePNCP` distintos das
   páginas da janela (`jsonb_array_elements` sobre `conteudo->'data'`, só `numero_pagina <= total_paginas`)
   e compara com `esperados`. Abaixo: falha `incompleta`. Senão `concluida`, com `paginas`, `registros` e
   `concluida_em` calculados no banco; lease solto. Uma transação.
5. **Não terminou** (orçamento acabou): `lote = lote + 1`, lease solto e
   `outbox(coletar_janela, {"janela_id", "lote": lote + 1})`, **na mesma transação**. Nunca existe janela
   a meio caminho sem mensagem que a continue.
6. **Falhas**, todas soltando o lease:
   - `ErroPNCP` numa página (já depois das retentativas do cliente): `status = falha`, `erro`, checkpoint
     **mantido**; o desfecho `falha` devolve a mensagem para nova tentativa.
   - `inconsistente` ou `incompleta`: `status = falha`, `erro`, checkpoint volta a 1 e `total_paginas`/
     `esperados` a NULL (o PNCP mudou no meio; recoleta inteira); desfecho `falha`.
   - Divisão (janela de mais de um dia, erro na página 1, nenhuma página gravada): troca pelas duas metades
     como hoje e, no modo mensagem, enfileira `coletar_janela` das duas metades na mesma transação; desfecho
     `dividida`, mensagem confirmada.
   - Exceção inesperada: rollback, `status = falha`, `erro`, lease solto; propaga (nova tentativa).

O modo é dado pelo `lote`: com `lote` (Lambda), a fatia confere o fencing token e enfileira continuação e
metades. Sem `lote` (cli local), reivindica qualquer janela elegível e vai até o fim, sem enfileirar nada.

### Planejador

- `planejar_e_enfileirar(sessao, criterios, hoje, recuo)`: `planejar_janelas("atualizacao", hoje - recuo,
  hoje, modalidades)` (sem mudança) e `enfileirar_elegiveis("atualizacao")`, que numa transação:
  - **reabre** as janelas de atualização concluídas antes de o dia delas acabar em Brasília (a regra de
    `_proxima_janela`), com lease livre: `pendente`, `proxima_pagina = 1`, `total_paginas`/`esperados` NULL,
    `lote + 1`;
  - enfileira `coletar_janela` com o `lote` atual para cada janela `pendente`, `falha` ou `em_andamento`
    com lease livre.
- Depois o relay publica tudo. Uma mensagem a mais para uma janela que já tem continuação na fila é
  inofensiva: uma delas sai `ocupada` ou `obsoleta`.
- Recuo padrão 10 dias, o mesmo do `atualizar`. Lambda `radar-planejador` às 06:00 (`America/Sao_Paulo`),
  timeout 120 s, 256 MB. Comando `radar planejar` faz o mesmo à mão.

### Relay

- `publicar_outbox` não muda. A fila passa a ser **roteada por tipo**: `FilaRoteada` implementa `Fila` e
  manda `coletar_janela` para `radar-janelas` e o resto para `radar-editais`. Todo processo que roda o relay
  (planejador, coletora, Fargate, cli) recebe as duas URLs e pode enviar para as duas filas.
- Na coletora, o relay roda **no começo** (recupera o que uma invocação anterior não publicou) e **no fim**
  de cada invocação (publica as páginas desta fatia e a continuação). Com isso as páginas viram editais
  durante a coleta.
- Falha no relay do fim: a mensagem volta (falha de item). A próxima entrega publica no começo, encontra o
  `lote` já avançado e sai `obsoleta`.

### Lambda coletora

- `radar.processador.lambda_coletora.handler`, mesma imagem da API, timeout 900 s, 256 MB, fora de VPC,
  `DATABASE_URL` do SSM. Token do lease = `contexto.aws_request_id`. Cliente PNCP com 3 tentativas.
- Event source mapping em `radar-janelas`: `batch_size = 1`, `ReportBatchItemFailures`,
  `maximum_concurrency = 2`.
- Desfechos: `concluida`, `continua`, `dividida`, `ocupada`, `obsoleta`, `inexistente` e `lease_perdida`
  confirmam a mensagem; `falha`, corpo inválido e exceção voltam como falha de item.
- Fila `radar-janelas`: visibility timeout 6 × 900 = **5.400 s** (é também a espera entre tentativas:
  cortesia com um PNCP instável), `maxReceiveCount = 5`, retenção 4 dias. DLQ `radar-janelas-falhas`,
  retenção 14 dias, `redrive_allow_policy` só da `radar-janelas`. Cada continuação é uma mensagem nova, então
  as 5 tentativas valem por fatia, não pela janela inteira.

### Fargate

- O container do coletor passa a rodar `atualizar --fontes sanepar,compesa` (campo `command` da task
  definition no Terraform; o deploy registra revisões a partir da última e o preserva). Continua às 06:00.
- Ganha `RADAR_FILA_JANELAS_URL` e `sqs:SendMessage` em `radar-janelas` (o relay dele pode encontrar uma
  continuação pendente).
- `atualizar`, `processar` e `carga-inicial` voltam a imprimir `resultado: sem falhas` ou
  `resultado: N falhas` no fim (o alarme `radar-coletor-nao-rodou` depende disso).

### Local

- `RADAR_FILA=local`: `atualizar` coleta o PNCP no próprio processo, chamando `coletar_fatia` sem `lote` e
  sem orçamento para cada janela elegível. `coletar_pendentes` deixa de usar `CHAVE_COLETOR` e o reset geral
  de `em_andamento` (o lease vencido já cobre a sobra de execução interrompida e não atropela uma Lambda
  viva).
- `FilaLocal` ganha um despachante opcional para `coletar_janela`. Com ele, `radar planejar` local roda a
  cadeia inteira de fatias no processo (útil para testar a cadeia sem AWS); sem ele, `coletar_janela` é
  recusada e fica pendente na outbox.

### Alarmes (SNS `radar-alertas`; o total no projeto fica em 7 de 10 grátis)

- DLQ `radar-janelas-falhas` com mensagens visíveis > 0.
- `ApproximateAgeOfOldestMessage` de `radar-janelas` > 21.600 s (6 h; a coleta inteira leva ~2 h com
  duas em paralelo, e uma nova tentativa espera 1,5 h).
- `radar-pncp-nao-concluiu`: filtro de métrica do log `janela_concluida` da coletora, Sum < 1 em 5 períodos
  de 6 h, dado ausente conta como violação (mesmo padrão do `coletor_nao_rodou`). Cobre planejador que não
  rodou, permissão quebrada e cadeia parada. Todo dia há ao menos a janela de hoje a concluir.

### Recuperar a DLQ

- `radar reprocessar-falhas --fila janelas` move `radar-janelas-falhas` → `radar-janelas` (mesmo
  `StartMessageMoveTask`); o padrão continua `editais`. A mensagem devolvida ainda traz o `lote` dela. Se o
  planejador já reabriu ou avançou a janela, ela sai `obsoleta`, o que está certo: a janela já tem cadeia nova.
- Variável nova `RADAR_FILA_JANELAS_FALHAS_URL`.

### Auditoria

- A cobertura (`auditar`) não muda: dia × modalidade conta como coberto só com janela `concluida`.
- Nova linha "janelas em andamento há mais de 6 h": `em_andamento` com `iniciada_em` anterior ao corte,
  mostrando data, modalidade, `proxima_pagina` e `total_paginas`; qualquer uma faz `auditar` sair com 1.
- A linha de falhas passa a mostrar `proxima_pagina/total_paginas` (onde a janela parou).
- `tentativas` passa a contar fatias reivindicadas, não execuções do `atualizar`.

## Testes (o que provar)

- Lease: duas sessões reivindicando a mesma janela e o mesmo `lote`, só uma ganha; lease vencido é
  retomado; `lote` diferente é `obsoleta`; janela concluída é `concluida`; nenhum desses chama o PNCP.
- Fencing: outra invocação assume depois do vencimento; a página seguinte da primeira não grava, e o
  checkpoint não regride.
- Checkpoint: orçamento que corta depois de 2 páginas → janela com `proxima_pagina = 3`, `lote + 1`, uma
  `coletar_janela` na outbox, lease solto; a continuação começa na página 3 sem pedir as anteriores.
- Completude entre fatias: janela coletada em 3 fatias, com registro repetido entre páginas, conclui só se
  os distintos alcançam `esperados`; `total_paginas` diferente numa fatia posterior falha e zera o checkpoint.
- Divisão no modo mensagem enfileira as duas metades na mesma transação.
- Equivalência: a cadeia com orçamento mínimo (uma página por fatia) dá os mesmos editais e contadores que o
  laço local sem orçamento.
- Handler: desfechos que confirmam e os que voltam; relay do fim falhando devolve a mensagem.
- Infra estática: valores da fila, ESM, Lambdas, agenda e alarmes; total de alarmes ≤ 10.
- Rodada real: uma atualização completa por Lambda com números (fatias por janela, duração, GB-s, memória
  máxima); demonstração gravável de uma duplicata `ocupada`, de uma Lambda morta no meio retomando do
  checkpoint e de uma janela envenenada indo para a DLQ e disparando o alarme.

## O que fica com quem constrói

- Divisão de `coleta/janelas.py` (novo módulo das fatias ou não), mensagens de log além de `janela_concluida`
  e os desfechos, nomes de funções privadas.
- Como simular o relógio monotônico e o `now()` do banco nos testes.
- Texto exato das linhas novas do `auditar`.
- Se o planejador e a coletora compartilham o módulo de bootstrap (sessão, SSM, logs) com o processador.
