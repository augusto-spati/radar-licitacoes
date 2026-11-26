# radar-licitacoes

Projeto público de estudo. Coleta editais de licitação da API pública do PNCP, guarda em Postgres e filtra por linhas de serviço e estados. Só usa dado público.

Cada etapa trata um tema (dados, eventos, observabilidade, IA) e deixa a mudança no código, uma nota do que se aprendeu e material para vídeo.

## Como subir

Requisitos: Docker e [uv](https://docs.astral.sh/uv/).

```
docker compose up -d db
uv sync
uv run radar --help
```

O banco local é o Postgres 17, na porta 5432, com usuário, senha e banco `radar`.

## Checagens

```
uv run pytest
uv run ruff check .
uv run mypy src
uv run lint-imports
```

## Decisões

As escolhas de tecnologia e arquitetura estão em [docs/decisoes/](docs/decisoes/).
