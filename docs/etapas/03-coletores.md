# Etapa 3: coletores Sanepar e Compesa (rascunho)

> Rascunho. As seções de retrato, cortesia e perguntas de entrevista entram nas próximas tarefas.

## Migração da chave

### O que mudou

A migração `0003_multifonte` troca a chave de `edital`, que era `numero_controle_pncp`, por `(fonte, id_na_fonte)`.

- **Preenchimento:** os 38.382 editais existentes viram `fonte='pncp'` e recebem `id_na_fonte = numero_controle_pncp`. O `modalidade_nome` sai de `bruto->>'modalidadeNome'`. A situação segue o mapa do PNCP: 1 vira `aberta`; 2 e 3 viram `encerrada`; 4 e qualquer outro código viram `outra`.
- **Colunas:** `situacao_nome` é renomeada para `situacao_original`. Entram `situacao_normalizada`, com CHECK nos quatro valores, além de `visto_em` e `hash_conteudo`. Também há um CHECK em `fonte`.
- **Nulos:** `modalidade_id`, `situacao_id`, `orgao_cnpj` e `data_publicacao` passam a aceitar nulo, porque só o PNCP garante esses campos.
- **Índice novo:** `ix_edital_situacao_encerramento (situacao_normalizada, data_encerramento_proposta)`.
- **Downgrade:** volta ao esquema 0002 e apaga os editais que não são do PNCP, porque o esquema antigo não tem como guardá-los.
- **Transação:** tudo roda numa transação só. No banco local, `alembic upgrade head` levou 4,5 s.

Resultado do `alembic check` depois do upgrade: `No new upgrade operations detected`. O ORM e o banco batem.

### O PNCP continua igual

`uv run radar filtrar` (config/filtro.toml), no mesmo banco:

| | Editais abertos | Saída |
|---|---|---|
| Antes (0002) | 24 | — |
| Depois (0003) | 24 | idêntica, linha a linha (`diff` vazio) |

`radar auditar` depois da migração: 120 esperadas, 120 concluídas, nada faltando, sem falhas.

Situação normalizada depois da migração: 37.280 `aberta`, 329 `encerrada`, 773 `outra`. "Aberta" aqui é só a situação; com o prazo, são bem menos.

### EXPLAIN (ANALYZE, BUFFERS) da consulta do `filtrar`

Medido com a consulta que o `filtrar` gera (`_montar_consulta`, 4 linhas de serviço, 4 UFs, `somente_abertos`), em três momentos. Cada número vale para a terceira execução seguida, com o cache quente.

| Momento | Execução | Buffers | Blocos de heap | Plano |
|---|---|---|---|---|
| Antes (0002) | 2,5 ms | 366 | 127 | BitmapAnd(prazo, OR de 4 GIN); UF, modalidade e `situacao_id = 1` no Filter |
| Logo depois da 0003 | 30,4 ms | 1.586 | 124 | BitmapAnd(prazo, UF, OR de 4 GIN). Os índices devolvem o dobro de linhas (37.790 no prazo, contra 18.935) |
| Depois de `VACUUM (ANALYZE)` | 4,6 ms | 419 | 62 | O mesmo plano, com os índices limpos |

**Leitura:**

1. **O UPDATE da migração duplica todas as linhas.** No Postgres, UPDATE é delete mais insert: cada um dos 38 mil editais ganhou uma versão nova, e a antiga virou tupla morta. Logo depois da migração, os índices ainda apontavam para as duas versões. A varredura do índice de prazo devolveu 37.790 entradas para 18.935 editais, e o GIN, 351 para 176. Daí a consulta 12 vezes mais lenta e com 4 vezes mais buffers. O heap ficou com 92 MB, e o VACUUM não devolve esse espaço; só um `VACUUM FULL` ou um `pg_repack` devolveria.
2. **O autovacuum resolveu sozinho**, poucos segundos depois da medição (às 15:22:33, contra 15:22:31 do EXPLAIN). Mesmo assim, o certo é rodar `VACUUM (ANALYZE) edital` logo depois de uma migração que reescreve a tabela, ou incluí-lo no roteiro do deploy. O ANALYZE também atualiza as estatísticas das colunas novas, e sem elas o planejador estima no escuro.
3. **Depois do VACUUM, a consulta ficou em 4,6 ms contra 2,5 ms.** É ruído nessa escala, e a forma do plano é a mesma. O filtro de modalidade virou `fonte <> 'pncp' OR modalidade_id IN (...)`, e o planejador passou a cruzar também o índice de UF no BitmapAnd. Os blocos de heap lidos caíram pela metade (62 contra 127), porque as linhas reescritas ficaram juntas em páginas novas.
4. **O índice novo `(situacao_normalizada, data_encerramento_proposta)` não foi escolhido.** Quase todos os editais estão `aberta` (37 mil de 38 mil), então a primeira coluna não filtra quase nada, e o índice só de prazo dá o mesmo resultado. Ele passa a valer quando houver muitos encerrados, por exemplo depois de meses de coleta ou com a Compesa, que traz os últimos 12 meses com todas as situações. Fica para reavaliar na Task 6, com dados das três fontes.

### Se fosse produção

Com a tabela grande e o sistema no ar, a migração não seria feita numa transação só, porque o `ALTER ... PRIMARY KEY` e o UPDATE de tudo travam a tabela. O roteiro seria:

1. criar as colunas novas como nulas (operação só de catálogo);
2. preencher em lotes, com commit a cada lote;
3. criar o índice único novo com `CREATE UNIQUE INDEX CONCURRENTLY`;
4. trocar a chave numa janela curta com `ALTER TABLE ... ADD PRIMARY KEY USING INDEX`;
5. rodar `VACUUM (ANALYZE)` no fim.

Nesse meio-tempo, o código teria de aceitar os dois formatos de id.

## Duplicatas entre o PNCP e as fontes próprias

Conferido em 2026-11-26, só leitura, no banco local (PNCP: 38.382 editais publicados de 27/10 a
25/11/2026, modalidades 4 a 7; Sanepar: 92; Compesa: 283, aberturas de 26/11/2025 a 24/11/2026).

| Órgão | CNPJ | No PNCP | Sobreposição com a fonte própria |
|---|---|---|---|
| Compesa | 09769035000164 | 0 editais | nenhuma |
| Sanepar | 76484013000145 | 67 editais, todos `Pregão - Eletrônico` | nenhuma |

- **Compesa:** nenhum edital do PNCP com o CNPJ 09.769.035/0001-64, nem com `orgao_nome` contendo
  "COMPESA" ou começando por "COMPANHIA PERNAMBUCANA" (o único "Companhia Pernambucana" é a Copergás,
  CNPJ 41025313000181, 3 editais). O CNPJ foi confirmado fora do banco (cadastro da Receita, sede
  em Recife). Como o PNCP não traz a Compesa nas modalidades coletadas, não há o que deduplicar: o
  coletor só passa a gravar `orgao_cnpj = 09769035000164` nos editais da Compesa, para que um
  cruzamento futuro seja por CNPJ.
- **Sanepar:** os 67 editais do PNCP são pregões (compras e serviços comuns: material de laboratório,
  locação de detectores, TI). Os 92 da fonte própria são OE (obras e engenharia, `LICITACAO` e
  `LICITACAO ELETRONICA`). Cruzando por data de encerramento no mesmo dia, os pares têm objetos sem
  relação ("AQUIS DE MATERIAL PARA CROMATOGRAFIA" contra "EXECUCAO DE OBRAS DE AMPLIACAO DO SISTEMA
  DE ESGOTO"); pelos primeiros 40 caracteres do objeto, 0 coincidências.

**Decisão:** sem sobreposição, nenhuma regra de exclusão no `filtrar` (a ideia era esconder do PNCP
os CNPJs cobertos por portal próprio). Se um dia o PNCP passar a trazer OE da Sanepar ou licitações
da Compesa, a regra entra com uma lista de CNPJs cobertos fora do `filtro.toml` das linhas e UFs.

## Retenção das páginas brutas

Cada retrato grava todas as páginas (a Sanepar, umas 100 por rodada com os detalhes). Depois de um
retrato concluído e registrado, `limpar_brutos` apaga as páginas dos retratos mais antigos da
mesma fonte: ficam as dos 3 concluídos mais novos e as dos 10 em falha mais novos (a falha é o que
se quer diagnosticar). As linhas de `coleta_retrato` ficam todas. O log é `brutos_removidos`.
