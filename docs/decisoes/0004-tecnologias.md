# 0004. Conjunto de tecnologias

## Contexto
É preciso fixar as ferramentas de Python, testes, banco, HTTP, execução e CI, de modo que o estudo se concentre na lógica incremental.

## Opções
- **uv, ruff, mypy, pytest, Alembic, httpx com tenacity, Pydantic v2, typer.**
- Prefect ou Dagster para orquestração.
- pip ou poetry para dependências.

## Escolha
- Python 3.13 e uv; ruff (lint e formatação) e mypy.
- pytest, com testcontainers e Postgres real nos testes de integração.
- Postgres 17 no docker compose; psycopg 3; Alembic para migrações.
- httpx e tenacity, com retry explícito em 500, 502, 503, 504, 429 e timeout.
- Pydantic v2 para modelos de entrada; typer para comandos, agendados pelo Agendador de Tarefas do Windows.
- Logs em JSON estruturado desde o início (OpenTelemetry no tema 3).
- GitHub Actions: ruff, mypy, import-linter, pytest; import-linter para as fronteiras.

## Trade-off
São ferramentas atuais. Um orquestrador como Prefect ou Dagster esconderia a lógica incremental (janelas, retry, registro de execução), que é justamente o conteúdo do tema 1, então ficou de fora. Em troca, o agendamento é mais rudimentar. uv substitui pip e poetry por ser mais rápido e unificar Python e dependências.
