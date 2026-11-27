# Evals da extração de exigências

Mede se a extração com IA acerta as exigências de habilitação, campo a campo, contra um gabarito
feito à mão. O CI roda em replay (respostas commitadas, sem rede e sem custo).

## Arquivos

- `textos/<slug>.json`: páginas extraídas dos documentos do edital (dado público), o
  `valor_estimado` e os documentos usados. `slug` = `id_publico` com `~` e `/` trocados por `_`.
  Gerado por `radar avaliar preparar <id_publico>`, que mascara CPF (`***.***.***-**`) e RG
  (`**.***.***-*`) de pessoas citadas no edital.
- `gabarito/<slug>.json`: `{edital, documentos, conjunto, conferido, conferido_por, conferido_em,
  observacoes, exigencias}`. `exigencias` tem o mesmo formato que o pipeline grava, com o trecho
  literal e a página de cada valor. Campo ausente (`null` ou lista vazia) = o edital não exige.
- `respostas/<modelo>/<chave>.json`: respostas do modelo em cache (geradas por `--modo gravar`).
- `linha_de_base.json`: F1 por campo do modelo padrão no conjunto `ajuste`; o CI falha se algum
  cair mais de 0,05, se a base for de outro modelo, se o conjunto `ajuste` sumir do relatório ou
  se um campo da base ficar sem F1. `{}` enquanto não houver gabarito conferido. O replay do CI
  roda com a rede bloqueada.

Conjuntos: `ajuste` (pode olhar ao mexer no prompt) e `teste` (só no fim, uma vez).

## Conferir um gabarito (Augusto)

1. Abra o PDF pela `url` em `documentos` e o `gabarito/<slug>.json` lado a lado.
2. Para cada exigência: o valor está certo? O trecho é literal e está na página indicada? Falta
   alguma exigência? Há alguma que o edital não faz?
3. Corrija o arquivo, rode `uv run radar avaliar validar` (0 erros) e marque `"conferido": true`,
   `"conferido_por": "<nome>"`, `"conferido_em": "<AAAA-MM-DD>"`.
4. Uma semana depois, reanote 3 editais sem olhar o gabarito e anote a concordância em
   `observacoes` (consistência do anotador).

## Rodar

- `uv run radar avaliar rodar` (replay, modelo padrão, todos os conjuntos).
- `RADAR_IA_PROVEDOR=bedrock uv run radar avaliar rodar --modo gravar --modelo <id> --teto-usd 1.0`
  (chama o Bedrock com o perfil AWS da máquina; commite `respostas/`).
