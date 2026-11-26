# 0007. Front: React, Vite, TypeScript e Astryx, com API FastAPI

## Contexto
O projeto precisa de uma tela para mostrar os editais filtrados. O front não é foco do estudo.

## Opções
- **React + Vite + TypeScript + Astryx (beta, MIT), com backend FastAPI expondo JSON.**
- FastAPI com Jinja e HTMX.
- Streamlit.
- Next.js.

## Escolha
React, Vite, TypeScript e Astryx (github.com/meta/astryx), consumindo uma API JSON em FastAPI (OpenAPI).

## Trade-off
O front fica fora da trilha de estudo: é feito majoritariamente com IA, rápido, e o Astryx foi desenhado para agentes. Esta é uma exceção registrada: ele não conta como argumento de competência. O que o Augusto precisa saber defender é o contrato da API. Vite e não Next porque gera build estático, que o FastAPI serve agora e o S3 serve depois. O Astryx está em beta, então a versão fica travada.
