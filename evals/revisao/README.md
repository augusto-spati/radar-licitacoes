# Revisão cruzada dos gabaritos (2026-11-27)

Os 12 gabaritos eram rascunho por busca de palavras-chave. Passaram por três leituras, todas do
Claude (Opus), sem usar o modelo avaliado (Amazon Nova Lite) nem nenhuma API de IA:

1. **Rascunho** (já existente).
2. **Leitura cega:** 4 agentes leram 3 editais cada, inteiros, sem ver o rascunho.
3. **Juiz adversarial:** 4 agentes novos, com os editais embaralhados. Decidiram cada divergência
   no texto, tentaram derrubar o que as duas leituras concordavam e procuraram o que ambas
   deixaram passar.

Continuam `conferido: false`: só a conferência humana vale para a métrica. Cada arquivo
`<slug>.md` desta pasta mostra o que mudou em relação ao rascunho (página e trecho) e o que
fica para o Augusto decidir.

## O que a revisão achou

- O rascunho deixava passar exigência fora da seção de habilitação: garantia contratual (119),
  garantia de proposta (017), certidões de falência e municipais, visita técnica (335), prazos de
  entrega e declarações. Também tinha um trecho errado: o de registro no conselho do 984 citava a CAT.
- O 677 estava sem texto: o edital vinha num ZIP com o nome "Pregão Eletrônico nº 034.2026 ...",
  sem a palavra "edital". A seleção de pacotes passou a reconhecer o nome da modalidade, e o
  gabarito foi feito do zero sobre as 74 páginas.
- Anexo citado e não publicado (FORA DO TEXTO): o TR do 124 e do 984 e o quadro de quantitativos
  do 119. O gabarito cobre só o texto que a IA recebe.

## Critérios gerais (decididos na revisão; confirmar)

1. `prazo_execucao` = prazo de execução ou de entrega do objeto, nunca a vigência do contrato ou
   da ata. Registro de preços sem prazo de entrega → null.
2. Atestado: quantidade só quando o número está escrito. "50% da execução pretendida" fica null,
   com o número derivado na observação.
3. Capital social ou patrimônio líquido alternativos → os dois campos preenchidos.
4. CAT do responsável técnico sem quantidade → atestado com quantidade null.
5. `declaracao_menor` entra quando exigida em qualquer etapa, inclusive como declaração no sistema.
6. Inscrição no CNPJ e o que não tem categoria → `outro`.
7. Anexo não publicado → nada inventado; observação "FORA DO TEXTO".
8. **Em aberto:** edital com ME/EPP misto (itens ampla, cota e exclusivos; caso do 017). O
   gabarito usa `cota`; a regra de precedência do pipeline dá `exclusiva`.

Os critérios 1 e 8 tocam a definição dos campos no prompt (`src/radar/analise/prompts/extracao.md`),
que não diz se o prazo é a execução ou a vigência. Quando confirmados, o prompt deve dizer o mesmo.
