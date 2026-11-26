# Etapa 4a: outbox e SQS entre a coleta e os editais

Data: 2026-11-26. Aprovado pelo Augusto em 2026-11-26. Tema 2 da trilha (eventos e mensageria na AWS).

## Por que

O radar é um projeto de estudo público; a etapa existe para que o Augusto entenda e defenda, em entrevista
e em vídeo, **entrega pelo menos uma vez, outbox transacional, nova tentativa, fila de mensagens com
falha e consumidor idempotente**. "Eventos e AWS" é uma das lacunas que aparecem nas vagas. Utilidade para
o usuário final (o tio) não muda: ele continua vendo os mesmos editais, no máximo alguns minutos depois.

Hoje a fronteira coleta → editais é síncrona, dentro do mesmo processo:

- **PNCP** (`src/radar/cli.py:_processar`): a coleta grava cada página em `pagina_bruta`; depois um laço lê
  as páginas não processadas, chama `registrar` e `marcar_processada` (commit por página). O comentário
  "é aqui que a fila do Tema 2 entra" marca o ponto.
- **Retratos** (`cli.py:_rodar_retrato`, `coleta/retratos.py:_executar`): o retrato baixa todas as páginas,
  normaliza **em memória** (`fonte.extrair`), marca as páginas como processadas e o retrato como
  `concluido`; em seguida, sob a mesma trava da fonte, a cli grava os editais (`registrar_normalizados`),
  checa a fração de inválidos e reconcilia (`reconciliar_retrato`) numa transação só, e limpa brutos.

## Objetivos

1. A coleta (PNCP e retratos) **só coleta**: grava páginas e, na mesma transação, uma mensagem na
   tabela `outbox`. Nunca existe página sem mensagem nem mensagem sem página.
2. Um **relay** publica a outbox no **SQS**.
3. Um **consumidor** (Lambda) processa cada mensagem de forma **idempotente**:
   - `pagina_coletada` (PNCP): registra os editais da página.
   - `retrato_coletado` (Sanepar, Compesa): relê as páginas brutas do retrato, normaliza, grava e reconcilia
     numa transação, com as mesmas travas de hoje.
4. Falhas repetidas vão para uma **fila de mensagens com falha** e disparam alarme.
5. Local e testes funcionam sem AWS.
6. A etapa fecha com a nota `docs/etapas/04a-filas.md` (números, lições, 5 perguntas de entrevista) e o
   roteiro `docs/videos/04a-filas.md`, como as anteriores.

## Não objetivos

- **Coleta do PNCP por janela em Lambda** (uma mensagem por janela, `MaximumConcurrency` para cortesia):
  é a **etapa 4b**, depois desta provada.
- **Barreira por página nos retratos** (uma mensagem por página + contagem + `retrato_completo`): descartada
  nesta etapa; fica como exercício opcional no fim (alternativa B abaixo).
- pgmq (indisponível no Neon: `pg_available_extensions` só tem `pg_cron` e `pg_partman`), FIFO, Step
  Functions, EventBridge Pipes, Kafka.
- Mudar API, front, filtro ou o modelo de `edital`.

## Antiobjetivos (falharia mesmo funcionando)

- **Reconciliar com dado parcial**: encerrar editais de uma fonte a partir de um retrato incompleto,
  falho ou **mais velho** que outro já processado.
- Mensagem perdida em silêncio (página coletada que nunca vira edital, sem alarme nem rastro na auditoria).
- Processo que fica fazendo polling no Neon (o compute do plano grátis suspende após 5 min parado; polling
  contínuo gastaria ~180 CU-h/mês contra 100 grátis).
- Credencial fixa, recurso fora de us-east-2, custo fora do plano grátis.

## Restrições

- AWS: projeto da nova experiência, plano FREE, **us-east-2**, SCPs gerenciadas. SQS, Lambda com event
  source mapping e DLQ estão na lista do plano grátis (pesquisa em `~/carreira/projetos/tema2-sqs-lambda.md`).
- Neon: usar o endpoint com pooler e `NullPool` (já é o padrão de `comum/db.py`).
- A pausa de 3 s entre requisições das fontes próprias continua; a coleta de um retrato é sequencial.
- Arquivos guardados pelo contrato do front e workflows exigem trailer `Contrato-Front: aprovado`.
- Merge na main só por PR, com os checks obrigatórios.
- **Autorização do Augusto (2026-11-26):** nesta etapa o agente pode usar o trailer `Contrato-Front: aprovado`
  só nos commits que mudam `.github/workflows/deploy.yml` (atualizar também a Lambda `radar-processador`) e
  `.github/CODEOWNERS` (comentário que ainda cita o job `contrato-guardado`, removido). Nenhum outro arquivo guardado.

## Abordagens consideradas

Para a fila no geral (decidido pelo Augusto: **C**):

- A. Fila feita à mão no Postgres (`FOR UPDATE SKIP LOCKED`). Mostra o mecanismo inteiro; mais código.
- B. pgmq instalado como SQL puro. Mais rápido; incerto no Neon; esconde o mecanismo.
- **C. Outbox + SQS direto.** Escolhida.

Para os retratos (o Augusto recusou deixá-los síncronos; decidido: **A**):

- **A. A mensagem é o retrato inteiro** (`retrato_coletado`), emitida na transação que conclui o retrato.
  O consumidor relê as páginas brutas e faz gravação + reconciliação numa transação. Mesmo padrão do PNCP
  com outra unidade de trabalho; pouco código novo. Escolhida.
- B. Uma mensagem por página com barreira (contador de páginas, última dispara `retrato_completo`). Padrão
  fan-out/fan-in, bom para entrevista, mas com contador atômico, duplicata contando duas vezes e página que
  trava a barreira, para no máximo ~100 páginas baixadas em sequência. Fica como exercício opcional.

## Desenho

### Dados (migração `0005_outbox`)

- Tabela `outbox`: `id bigserial`, `tipo text` (CHECK `pagina_coletada | retrato_coletado`),
  `carga jsonb` (`{"pagina_id": n}` ou `{"retrato_id": n}`), `criada_em timestamptz`,
  `publicada_em timestamptz null`, `tentativas int default 0`, `erro text null`.
  Índice parcial em `(id) WHERE publicada_em IS NULL`.
- `coleta_retrato`: novo `processado_em timestamptz null`. O status `concluido` passa a significar
  "coletado e validado" (mínimo de ids, Abertura vazia etc. continuam na coleta); `processado_em` diz que os
  editais foram gravados e reconciliados.
- Retratos deixam de marcar `pagina_bruta.processada_em` na coleta; quem marca é o consumidor.

### Produtor (coleta)

- PNCP (`coleta/janelas.py:_gravar_pagina`): ao inserir a página, insere `outbox(pagina_coletada)` na mesma
  sessão, antes do commit que já existe.
- Retratos (`coleta/retratos.py:_executar`): no commit que marca o retrato `concluido`, insere
  `outbox(retrato_coletado)`. Retrato em falha não gera mensagem.
- `executar_retrato` deixa de devolver `normalizados` para a cli gravar; `_rodar_retrato` só coleta (a parte
  de registro/reconciliação muda para o consumidor). Mantém a trava da fonte durante a coleta.

### Relay

- Função `publicar_outbox(sessao, fila)`: em lotes, `SELECT ... WHERE publicada_em IS NULL ORDER BY id
  LIMIT 10 FOR UPDATE SKIP LOCKED`, `SendMessageBatch`, marca `publicada_em` nas entradas aceitas, incrementa
  `tentativas` e grava `erro` nas recusadas, commit por lote. Repete até esvaziar.
- Onde roda: **no fim do `radar atualizar`** (Fargate) e **no começo** do próximo `atualizar` (recupera o que
  ficou se o container morreu entre coletar e publicar). Comando `radar publicar` para rodar à mão.
  Pior caso de atraso aceito: até a próxima coleta diária. Sem relay agendado à parte (manteria o Neon acordado).
- Duplicata é esperada: se o SQS aceitou e o commit falhou, a mensagem sai de novo. O consumidor absorve.
- Retenção: `radar publicar` apaga linhas publicadas há mais de 7 dias.

### Fila

- Interface `Fila` com `enviar_lote(mensagens) -> aceitas/recusadas`.
  - `FilaSQS` (boto3) quando `RADAR_FILA=sqs` e `RADAR_FILA_URL` definidos.
  - `FilaLocal` (padrão): chama o consumidor na hora, no mesmo processo. Mantém `uv run radar atualizar`
    funcionando no notebook e nos testes sem AWS.
- SQS standard `radar-editais`, DLQ `radar-editais-falhas` (retenção 14 dias), redrive com
  `maxReceiveCount = 5`, visibility timeout = 6 × timeout do consumidor.
- Corpo da mensagem: JSON `{"tipo": ..., "carga": {...}, "outbox_id": n}`.

### Consumidor

- `radar.editais.consumidor.processar_mensagem(sessao, mensagem)`:
  - `pagina_coletada`: se a página já tem `processada_em`, confirma e sai. Senão `registrar` +
    `marcar_processada` numa transação (o upsert newest-wins já é idempotente).
  - `retrato_coletado`, sob `trava_fonte` da fonte do retrato:
    - se a trava não sai (coleta da mesma fonte rodando), falha a mensagem para nova tentativa;
    - se `processado_em` já existe, confirma e sai;
    - **se já existe retrato da mesma fonte mais novo com `processado_em`, marca este como processado sem
      gravar nem reconciliar** (log `retrato_obsoleto`): reconciliar com um retrato velho reabriria ou
      encerraria editais errado;
    - senão relê as páginas (`pagina_bruta` do retrato, em ordem de `numero_pagina`), reconstrói as
      `PaginaColetada`, chama `fonte.extrair`, e faz exatamente o que `_rodar_retrato` faz hoje
      (fração de inválidos → `marcar_retrato_falha`; reconciliação; `processado_em`; páginas marcadas),
      numa transação; depois `limpar_brutos`.
- Handler Lambda `radar.editais.lambda_consumidor.handler`: event source mapping com batch size 10,
  `ReportBatchItemFailures` (devolve só os ids que falharam), `MaximumConcurrency = 2` (protege o Neon).
  Uma sessão por mensagem.

### Infra (Terraform, `infra/prod/fila.tf`)

- As duas filas, a política de redrive, a Lambda `radar-processador` com a **mesma imagem da API** e
  `image_config.command` apontando para o handler do consumidor, timeout 120 s, memória 512 MB, fora de VPC,
  `DATABASE_URL` lida do SSM como a API faz.
- Permissões: task do coletor ganha `sqs:SendMessage` na fila; role do processador ganha
  Receive/Delete/GetQueueAttributes e leitura do parâmetro SSM; nada mais.
- Variáveis do container do coletor: `RADAR_FILA=sqs`, `RADAR_FILA_URL`.
- Alarmes no SNS existente: mensagens visíveis na DLQ > 0; `ApproximateAgeOfOldestMessage` > 3600 s.
- Deploy (`.github/workflows/deploy.yml`): atualizar também o código da `radar-processador` com a nova
  imagem; a role de deploy ganha `lambda:UpdateFunctionCode` nela.

### Falhas que não chegam à DLQ (decisão do Claude)

- O consumidor marca o retrato como falha (muitos inválidos, páginas ausentes) e confirma a mensagem, sem
  nova tentativa, porque repetir não muda o resultado. Para não virar falha silenciosa, cada falha dessas
  grava o log `processamento_falhou` e um **filtro de métrica** no log group do processador alimenta um
  alarme (> 0 em 1 dia) no SNS existente. É o substituto da regra atual de código de saída do Fargate.
- Com `FilaLocal`, falha do consumidor faz `atualizar` sair com código diferente de 0, como hoje.

### Recuperar a DLQ (decisão do Claude)

- Comando `radar reprocessar-falhas`: chama `StartMessageMoveTask` da DLQ de volta para a fila principal
  (redrive nativo do SQS) e mostra quantas mensagens voltaram. É o passo da demonstração no vídeo, depois
  de corrigir a causa.
- `radar processar` e `carga-inicial` passam pelo mesmo caminho: coleta → outbox → relay → fila. Com
  `FilaLocal` (padrão local), o efeito é o de hoje. Não há atalho síncrono separado.

### Retenção de brutos (decisão do Claude)

- `limpar_brutos` passa a contar só retratos com `processado_em` (os 3 mais novos processados) e nunca
  apaga páginas de retrato `concluido` sem `processado_em`.
- Consumidor que não acha as páginas do retrato: se já existe retrato mais novo processado da fonte,
  trata como `retrato_obsoleto`; senão marca falha (`processamento_falhou`, alarme acima).

### Auditoria

- `radar auditar` passa a mostrar: outbox não publicada (contagem e a mais velha), páginas do PNCP com
  mensagem publicada e ainda não processadas há mais de 1 h, retratos `concluido` sem `processado_em`.

## Testes (o que provar)

- Atomicidade: rollback da transação da coleta não deixa nem página nem linha na outbox.
- Relay: duas sessões concorrentes não publicam a mesma linha (SKIP LOCKED); entrada recusada pelo SQS fica
  pendente com `erro`; falha entre envio e commit gera duplicata, e o consumidor a absorve.
- Consumidor: mensagem repetida não duplica nem altera nada; retrato obsoleto não reconcilia; retrato com
  muitos inválidos vira falha sem gravar; trava ocupada devolve falha da mensagem.
- Equivalência: rodar `atualizar` com `FilaLocal` no banco local dá o mesmo resultado de editais que a
  versão síncrona de hoje (mesmos contadores e `filtrar` idêntico).
- Rodada real na AWS: uma coleta completa, com números na nota da etapa; demonstração gravável de uma
  mensagem envenenada indo para a DLQ e disparando o alarme.

## O que fica com quem constrói

- Biblioteca de SQS nos testes (moto, stub do botocore ou fake próprio).
- Nome exato dos módulos e a divisão em tarefas.
- Mensagens de log e contadores impressos pela cli.
- Como reconstruir `PaginaColetada` a partir de `pagina_bruta` (campos `formato`, `conteudo`,
  `conteudo_texto`, `url`).
