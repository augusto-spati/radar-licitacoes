# Imagem do coletor (Fargate). Build a partir da raiz: docker build -f docker/coletor.Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS requisitos
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv export --no-dev --frozen --no-hashes --no-emit-project -o /requirements.txt

FROM python:3.13-slim
WORKDIR /app
COPY --from=requisitos /requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY pyproject.toml README.md /app/
COPY src/ /app/src/
RUN pip install --no-cache-dir --no-deps .
COPY config/ /app/config/
COPY migrations/ /app/migrations/
COPY alembic.ini /app/alembic.ini
# Sem PYTHONPATH: o pacote ja esta instalado no site-packages (pip install .), entao
# `radar` e os imports funcionam; config/ e migrations/ ficam em /app (cwd do alembic).
RUN useradd --system --no-create-home radar
USER radar
ENV RADAR_DB_NULLPOOL=true \
    RADAR_CONFIG_FILTRO=/app/config/filtro.toml     RADAR_CONFIG_SERVICOS=/app/config/servicos.toml
ENTRYPOINT ["radar"]
CMD ["atualizar"]
