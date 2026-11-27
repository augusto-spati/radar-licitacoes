# radar-licitacoes

Projeto público de estudo. Coleta editais de licitação da API pública do PNCP, guarda em Postgres e filtra por linhas de serviço e estados. Só usa dado público.

Cada etapa trata um tema (dados, eventos, observabilidade, IA) e deixa a mudança no código, uma nota do que se aprendeu e material para vídeo.

## Como subir

Requisitos: Docker e [uv](https://docs.astral.sh/uv/).

```
docker compose up -d db
uv sync
uv run alembic upgrade head
uv run radar --help
```

O banco local é o Postgres 17, na porta 5432, com usuário, senha e banco `radar`.

## Comandos

- `radar carga-inicial`: planeja janelas de publicação dos últimos dias, coleta e processa.
- `radar atualizar`: planeja janelas de atualização (hoje e os dias de recuo), coleta e processa.
- `radar processar`: coleta as janelas pendentes e registra as páginas ainda não processadas.
- `radar auditar`: confere se todos os dias e modalidades esperados foram coletados (sai com 1 se não).
- `radar filtrar`: lista os editais que casam com os critérios de `config/filtro.toml`.

Rode a carga longa com o notebook sem entrar em suspensão. Rode `radar processar` de novo até `radar auditar` sair com 0.

`carga-inicial`, `atualizar` com o PNCP e `processar` coletam o PNCP no próprio processo. Não rode esses comandos contra o banco de produção: lá quem coleta o PNCP é a Lambda coletora, pelas mensagens do planejador (`radar planejar`). Se alguma janela tiver lease vivo (alguém coletando agora), eles saem com 2 sem coletar nada; `radar atualizar --fontes sanepar` continua valendo. Com `RADAR_FILA=local`, o relay desses comandos só publica as mensagens que a fila local consome: uma `coletar_janela` pendente fica para a Lambda, sem contar falha.

## Checagens

```
uv run pytest
uv run ruff check .
uv run mypy src
uv run lint-imports
```

## Decisões

As escolhas de tecnologia e arquitetura estão em [docs/decisoes/](docs/decisoes/).
