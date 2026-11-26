# Vídeo 3: um edital, várias fontes (Sanepar e Compesa)

Roteiro em tópicos. Material de apoio: `docs/etapas/03-coletores.md`.

## Gancho (30 s)

- Pergunta: "Duas companhias de saneamento não publicam no PNCP. Como entram no mesmo radar sem virar um segundo sistema?"
- Mostrar o resultado: Sanepar com 92 editais e 15 que casam com o filtro; Compesa com 283 editais nos últimos 12 meses.
- Promessa: no fim, o que a rodada real mostrou e que nenhum teste tinha achado.

## Janela ou retrato (2 min)

- O PNCP se coleta por janela de datas: dá para pedir "o que mudou naquele dia". A Sanepar e a Compesa não têm isso: só uma lista inteira.
- O motor de retratos lê a lista inteira uma vez por dia. A reconciliação encerra os editais que saíram da lista.
- Quando cada um faz sentido: janela quando a fonte filtra por data e é grande; retrato quando é pequena e só lista.

## Um edital, várias fontes (2 min)

- A chave do `edital` passa a ser `(fonte, id_na_fonte)`. O id público do front vira `fonte~id`.
- A situação vira um valor normalizado (`aberta`, `em_julgamento`, `encerrada`, `outra`), com o texto original guardado.
- O cartão ganha o selo da fonte. O mesmo filtro, a mesma API e a mesma tela servem as três.

## Cortesia com a fonte (2 min)

- O cliente pausa 3 s entre requisições, se identifica pelo User-Agent e para no primeiro 403 ou 429.
- Cada fonte tem um mínimo de registros e uma trava própria, para duas coletas não correrem juntas.
- Mostrar o log de uma rodada.

## A migração da chave (4 min)

- A migração `0003_multifonte` troca a chave primária de uma tabela com 38.382 linhas, numa transação só (4,5 s no banco local). `alembic check` depois: "No new upgrade operations detected".
- O PNCP continua igual: `radar filtrar` devolve os mesmos 24 abertos, `diff` vazio.
- **O EXPLAIN:** 2,5 ms antes, 30,4 ms logo depois, 4,6 ms depois do `VACUUM (ANALYZE)`. Mostrar os três planos lado a lado.
  - Por quê: o UPDATE da migração duplicou todas as linhas (delete mais insert), e os índices apontavam para as duas versões. O índice de prazo devolveu 37.790 entradas para 18.935 editais.
  - O autovacuum resolveu sozinho, mas o certo é rodar o `VACUUM (ANALYZE)` logo depois.
  - O índice novo não foi escolhido, porque quase tudo está `aberta`. Dizer isso como resultado.
- Se fosse produção: colunas nulas, preenchimento em lotes, `CREATE UNIQUE INDEX CONCURRENTLY`, `ADD PRIMARY KEY USING INDEX`, `VACUUM`.

## A rodada real (4 min)

Contar as três descobertas que os testes não pegaram.

1. **A data da Compesa segue o idioma pedido, não a página.** A página 1 veio de um navegador (pt-BR) e a página 2 do `curl` (sem idioma). Daí o modelo errado de "página 1 em dd/MM e as seguintes em MM/dd". A trava de datas impossíveis (mais de 20% numa página) falhou alto em vez de gravar dia e mês trocados. Correção: pedir sempre pt-BR.
2. **A lista da Compesa é o histórico inteiro**, desde 2012. O limite de 50 páginas disparou. Correção: janela de 12 meses.
3. **Lista vazia pode ser layout quebrado.** "Abertura" vazia na Sanepar com "Julgamento" cheio passava por todas as travas e encerraria todos os abertos. Agora é falha.

Ponto a fixar: a segunda rodada deu 0 alterados nas duas fontes. Isso prova que o hash não acusa mudança quando nada mudou. Os 2 alterados da primeira rodada eram anexos novos de verdade.

## Duplicatas entre o PNCP e as fontes próprias (1 min)

- Conferido por CNPJ e por texto: a Compesa tem 0 editais no PNCP, e os 67 da Sanepar são pregões, enquanto os 92 da fonte própria são de obras e engenharia, sem coincidência.
- Decisão: nenhuma regra de exclusão por enquanto. A regra entra se um dia houver sobreposição.

## Retenção dos brutos (1 min)

- Depois de um retrato concluído, `limpar_brutos` mantém as páginas dos 3 concluídos e dos 10 em falha mais novos. A falha é o que se quer diagnosticar.

## Perguntas (1 min)

Deixar na tela e responder no vídeo (as de `docs/etapas/03-coletores.md`):

1. Como você trocou a chave primária de uma tabela com 38 mil linhas, e por que a consulta ficou mais lenta logo depois?
2. Qual a diferença entre coletar por janela e por retrato?
3. Como o radar detecta a retificação na Sanepar, e como você provou que não gera alterações falsas?
4. O que é "cortesia com a fonte" no código, e o que o coletor faz num 403 ou 429?
5. Por que um retrato vazio, ou com "Abertura" vazia, conta como falha?

## Fecho

- Entregue: três fontes no mesmo radar, com a mesma auditoria de falha em silêncio da etapa 1.
- Limite honesto: com o filtro atual, a Compesa traz 0 editais que casam. O radar mostra isso, não esconde.
- Próximo: filas (tema 2).
