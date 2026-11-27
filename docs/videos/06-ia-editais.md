# Vídeo 6: IA nos editais, comparação com o perfil e evals

Roteiro em tópicos. Material de apoio: `docs/etapas/06-ia-editais.md`.

## Gancho (30 s)

- Pergunta: "Dá para um modelo de linguagem ler um edital de 80 páginas e dizer se a empresa pode participar, sem inventar nada?"
- Promessa: mostrar por que o modelo só extrai, como medir o acerto e quanto custa (centavos).

## Claude não, Nova Lite sim (3 min)

- A sonda do Bedrock: Claude deu `AccessDeniedException` nesta conta; perfil cross-region fica proibido no plano FREE.
- Modelos que respondem in-region em us-east-2: Nova Lite, gpt-oss-120b, qwen3-32b e ministral.
- Cada modelo aceita um modo de saída diferente (esquema JSON ou ferramenta forçada). Mostrar a tabela e o `config/ia.toml`.
- A interface `Provedor`: trocar de modelo é configuração.

## O LLM só extrai (4 min)

- Texto por página, trechos de 60.000 caracteres com 1 página de sobreposição.
- Achado = `{campo, valor, numero, unidade, trecho}`. Mostrar um achado de verdade.
- Ancoragem: o trecho tem de existir no texto; a página vem da posição, não do modelo.
- Junção: o valor mais restritivo vence e o campo ganha `conflito`.
- Comparação com o perfil: função pura, uma regra por campo, veredito `apto | inapto | verificar`.
- Limite honesto: exigência que a extração perdeu vira `apto` falso; por isso o recall importa.

## Custo em centavos (2 min)

- Cache por hash, teto mensal próprio, só editais abertos que casam com o filtro.
- Rodada real na nuvem: 1 edital, Nova Lite, US$ 0,0046, veredito `verificar`. Agenda das 09:00 ligada.

## Evals (4 min)

- Gabarito por edital, 12 editais, 8 `ajuste` e 4 `teste`.
- O Claude faz o rascunho; só o Augusto confere. Mostrar o relatório com a seção "rascunho não conferido: métrica sem valor".
- Precisão, recall e alucinação por campo. Replay sem rede no CI protege ancoragem, normalização e junção.
- Dizer o que falta: com os gabaritos não conferidos, ainda não há número de acerto para mostrar.

## O que a revisão ensinou (4 min)

1. Transação aberta durante o Bedrock apagaria respostas pagas no rollback. Conexão própria, commit na hora.
2. Número sem âncora no trecho podia gerar `inapto` falso e abria porta para prompt injection. Trecho de 20 caracteres ou mais, número dentro do trecho, texto em `<edital>`.
3. CPF e RG de servidores nos textos do corpus: mascarados.
4. O limite de extração que o pypdf engolia com `except Exception`: a exceção passou a herdar de `BaseException`.

## Perguntas (1 min)

Deixar na tela as cinco de `docs/etapas/06-ia-editais.md`.

## Fecho

- Entregue: extração de exigências com saída estruturada, comparação determinística, provedor trocável, cache, teto de custo e evals com replay.
- Limite honesto: sem gabarito conferido, a precisão e o recall ainda não valem.
- Próximo: o Augusto confere os gabaritos, depois a rodada real comparando modelos; a tela das exigências fica para a 6b.
