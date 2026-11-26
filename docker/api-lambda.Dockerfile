# Imagem da API na Lambda. Build a partir da raiz: docker build -f docker/api-lambda.Dockerfile .
# boto3 não é dependência do projeto: vem na imagem base da Lambda (lido só no SSM).
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS requisitos
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv export --no-dev --frozen --no-hashes --no-emit-project -o /requirements.txt

FROM public.ecr.aws/lambda/python:3.13
COPY --from=requisitos /requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY src/ ${LAMBDA_TASK_ROOT}/
COPY config/ ${LAMBDA_TASK_ROOT}/config/
ENV RADAR_DB_NULLPOOL=true \
    RADAR_CONFIG_FILTRO=/var/task/config/filtro.toml
CMD ["radar.api.lambda_handler.handler"]
