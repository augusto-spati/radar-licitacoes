# Etapa 8a: painel de leitura (tela inicial)

A Lista de editais é exaustiva e não responde "o que eu preciso decidir hoje?". A 08a põe na frente dela um **Painel** só de leitura: o que ainda dá tempo de decidir, o que vem mais adiante e se a coleta está em dia. Desenho (revisado por três revisões adversariais): `docs/superpowers/specs/2026-11-28-painel-design.md`; plano: `docs/superpowers/plans/2026-11-28-etapa-08a-painel.md`. A triagem (08b) e a prioridade configurável (08c) vêm depois.

## Por que

- É a tela que o Augusto mostra ao tio: o que ainda dá tempo de decidir, sem repetir o que já foi descartado.
- Pela Lei 14.133 (art. 55) a obra tem de 10 a 60 dias úteis até a proposta, e atestado, CAT, garantia e visita técnica levam semanas. Decidir "na reta final de 7 dias" é tarde; a janela de decisão é de **20 dias**.
- Sem triagem ainda, todo edital conta como "sem triagem": a 08a separa só por prazo.

## O que foi feito (08a)

- **`primeiro_visto_em`** (`migrations/versions/0015_painel.py`, `src/radar/editais/registro.py`): marca quando o radar viu o edital pela primeira vez. Só a inserção a grava; o upsert comum a exclui do `SET`, então conteúdo novo, versão mais nova do PNCP e reconciliação de retrato não a renovam. O backfill usa o menor entre `visto_em`, a publicação (convertida de Brasília) e 30 dias atrás: nada que já existia aparece como novo (o PNCP não grava `visto_em` e a Compesa não tem data de publicação, por isso a data-sentinela).
- **Veredito no resumo** (`src/radar/editais/filtro.py`): `LEFT JOIN LATERAL` com a análise mais recente do modelo padrão (`criada_em desc, id desc limit 1`, o critério de `obter_analise`) e o índice `ix_analise_edital_ultima`. `editais` lê `analise_edital` por `table(...)`, sem importar `analise`. O resumo ganha `veredito`, `motivo_verificar` e `primeiro_visto_em`.
- **`perfil_resumo`** (`src/radar/analise/perfil_resumo.py`): uma linha com `exemplo`, `uf_sede`, `valor_maximo_obra`, gravada por `analisar` e `recomparar`. Sem linha, ou com `exemplo = true`, é perfil de exemplo.
- **`GET /api/painel`** (`src/radar/api/app.py`, `src/radar/editais/painel.py`): uma consulta-base dos abertos (a mesma definição `_aberto` da Lista, em Brasília), seções Decidir e Mais adiante separadas em Python e contadores da **mesma base**, nunca de uma lista cortada. Devolve também `perfil_exemplo` e as falhas de coleta.
- **Faixa de coleta** (`src/radar/coleta/frescor.py`, `frontend/src/componentes/AvisoColeta.tsx`): por fonte, aparece só se a fonte está parada há mais de 36 h, nunca rodou ou a última leitura falhou. Fonte fresca com última leitura ok não aparece. A API nunca devolve o texto cru do erro.
- **Rotas e recorte da Lista** (`frontend/src/rotas.tsx`): `/` é o Painel (primeiro no menu), `/editais` é a Lista. A Lista aceita `encerra_em_dias`, `novos` e `veredito` na URL; os contadores do Painel são links para ela. Link antigo `#/?linha=redes&uf=SP` redireciona para `/editais` com os mesmos filtros. Voltar do detalhe e links do 404 e do login apontam para `/editais`.
- **Fila de análise por prazo**: a fila de editais a analisar já sai por prazo de encerramento (`order_by` em `filtro.py`), então o teto mensal de IA vai primeiro para o que vence antes. Não precisou de mudança.
- **E2E** (`frontend/e2e/painel.spec.ts`, `navegacao.spec.ts`): Painel em `/`, contador que leva à Lista filtrada, link antigo, largura do Painel e da Lista no celular. Screenshots do shell regerados no container Linux do CI (o menu ganhou "Painel").

## Decisões da 08a

- **Decidir = sem triagem, encerra em até 20 dias, qualquer veredito exceto inapto**; "sem análise" entra com selo "conferir edital". Inapto não some por engano: fica na Lista com o selo.
- **Dias de calendário em Brasília**: o edital que encerra às 23:59 do 20º dia entra; o de 00:00 do 21º não. Contador e Lista filtrada dão o mesmo número porque usam a mesma consulta.
- **Selo "novo" sem carimbo de visita**: visto pela primeira vez há até 3 dias. Carimbo por pessoa quebrava com dois usuários e dois aparelhos.
- **Perfil de exemplo ignora veredito**: sem aderência verdadeira, o link do contador não filtra por veredito, nenhum selo de aderência aparece e o Painel diz "Aderência indisponível: perfil não carregado" (uma frase, não um aviso por cartão).
- **A faixa só aparece quando algo falhou**, para não virar paisagem.
- **Voltar do detalhe vai para a Lista** (`/editais?...`, com os filtros), e **Entrar cai no Painel**.
- **Índice ascendente** em `analise_edital`: o Postgres o percorre de trás para frente para `criada_em desc, id desc`.
- Cada edital aparece numa seção só; seção vazia vira uma linha de texto; "Mais adiante" vem recolhida.

## O que fica para 08b e 08c

- **08b**: triagem compartilhada (Avaliando, Vamos participar, Não vamos), a primeira escrita da API, papel `radar_api` no banco, identidade do token, desfazer, seções Reta final, Acompanhando e Não vamos, e `docs/decisoes/0008-api-com-escrita.md`.
- **08c**: prioridade configurável por usuário (Prazo, Aderência, Valor, Local), `preferencia_usuario`, `ordem=` na API, uso de `uf_sede` e `valor_maximo_obra` (já no perfil desde a 08a) na prioridade.
- Migração 0015 no Neon e deploy da 08a, com o Augusto, nesta ordem:
  1. Aplicar a 0015 no Neon (`alembic upgrade head`) ANTES do merge e com nenhuma tarefa da coletora rodando: o backfill trava `edital`. O código antigo funciona com a 0015 aplicada.
  2. Conferir que a API antiga segue respondendo (`/api/editais` 200).
  3. Fazer o merge, para o deploy trocar as imagens, e conferir `/api/painel`.
  4. Logo depois, rodar `radar recomparar` com `RADAR_CONFIG_PERFIL` apontando para o perfil do S3 (ECS run-task com override de comando, como o `analisar`), para `perfil_resumo` refletir o perfil real antes do próximo `analisar` das 09:00. Confirmar `perfil_exemplo: false` em `/api/painel`.

## Perguntas de entrevista

1. Por que 20 dias e não 7 para decidir, e de onde vem esse número?
2. Como garantir que um edital que já existia antes da migração não apareça como "novo", e que um upsert não renove `primeiro_visto_em`?
3. Por que o contador e a Lista filtrada dão sempre o mesmo número, e que erro de fuso quebraria isso?
4. Por que o veredito entra por junção lateral com índice, e não por subconsulta por linha ou por uma cópia na tabela `edital`?
5. Por que, com perfil de exemplo, o Painel esconde a aderência em vez de mostrá-la com um aviso?
