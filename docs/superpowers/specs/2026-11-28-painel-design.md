# Etapa 08: painel (tela inicial), triagem e prioridade configurável

Data: 28/11/2026. Revisado no mesmo dia por três revisões adversariais (produto, técnica e segurança, escopo e contrato), cujos achados estão incorporados abaixo.

Pedido do Augusto: um painel que traga as novidades e os editais mais adequados, já com os botões de triagem, e com a ordem dos critérios configurável "de forma ultra simples".

## Por que
O front tem a Lista de editais abertos, o Detalhe e o login. A Lista é exaustiva e não responde "o que eu preciso decidir agora?". O painel é a tela que o Augusto mostra ao tio (FR Saneamento): o que ainda dá tempo de decidir, o que está na reta final e o que já foi triado, sem repetir o que já foi descartado.

## Objetivos
- Tela inicial que separa **decidir** (edital sem triagem) de **reta final** (edital em que a empresa vai entrar ou está avaliando), com cada edital numa seção só.
- Triagem compartilhada pela empresa, de um toque, com desfazer e com quem fez.
- Prioridade dos critérios configurável por quem usa, num toque, e com efeito visível.

## Não objetivos
- Notificação por e-mail ou push nesta etapa. É o próximo passo natural (resumo semanal), registrado no fim.
- Gráficos (o contrato do front proíbe dependência nova).
- Nota numérica de aderência: o veredito continua de três valores.
- Motivo do descarte virar regra automática de filtro.

## Anti-objetivos (seria falha mesmo funcionando)
- Edital urgente que some do topo porque não foi analisado.
- Aderência mostrada como se valesse quando o perfil é de exemplo.
- Um toque errado esconder um edital dos dois usuários sem aviso nem volta.
- Contador ou ficha que não muda nada visível.
- Edital vencido misturado com os que ainda dá tempo.

## Abordagens consideradas
- **A. Feed priorizado** (escolhida): seções por momento da decisão.
- **B. Cartões por linha**: bom resumo de quantidade, não diz o que fazer.
- **C. Kanban por prazo**: é a Lista reordenada.

## O que mudou com a revisão adversarial
- "Agir agora (7 dias, só apto/verificar)" virou **Decidir** (sem triagem, até 20 dias, qualquer veredito exceto inapto, "sem análise" incluído com selo) e **Reta final** (triados como participar/avaliando, até 7 dias). Pela Lei 14.133 (art. 55) obra tem de 10 a 60 dias úteis até a proposta, e atestado, CAT, garantia e visita técnica levam semanas: 7 dias é tarde para decidir.
- "Novos desde sua última visita" virou **selo "novo"** no cartão (visto pela primeira vez há até 3 dias e ainda sem triagem). A caixa de entrada passa a ser "sem triagem", que é compartilhada e explícita. Sai o carimbo de visita por pessoa, que quebrava com dois usuários e dois aparelhos e tinha condição de corrida.
- A prioridade continua "tocar para pôr em primeiro", mas cada critério agora tem **faixas** (prazo exato desempatava tudo e tornava os outros critérios inúteis). "Valor" passou a ser **cabe na capacidade da empresa**, não "maior primeiro".
- Estados da triagem com nome de decisão: **Avaliando**, **Vamos participar**, **Não vamos**. Desfazer por 10 s, "Não vamos" separado dos outros botões, e o cartão mostra quem triou.
- Com perfil de exemplo, a aderência **não aparece** ("aderência indisponível: perfil não carregado"), em vez de aparecer com um aviso que vira paisagem.
- A faixa de status só aparece **quando algo está errado**, em linguagem de consequência.
- `/` passa a ser o painel e a Lista vai para `/editais` (a versão anterior se contradizia: com HashRouter, "abrir sem rota" é `/`).
- Entrega em **três fases** (08a, 08b, 08c), com inventário nomeado de arquivos e componentes por fase.
- Técnica: `primeiro_visto_em` protegido do upsert, backfill com data-sentinela e fuso certo, papel do banco só para a API, escrita sem identidade recusada, junção lateral com índice, ordem por lista fechada de critérios.

## Desenho

### Tela
```
+--------------------------------------------------------------------------------------+
| Radar FR   Painel  Editais                                     atualizado às 06:12    |
+--------------------------------------------------------------------------------------+
| (só se algo falhou) Jundiaí pode estar desatualizado desde ontem.  [detalhes]         |
|--------------------------------------------------------------------------------------|
| [ 9 para decidir ]  [ 2 na reta final ]  [ 3 novos ]  [ 31 abertos ao todo ]          |
| Prioridade: (1 Prazo) (2 Aderência) (3 Valor) (4 Local)   toque para pôr em primeiro  |
|--------------------------------------------------------------------------------------|
| Reta final: vamos participar / avaliando, encerra em até 7 dias                       |
|  encerra em 3 dias · Sanepar · Curitiba/PR · Redes · R$ 3,2 mi                        |
|  Ampliação de rede de esgoto...                       Vamos participar (Augusto)      |
|--------------------------------------------------------------------------------------|
| Decidir: sem triagem, encerra em até 20 dias                                          |
|  encerra em 12 dias · PNCP · Uberaba/MG · Perdas · R$ 800 mil   NOVO   verificar: CAT |
|  Setorização e macromedição...                                                        |
|  [ Avaliando ] [ Vamos participar ]                             [ Não vamos ]         |
|--------------------------------------------------------------------------------------|
| Acompanhando: vamos participar / avaliando com mais de 7 dias                         |
| Mais adiante: sem triagem, mais de 20 dias (recolhido)                                |
| Não vamos (recentes): os últimos 5 descartados, com quem e quando (recolhido)         |
+--------------------------------------------------------------------------------------+
```
- Cada edital aparece numa seção só, na primeira em que cabe, de cima para baixo.
- Seção vazia vira uma linha ("nada para decidir agora").
- Contadores são links para a Lista filtrada (filtros na URL).
- Cartão em duas ou três linhas (cabe em 360 px), botões com texto e área de toque de 44 px, "Não vamos" separado. Os botões ficam numa linha própria **fora** do link do cartão (não há interativo dentro de interativo).
- "verificar: CAT" mostra o primeiro motivo de `verificar` da comparação. "sem análise" mostra "conferir edital".
- Inapto não entra em Decidir; aparece na Lista com o selo.

### Triagem (fase 08b)
- Estados: `avaliando`, `participar`, `nao_vamos`; ausência = sem triagem. Motivo opcional (até 200 caracteres) só em "Não vamos".
- Compartilhada pela empresa. Guarda quem mudou (e-mail do token) e quando, e o cartão mostra isso.
- Ao trocar: atualização otimista, aviso "Marcado como Não vamos. [Desfazer]" por 10 s. Se a API falhar (inclusive 429 do limite do API Gateway), volta e avisa.
- "Não vamos" some do painel e da Lista (a Lista ganha o filtro "mostrar os que não vamos"); o painel guarda os 5 mais recentes recolhidos, para achar o erro.
- Triagem cujo edital sumiu fica guardada; só não aparece.

### Prioridade (fase 08c)
- Uma linha de 4 botões numerados: Prazo, Aderência, Valor, Local. Tocar num o põe em primeiro; os outros descem mantendo a ordem. Tocar de novo no primeiro não faz nada. Rótulo para leitor de tela: "Pôr Prazo em primeiro". Não são botões de alternância (não há `aria-pressed`).
- Faixas, para que cada critério tenha efeito:
  - Prazo: encerra em até 7 dias; até 20 dias; depois.
  - Aderência: apto; verificar; sem análise; inapto. Some com perfil de exemplo.
  - Valor: cabe na capacidade da empresa; acima; sem valor. Capacidade = `valor_maximo_obra` do perfil (campo novo opcional). Sem o campo, o botão Valor some.
  - Local: na UF da sede; outras. UF da sede = `uf_sede` do perfil (campo novo opcional). Sem o campo, o botão Local some.
- Ordenação: pelas faixas na ordem escolhida e, no fim, pelo prazo exato. Vale dentro de cada seção do painel e na Lista. A ordem padrão é Prazo, Aderência, Valor, Local.
- Guardada por usuário no servidor (`preferencia_usuario`: sub, prioridade), vale em qualquer aparelho.

### Back-end
**08a (só leitura):**
- Migração: `edital.primeiro_visto_em timestamptz`. Preenchida só na inserção: o upsert comum (`registro.py` `_upsert`, que atualiza todas as colunas não-chave) passa a excluir a coluna do `SET`, com teste de que um upsert com conteúdo novo e a reconciliação de retrato não a mudam. Backfill: `least(visto_em, data_publicacao at time zone 'America/Sao_Paulo')` quando houver, limitado a no máximo a data da migração menos 30 dias, para que nada que já existia apareça como novo (a Compesa não tem data de publicação e o PNCP não grava `visto_em`).
- Veredito no resumo: `LEFT JOIN LATERAL` com a análise mais recente do modelo padrão (`criada_em desc, id desc limit 1`, o mesmo critério de `obter_analise`) e índice `(fonte, id_na_fonte, modelo, criada_em desc, id desc)` em `analise_edital`. O resumo ganha `veredito`, `motivo_verificar`, `primeiro_visto_em`.
- `GET /api/painel`: uma consulta-base dos abertos (a mesma definição `_aberto` da Lista, em Brasília) e as seções separadas em Python; contadores a partir da mesma base, nunca de uma lista cortada. Inclui `perfil_exemplo` e a lista de falhas de coleta.
- Estado da coleta: `frescor_por_fonte` mais o último status de `coleta_retrato` e `coleta_janela`; "atrasado" acima de 36 h, o mesmo limite do `AvisoDesatualizado`. O gasto de IA vai para o "detalhes", não para a faixa.
- `perfil_resumo` (uma linha: exemplo, uf_sede, valor_maximo_obra, atualizado_em), gravado por `analisar` e `recomparar` no banco a que estiverem ligados. `Perfil` declara `exemplo`, `uf_sede` e `valor_maximo_obra` em `[empresa]` (hoje campo desconhecido some em silêncio); o `config/perfil.toml` de exemplo tem `exemplo = true`. Sem linha = exemplo.

**08b (primeira escrita):**
- Tabela `triagem` (fonte, id_na_fonte, estado, motivo, alterado_por, alterado_em), chave (fonte, id_na_fonte).
- `PUT /api/triagem/{id_publico:path}` com `{estado, motivo?}` e `DELETE` para voltar a sem triagem; 404 para edital inexistente, 422 para estado inválido ou motivo longo.
- Identidade: `sub` e `email` das claims do ID token validado pelo API Gateway. A Lambda recebe `RADAR_EXIGIR_IDENTIDADE=true`: rota de escrita sem claims responde 401, mesmo se alguém desligar `exigir_login`. A identidade fixa `local` só existe no `radar api` local (127.0.0.1).
- Papel do banco só para a API: `radar_api`, com `SELECT` no que a API lê e `INSERT/UPDATE/DELETE` só em `triagem` e `preferencia_usuario`, num segundo parâmetro SSM. A senha é gerada e gravada no SSM pelo controlador, nunca no repositório.
- Revoga, em `docs/decisoes/0008-api-com-escrita.md`, o "API só leitura" da etapa 02.

**08c:**
- `preferencia_usuario` (sub, prioridade text[], atualizado_em); `GET/PUT /api/preferencias`.
- `/api/editais` e `/api/painel` aceitam `ordem: list[Criterio]` (StrEnum fechado; duplicata ou critério indisponível = 422). A ordem vira `ORDER BY` a partir de um dicionário fixo de expressões, antes do `LIMIT`.

**Filtros da Lista** (para os contadores): `encerra_em_dias`, `novos=true`, `veredito=apto`, `triagem=<estado>|sem`, `nao_vamos=true`, todos validados por enum ou inteiro com limite.

### Front
- Rotas: `/` = Painel (primeiro no menu), `/editais` = Lista ("Editais"), `/edital/:id`, `/entrar`, `*`. Um link antigo `#/?linha=...` ou `#/?uf=...` que chega ao Painel é redirecionado para `/editais` com os mesmos parâmetros. O `Shell` (guardado) não muda: a marca já aponta para `/`.
- Componentes Astryx usados: `ClickableCard` (contadores), `ToggleButtonGroup` de seleção única que aceita desmarcar (triagem: `aria-pressed` é o estado atual, que é a semântica certa), `Button` (prioridade), `Banner` (faixa de problema), `Collapsible` (seções recolhidas), `Toast` ou equivalente para o desfazer (a confirmar com `astryx search`; se não houver, `Banner` temporário).
- `fonteExemplo`: triagem e prioridade em memória; a ordenação segue um arquivo de casos compartilhado com o back-end (o back-end é a referência).

### Inventário de mudanças no contrato do front (o que a aprovação cobre)
O trailer `Contrato-Front: aprovado` vale só para os itens desta lista, na fase indicada. Qualquer coisa fora dela volta para o Augusto.

| Fase | Item | Arquivos guardados ou de contrato |
|---|---|---|
| 08a | Rota `/` passa a ser `Painel`; Lista vai para `/editais`; página nova `Painel.tsx` | `rotas.tsx`, `rotas.test.ts`, `frontend/CLAUDE.md` (lista de rotas e "única no menu") |
| 08a | Componentes novos `SecaoPainel`, `ContadorPainel`, `AvisoColeta` | `componentes.test.ts` |
| 08a | Links que apontam para a Lista passam a `/editais` | `Entrar.tsx`, `Detalhe.tsx` (Voltar), `NaoEncontrada.tsx` |
| 08a | E2E e capturas de tela regeradas no container Linux do CI | `e2e/` |
| 08b | Componente novo `TriagemEdital`; `CartaoEdital` ganha a linha de triagem fora do link | `componentes.test.ts`, `e2e/` |
| 08c | Componente novo `PrioridadeCriterios` | `componentes.test.ts`, `e2e/` |

### Fases e ordem
1. **08a — Painel de leitura.** `primeiro_visto_em`, veredito no resumo, `perfil_resumo`, `/api/painel`, faixa de status, troca de rotas, seções Decidir / Mais adiante (sem triagem ainda, todos contam como "sem triagem"), selo novo, contadores. Já dá para mostrar ao tio.
2. **08b — Triagem.** Primeira escrita, revisada sozinha: tabela, rotas, papel `radar_api`, guarda de identidade, botões, desfazer, seções Reta final / Acompanhando / Não vamos.
3. **08c — Prioridade.** `preferencia_usuario`, `ordem=`, faixas, campos `uf_sede` e `valor_maximo_obra` no perfil.

Cada fase é um PR com revisão própria, CI verde e deploy, seguindo a convenção das etapas: `docs/etapas/08-painel.md`, roteiro em `docs/videos/`, itens novos no `docs/checklist.md`, spec e plano em `docs/superpowers/`.

### Relacionado, fora destas fases
- Fila de análise por prazo: conferir a ordem atual da fila de IA; se não for por prazo de encerramento, passar a ser, para o teto mensal ir primeiro para o que vence antes. Pequeno, pode entrar na 08a se for só uma ordenação.
- Resumo semanal por e-mail (decidir, reta final, novos) com link para o painel: depende do SES sair do sandbox.

## Restrições
- Plano gratuito da AWS, sem serviço novo (um parâmetro SSM a mais e um papel no Neon, ambos gratuitos).
- Repositório público: nada do perfil real nem da empresa no código, nos exemplos ou nos testes.
- Contrato do front (`frontend/CLAUDE.md`): sem HTML cru, sem dependência nova, dados por `useFonte`/`useCarga`, datas por `formatos.ts`, jscpd sem duplicação.
- Limite do API Gateway (burst 10, 5/s) mantido; o front trata o 429.

## Fica para quem constrói
- Formato exato do JSON do `/api/painel`, limites de itens por seção, textos dos estados vazios e do aviso de coleta.
- Se o desfazer usa `Toast` ou `Banner`, conforme o que o Astryx tiver.
