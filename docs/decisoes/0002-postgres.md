# 0002. Postgres como banco

## Contexto
SQL é uma lacuna declarada. O projeto precisa de transações, locks e índices reais para estudar carga incremental, além de busca de texto em português.

## Opções
- **Postgres.**
- SQLite.
- DynamoDB.
- OpenSearch.

## Escolha
Postgres 17, local no docker compose.

## Trade-off
Postgres dá transações, locks e índices reais (o que o livro *Designing Data-Intensive Applications* descreve), e reúne num banco só busca de texto em português, upsert, JSONB e pgvector para o tema de IA. Existe gerenciado na AWS. O custo é ter um serviço a subir (Docker) em vez de um arquivo, como seria no SQLite, e menos escala horizontal automática que o DynamoDB, que não é problema neste volume.
