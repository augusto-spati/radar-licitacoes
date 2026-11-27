# Etapa 7: histórico de preços e vencedores

Data: 2026-11-27. Pedido do Augusto: "estudar mais portais e ir atrás do histórico de preços, vencedores"; caminho B escolhido (histórico estruturado + preço unitário extraído por IA); Sanasa fora por ora; "pode seguir autônomo". Decisões tomadas sem ele estão marcadas **[decisão do Claude]** e também em `~/carreira/projetos/radar-decisoes-madrugada.md`.

Pesquisas que embasam este desenho (privadas, fora do repositório): `~/carreira/projetos/radar-pesquisa-precos-decisoes.md` (o que o dono decide), `radar-pesquisa-precos-dados.md` (cobertura medida no PNCP e nos TCEs), `radar-pesquisa-portais.md` (portais fora do PNCP).

## Por que

O dono da empresa decide, nesta ordem de valor:

1. **Entrar ou não** numa licitação (montar proposta de obra custa dias de engenharia). Precisa ver o histórico do órgão: quantas deram em contrato, quantas fracassaram ou ficaram desertas, quem costuma vencer e com que desconto.
2. **Quanto de desconto oferecer.** O histórico dá uma faixa de referência (desconto do vencedor sobre o estimado, por linha de serviço, UF e faixa de valor), não o preço que ganha. A Lei 14.133 presume inexequível abaixo de 75% do orçado (70% nas estatais da Lei 13.303).
3. **Onde concentrar esforço**: órgãos dominados por um mesmo concorrente.
4. **Preço unitário** (R$/m de rede, R$/ligação, R$/hidrômetro, R$/km de pesquisa) é o número que ele mais entende, mas só existe em planilha (PDF/Excel), não em dado estruturado.

## Objetivos

- Histórico de resultados das contratações do recorte do radar (mesmo filtro: linhas de serviço, UFs SP/PR/PE/MG, modalidades 4–7) dos últimos 12 meses, e manutenção diária daí em diante.
- Na ficha do edital: histórico do órgão, régua de desconto da linha × UF e quem vence.
- Preço unitário por serviço normalizado, extraído com IA das planilhas (ver 7b; depende da medição de disponibilidade).

## Não objetivos

- Sócios de concorrentes, pessoa física, ranking geral de fornecedores (curiosidade, e a LGPD desaconselha).
- Lista de perdedores e propostas não vencedoras (o PNCP não publica; AUDESP tem, com 8+ meses de atraso: fica para depois).
- Contratos, aditivos e execução (cobertura de contrato no PNCP é 38%; aditivos ainda não existem nos contratos recentes). **[decisão do Claude]** fora da v1.
- Novos portais além do DAE Jundiaí (Sanasa fora por ora; Copasa/Sabesp/privadas inviáveis; Cesama médio esforço — depois).

## Anti-objetivos

- Mostrar desconto como "preço que ganha" ou recomendar lance. A tela diz "faixa de referência" com o número de casos.
- Comparar valor global entre obras de escopo diferente sem dizer quantos casos e de quê.
- Encher o Neon grátis (0,5 GB): o histórico só grava o que casa com o filtro.
- Versionar dados de resultado no repositório público.

## Restrições

- Neon grátis 0,5 GB; hoje ~120 MB. Orçamento AWS US$ 5/mês, créditos US$ 140; teto da IA US$ 2/mês (o 7b pode pedir subir: decisão do Augusto).
- Cortesia com o PNCP: UA identificável, ≤1 req/s nas APIs de consulta (lentas, 15–65 s/página), sem paralelismo além do que a 4b já usa.
- Fronteiras do import-linter: coleta, editais e analise independentes; api/cli só importam a raiz dos pacotes.
- Contrato do front: componente novo e rota nova são mudança de contrato (`Contrato-Front: aprovado`). **[decisão do Claude]** o pedido do caminho B inclui as visões, então vale como pedido explícito; as visões ficam **dentro da ficha do edital** (sem rota nova).

## Abordagens consideradas

- **A. Só histórico estruturado + DAE Jundiaí.** Barato e confiável; sem preço unitário.
- **B. A + preço unitário por IA das planilhas.** Escolhida pelo Augusto: o número mais intuitivo para o dono; extração mais cara e frágil, precisa de gabarito.
- **C. Portais primeiro.** Adiaria a novidade pedida.

Para a carga do histórico:

- **Janelas da 4b (Lambda + `pagina_bruta`).** Reaproveita a infra, mas grava cada página bruta (~800 MB transitórios para 12 meses): estoura o Neon. Descartada.
- **Comando de carga no Fargate, em fluxo, gravando só o que casa.** **[decisão do Claude]** escolhida: uma task avulsa, retomável por checkpoint, ~33 h a 1 req/30 s, ~US$ 0,50.

## Desenho

### 7a.1 Carga histórica (`radar historico carregar`)

- Percorre `contratacoes/publicacao` do PNCP mês a mês, nos últimos N meses (padrão 12), por **UF** (parâmetro `uf`, confirmado: PE tem 189 concorrências em jun/2026 contra 6.257 no país) e por modalidade do filtro.
- Cada página é pré-filtrada em memória: modalidade e UF já vêm na consulta; o objeto precisa conter algum termo das linhas de `config/filtro.toml`, comparado sem acento e em minúsculas (superconjunto da regra SQL; o filtro de verdade continua no `filtrar`). Só o que passa vai para `registrar` (mesma tabela `edital`, mesma regra de versão mais nova).
- Checkpoint por (UF, modalidade, mês): tabela `historico_carga` com `proxima_pagina`, `total_paginas`, `status` (`pendente|em_andamento|concluida|falha`), `registros_gravados`, `erro`; commit por página. Rodar de novo retoma de onde parou; `--refazer-mes AAAA-MM` reabre.
- Roda como task avulsa do Fargate (mesma família do coletor), disparada à mão uma vez (e de novo se cair). Sem agendamento.
- Trava própria (`trava_fonte` com chave `historico`) para não rodar duas ao mesmo tempo.

### 7a.2 Coletor de resultados (`radar resultados atualizar`)

- Alvos: editais do PNCP que casam com o filtro (todas as situações) com prazo de proposta já passado e (a) sem resultado buscado, ou (b) último resultado buscado há mais de 7 dias, enquanto o encerramento tiver menos de 180 dias e ainda houver item sem situação final.
- Para cada alvo: `GET /pncp-api/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens` (paginado) e, para cada item com `temResultado`, `.../itens/{n}/resultados`. Rápido (0,1–0,35 s), ainda assim ≤1 req/s.
- Grava em `resultado_item` (uma linha por item, upsert por (fonte, id_na_fonte, numero_item)): descrição, unidade, quantidade, valor unitário e total estimados, `orcamento_sigiloso`, situação do item (Homologado, Fracassado, Deserto, Anulado/Revogado/Cancelado, Em andamento), e do resultado: CNPJ/razão social/porte do vencedor, quantidade e valores homologados, data do resultado. Vencedor pessoa física (CPF): grava sem nome, só "pessoa física". **[decisão do Claude]**
- `desconto` calculado (`1 − homologado ÷ estimado`, por item e no total da contratação); o `percentualDesconto` do PNCP é ignorado (vem 0 em 129 de 132 resultados).
- `resultado_busca` por edital: quando foi buscado, quantos itens, situação agregada (`homologada|fracassada|deserta|parcial|em_andamento|sem_itens`).
- Roda todo dia às 07:30 (agenda do Fargate, depois da coleta das 06:00). Carga histórica e resultados são comandos separados: a rodada diária de resultados pega também o que a carga histórica trouxe. Sem métrica nova: o resumo vai no log (`resultados: N editais, M itens, K com vencedor`), porque a 10ª métrica customizada do plano grátis fica para o alerta da auditoria (pendência 1). **[decisão do Claude]**

### 7a.3 TCE-PE para a Compesa (condicional)

- A Compesa não publica no PNCP; o TCE-PE tem API aberta com vencedor e valor homologado (15/15 na amostra). Pode aceitar só IP brasileiro: **primeiro teste de dentro da AWS**. Se bloquear, fica fora (registrado), sem contornar.
- Se funcionar: mapear processos da Compesa do TCE-PE para os editais `compesa~<lc_chave>` pelo número da licitação; gravar no mesmo `resultado_item` com `fonte='compesa'`.

### 7a.4 API

- `GET /api/historicos/{id}` (rota separada: o conversor `path` de `/api/editais/{id}` engoliria um sufixo `/historico`) → para o órgão do edital (CNPJ; Sanepar/Compesa pelo órgão fixo) e a linha de serviço do edital:
  - `orgao`: contratações passadas do órgão no recorte (até 24 meses): contagem por situação agregada; últimas 10 com objeto, data, valor estimado, valor homologado, desconto, vencedor (razão social, porte);
  - `vencedores`: top 5 vencedores no órgão com número de vitórias e desconto mediano;
  - `reguas` (lista, uma por linha do edital): para a linha × UF do edital (e faixa de valor do edital, se houver: até 1 mi, 1–5 mi, acima de 5 mi): n, mediana, p25, p75 do desconto global; se n < 5, devolve também a régua só por linha (todas as UFs) e marca `poucos_casos`.
  - `limite_inexequivel`: 0,75 (Lei 14.133) ou 0,70 (estatal 13.303: Sanepar, Compesa) do estimado, para a tela mostrar a linha de corte.
- Mesmo autorizador JWT; `openapi.json` e `api.d.ts` regenerados.

### 7a.5 Tela (ficha do edital)

Abaixo de "Aderência à FR", uma seção **"Histórico do órgão"**:

```
Histórico do órgão (últimos 24 meses, serviços de saneamento)
  12 licitações · 8 homologadas · 3 fracassadas/desertas · 1 em andamento
  Quem vence: Construtora X (3) · Y Engenharia (2) · ...
  Últimas: objeto · data · estimado · homologado · desconto · vencedor
Faixa de desconto dos vencedores (redes, SP, 1–5 mi): 18% a 31% (mediana 24%), 14 casos
  Limite de inexequibilidade: 25% de desconto (75% do estimado)
```

Componentes novos `HistoricoOrgao.tsx` (lista e contagens) e `ReguaDesconto.tsx` (faixa + limite), na lista fechada (trailer). Sem dado: "Sem histórico deste órgão no recorte".

### 7b Preço unitário por IA (desenho final depende da medição em curso)

- Fontes candidatas: (a) planilha orçamentária do órgão (anexo do edital; preço **estimado**), (b) planilha/proposta do vencedor (preço **proposto**), (c) TCE-PE orçamento por item (estimado, estruturado). A medição de disponibilidade e formato está em `radar-pesquisa-precos-dados.md`, seção "Preço unitário: de onde sai".
- Extração: o mesmo provedor (Nova Lite) com saída estruturada `linhas: [{descricao, unidade, quantidade, preco_unitario, trecho}]`, ancoragem no texto como na etapa 6, só para editais homologados das linhas `redes` e `perdas`.
- Normalização para poucos serviços-padrão (rede de água/esgoto por DN e material em R$/m, ligação predial em R$/un, hidrômetro em R$/un, pesquisa de vazamento em R$/km), por regra no código (como a comparação da etapa 6), com a unidade convertida.
- Evals: gabarito de 5 a 10 planilhas, rascunho do Claude marcado `conferido: false` (só o Augusto confere).
- Custo: estimar antes; se passar do teto de US$ 2/mês, para e pergunta.

### 7c Coletor DAE Jundiaí (fonte `dae_jundiai`)

- WordPress estático, sem bloqueio; editais e homologações em PDF aberto. Retrato como Sanepar (lista + detalhe), anexos guardados, situação normalizada; vencedor pela homologação em PDF fica para depois.
- Nota da fonte em `docs/superpowers/specs/` antes do código, com amostras em `tests/dados/fontes/`.

### Pendências (pequenas, no mesmo ciclo)

1. Alerta quando a auditoria de cobertura falha: alarme sobre a linha de log da auditoria (filtro de métrica no CloudWatch Logs: a 10ª métrica e o 10º alarme do plano grátis).
2. `EXPLAIN (ANALYZE, BUFFERS)` com e sem o GIN numa ramificação do Neon; números na doc da etapa 1.
3. Revisão pelo Front-End-Checklist (acessibilidade, cabeçalhos de segurança no CloudFront, bundle de 1,1 MB).
4. Marcar como superado o agendamento local no checklist.
5. Documento "não lido" porque o ZIP não tinha edital não deve marcar a análise como incompleta (Patrocínio Paulista).
6. Entender por que a Compesa não teve análise na rodada de hoje (filtro ou sem documento).

## Respostas à revisão do construtor **[decisões do Claude]**

1. **Linha de serviço.** O histórico do órgão (`orgao`, `vencedores`) usa todas as linhas do recorte (saneamento). A `regua` é uma **lista**, uma por linha do edital; edital sem linha usa uma régua de todas as linhas.
2. **7b** é um marco separado, planejado depois da medição de disponibilidade das planilhas. Este ciclo: 7a, pendências e 7c.
3. **Sanepar.** O histórico do órgão vem dos pregões que ela publica no PNCP (`orgao_cnpj` = CNPJ da Sanepar, editais `pncp~`). As obras do portal próprio ficam sem resultado na v1 (só em PDF). A ficha de um edital `sanepar~` mostra o histórico do órgão Sanepar a partir dos editais `pncp~` dela.
4. **Desconto de uma contratação.** `1 − Σ homologado ÷ Σ estimado`, somando só os itens homologados com estimado conhecido (sem `orcamento_sigiloso` e com estimado > 0). Contratação com item ainda em andamento entra marcada `parcial`; contratação só com itens sigilosos fica sem desconto. Vitória conta uma por contratação (o fornecedor que levou o maior valor homologado dela).
5. **Compesa na nuvem.** Desde 27/11 a Compesa é coletada no Fargate (a "falta de resposta da AWS" era o nosso security group: porta 8743). O teste do TCE-PE roda numa task avulsa do Fargate.

## O que fica com o construtor

Nomes de colunas e índices, formato exato das respostas, paginação dos itens, testes e amostras, ordem das tarefas, layout fino com componentes Astryx já usados.

## Ordem

7a.1 + 7a.2 (dados) → 7a.4 + 7a.5 (API e tela) → disparo da carga histórica em produção → 7a.3 (teste TCE-PE da AWS) → pendências → 7c → 7b (com a medição pronta).
