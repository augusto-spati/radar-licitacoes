# Vídeo 8: o painel ("o que eu preciso decidir hoje?")

Roteiro em tópicos. Material de apoio: `docs/etapas/08-painel.md`.

## Gancho (30 s)

- Pergunta: "Tenho 30 editais abertos. Qual eu preciso decidir hoje?"
- Promessa: mostrar a tela que responde, e as armadilhas de data, de "novo" e de aderência que quase a deixaram mentir.

## Por que 20 dias (2 min)

- Lei 14.133, art. 55: obra tem de 10 a 60 dias úteis até a proposta; atestado, CAT, garantia e visita técnica levam semanas.
- Decidir com 7 dias é tarde. Decidir = sem triagem e encerra em até 20 dias, contados em dias de calendário de Brasília (23:59 do 20º dia entra, 00:00 do 21º não).
- Contador e Lista filtrada usam a mesma consulta: dão o mesmo número.

## O selo "novo" sem carimbo de visita (3 min)

- Ideia ingênua: "novos desde a sua última visita". Quebra com dois usuários e dois aparelhos.
- Solução: `primeiro_visto_em`, gravado só na inserção; o upsert não o toca. Mostrar o teste.
- Backfill com data-sentinela: o que já existia antes da migração não vira "novo".

## Perfil de exemplo sem aderência falsa (2 min)

- O repositório é público: o perfil é de exemplo. Mostrar um selo "apto" nele seria inventar.
- Sem perfil real, o Painel diz "Aderência indisponível" uma vez, não esconde inapto de Decidir e o contador não filtra por veredito.

## A faixa que só aparece quando algo falhou (2 min)

- Fonte parada há mais de 36 h, nunca rodou ou última leitura em falha. Fonte fresca não aparece: aviso permanente vira paisagem.
- A API não devolve o texto cru do erro (pode ter URL e detalhe interno).

## A tela (2 min)

- Abrir `/`: contadores que levam à Lista filtrada, Decidir, Mais adiante recolhido.
- Abrir o link antigo `#/?linha=redes` e ver a Lista com o filtro. No celular, cabe em 390 px.

## Fechamento (30 s)

- Próximo: triagem compartilhada (08b), a primeira escrita da API, e a prioridade configurável (08c).
