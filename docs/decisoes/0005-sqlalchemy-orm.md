# 0005. SQLAlchemy 2.0 com ORM

## Contexto
É preciso escolher como o código fala com o Postgres, e SQL é uma lacuna que o projeto quer fechar.

## Opções
- SQL à mão com psycopg 3, recomendação do Claude.
- SQLAlchemy Core.
- **SQLAlchemy 2.0 com ORM.**

## Escolha
SQLAlchemy 2.0 com ORM, escolha do Augusto contra a recomendação do Claude. O motivo não foi registrado.

## Trade-off
O ORM esconde o SQL, o que enfraquece o objetivo de aprender SQL. Para compensar, ficou combinado: log do SQL gerado em testes e desenvolvimento; upsert com `insert().on_conflict_do_update` do dialeto Postgres; e `EXPLAIN ANALYZE` antes e depois de cada índice, registrado na nota da etapa. O ganho é menos código repetitivo de mapeamento e um modelo comum em vagas.
