# Painel (tela inicial) com triagem e prioridade configurável

Data: 28/11/2026. Pedido do Augusto: um painel que traga as novidades e os editais mais adequados, já com os botões de triagem "Interessa / Descartar / Analisando" e com a ordem dos critérios configurável "de forma ultra simples".

## Por que
Hoje o front tem a lista de editais abertos, o detalhe e o login. A lista é exaustiva e não responde "o que eu preciso olhar hoje?". O painel é a tela que o Augusto mostra ao tio (FR Saneamento): urgência, novidade e aderência num lugar só, e a triagem para o que já foi visto não voltar a ocupar espaço.

## Objetivos
- Tela inicial "Painel" que mostra, nesta ordem: faixa de status da coleta, 4 contadores clicáveis, "Agir agora", "Novos desde sua última visita" e "Mais adequados".
- Triagem de um clique em cada edital (no painel, na lista e no detalhe): Interessa, Analisando, Descartar; e voltar a "sem triagem".
- Prioridade dos critérios configurável por quem usa, sem tela de configuração.

## Não objetivos
- Notificação por e-mail ou push.
- Gráficos (o contrato do front proíbe dependência nova).
- Motivo do descarte virar regra automática de filtro (pode vir depois; o motivo fica só guardado).
- Nota numérica de aderência: o veredito continua de três valores (apto, verificar, inapto).

## Anti-objetivos (seria falha mesmo funcionando)
- Contador que não leva a lugar nenhum.
- "Mais adequados" que pareça definitivo quando o perfil é de exemplo.
- Edital vencido ou sem prazo misturado com os que ainda dá tempo.
- Triagem perdida ao trocar de navegador (por isso fica no servidor, não no navegador).

## Abordagens consideradas
- **A. Feed "Hoje"** (escolhida): status + contadores + seções priorizadas. Responde o que fazer hoje.
- **B. Cartões por linha** (redes, perdas, ...): bom resumo de quanto há, mas não diz o que fazer.
- **C. Kanban por prazo**: é a lista reordenada; duplica o que existe.
O Augusto aceitou A e acrescentou triagem e prioridade configurável.

## Desenho

### Tela
```
+------------------------------------------------------------------------------------------+
| Radar FR   Painel  Editais                                         atualizado às 06:12    |
+------------------------------------------------------------------------------------------+
| Coleta: 4 fontes ok · DAE Jundiaí sem dado novo há 30 h · IA: US$ 0,40 de 2,00 no mês     |
|------------------------------------------------------------------------------------------|
| [ 42 abertos ]  [ 6 encerram em 7 dias ]  [ 3 novos desde sua visita ]  [ 5 aptos à FR ] |
|------------------------------------------------------------------------------------------|
| Prioridade: (1 Prazo) (2 Aderência) (3 Valor) (4 Local)     toque para pôr em primeiro    |
|------------------------------------------------------------------------------------------|
| Agir agora: apto ou "verificar", encerra em até 7 dias, não descartado (até 5)            |
|  em 2 dias  Sanepar PR Redes  Ampliação de rede de esgoto  R$ 3,2 mi  apto     [I][A][D]  |
|------------------------------------------------------------------------------------------|
| Novos desde sua última visita (até 10)                                                    |
|------------------------------------------------------------------------------------------|
| Mais adequados: na ordem da prioridade; inapto e descartado ficam de fora (até 10)        |
|------------------------------------------------------------------------------------------|
| Em análise / Interessa (os triados, até 10 cada)                                          |
+------------------------------------------------------------------------------------------+
```
- Cada contador é link para a Lista com o filtro correspondente (na URL).
- Cada "verificar" mostra o motivo (o primeiro item `verificar` da comparação, ex.: "CAT").
- Perfil de exemplo: as seções de aderência mostram o aviso "perfil de exemplo: aderência ilustrativa" enquanto o perfil carregado for o `config/perfil.toml` de exemplo (o back-end informa isso).
- Edital sem análise mostra "sem análise" e entra em "Mais adequados" depois dos analisados.

### Prioridade "ultra simples"
- Uma linha de 4 fichas numeradas: Prazo, Aderência, Valor, Local. Tocar numa ficha a põe em primeiro; as outras descem mantendo a ordem. Não há tela de configuração nem arrastar.
- Ordem padrão: Prazo, Aderência, Valor, Local.
- Significado: Prazo = encerra antes primeiro; Aderência = apto > verificar > sem análise > inapto; Valor = maior valor estimado primeiro (sem valor por último); Local = UF da sede primeiro.
- A ordenação é lexicográfica pelos critérios na ordem escolhida. Vale para "Mais adequados" e para a Lista; "Agir agora" é sempre por prazo (é a definição da seção).
- Guardada por usuário no servidor (junto com a última visita), para valer em qualquer navegador.
- "Local" precisa da UF da sede: campo novo opcional `uf_sede` no `[empresa]` do perfil. Sem ele, a ficha "Local" não aparece.

### Triagem
- Estados: `interessa`, `analisando`, `descartado` (e ausência = sem triagem). Motivo opcional em texto curto só no descarte.
- **Compartilhada pela empresa**, não por pessoa: o tio e o Augusto veem a mesma triagem. Guarda quem mudou (e-mail do token) e quando.
- Descartado some do painel e da lista; a lista ganha o filtro "mostrar descartados".
- Botões no cartão do painel, no cartão da lista e no detalhe. Troca otimista no front, com volta se a API falhar.

### Back-end
- Migração: coluna `edital.primeiro_visto_em` (timestamptz), preenchida na inserção; backfill = `least(visto_em, data_publicacao)` dos existentes (convertendo fuso).
- Tabela `triagem` (fonte, id_na_fonte, estado, motivo, alterado_por, alterado_em), chave (fonte, id_na_fonte).
- Tabela `preferencia_usuario` (sub do Cognito, ultima_visita_em, prioridade text[]).
- Rotas (todas sob o authorizer JWT existente):
  - `GET /api/painel`: status por fonte (frescor, última rodada, falha), gasto de IA do mês e teto, contadores, as seções prontas (com veredito, motivo e triagem) e se o perfil é de exemplo.
  - `PUT /api/triagem/{id_publico}` com `{estado, motivo?}`; `DELETE` volta a sem triagem.
  - `GET/PUT /api/preferencias`: prioridade e "marcar visita". A última visita só avança quando o painel é aberto, e "novos" compara com a visita anterior.
  - Resumo de edital (`/api/editais`) ganha `veredito`, `motivo_verificar`, `triagem` e `primeiro_visto_em`, e aceita `ordem=prazo,aderencia,valor,local` e `descartados=true`.
- As primeiras rotas de escrita da API: o `sub` e o `email` vêm das claims do JWT que o API Gateway já valida; sem token válido, 401.

### Front
- Rota nova `/painel`, primeira do menu; `/` continua sendo a Lista ("Editais"), para não quebrar links salvos. Abrir o app sem rota leva ao painel.
- Só componentes Astryx e os componentes próprios que já existem; o que se repetir entre seções vira componente interno da página ou componente novo aprovado.
- Mudanças de contrato do front (rota nova, componentes novos, testes de contrato) vão com o trailer `Contrato-Front: aprovado`, que o Augusto autoriza ao aprovar este desenho.
- `fonteExemplo` ganha os mesmos dados para o modo de demonstração e os testes.

### Decisões de construção (após a leitura do revisor)
- **Escrita no banco**: a API usa o mesmo `database_url` (SSM) dos coletores, com o mesmo papel do Neon, que escreve. Sem mudança de infra; migrações seguem pelo caminho de sempre.
- **Qual veredito**: o da análise mais recente do modelo padrão (`obter_analise`: `criada_em` desc), que já fica gravado em `analise_edital.veredito` e `comparacao`. Nada é calculado na leitura; a lista junta a análise mais recente por edital numa subconsulta (`distinct on`).
- **Perfil de exemplo e UF da sede**: a API não lê o perfil (só a tarefa de análise lê, do S3). O `config/perfil.toml` de exemplo ganha `exemplo = true`; a análise e o `radar recomparar` gravam numa tabela de uma linha `perfil_resumo` (exemplo, uf_sede, atualizado_em), que a API lê. Sem linha = exemplo.
- **"Novos desde sua última visita"**: `preferencia_usuario` guarda `ultima_visita_em` e `visita_anterior_em`. O front chama `PUT /api/preferencias/visita` uma vez por sessão do navegador; o servidor só desloca (`visita_anterior_em = ultima_visita_em`, `ultima_visita_em = agora`) se a última for de mais de 1 hora atrás, então recarregar não apaga os novos. "Novos" = `primeiro_visto_em > visita_anterior_em`. Primeira visita (sem linha): novos dos últimos 7 dias.
- **Identidade**: `sub` e `email` das claims do ID token que o API Gateway valida. Sem authorizer (modo local, testes, demonstração), a identidade é fixa `local`; em produção o login é obrigatório (`exigir_login`).
- **Filtros da lista para os contadores**: `/api/editais` ganha `encerra_em_dias`, `novos=true`, `veredito=apto`, `triagem=<estado>`, `descartados=true` e `ordem=`. Ordenação no servidor; a lista segue sem paginação (limite atual de 500).
- **Aberto, vencido e sem prazo**: a mesma definição de "aberto" da Lista (`_aberto`, prazo depois de agora em Brasília). Nenhuma seção do painel mostra o que a Lista não mostraria; "encerra em até 7 dias" conta em Brasília.
- **Faixa de status**: frescor por fonte (`frescor_por_fonte`), status da última rodada (`coleta_retrato` e `coleta_janela`) e gasto do mês contra o teto do `ia.toml`. "Sem dado novo" acima de 36 h, o mesmo limite do `AvisoDesatualizado`.
- **Triagem órfã**: a linha de triagem fica mesmo se o edital sumir; não aparece porque o edital não aparece.

## Restrições
- Tudo no plano gratuito da AWS, sem serviço novo.
- Repositório público: nada do perfil real nem da empresa no código ou nos exemplos.
- Contrato do front (`frontend/CLAUDE.md`): sem HTML cru, sem dependência nova, dados por `useFonte`/`useCarga`, datas por `formatos.ts`, jscpd sem duplicação.

## Fica para quem constrói
- Nomes exatos de componentes, formato do JSON do `/api/painel`, limites de cada seção (os números acima são o ponto de partida).
- Como mostrar o motivo do descarte (campo opcional depois de "Descartar").
- Textos dos estados vazios.
