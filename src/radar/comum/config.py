import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/radar/comum/config.py -> raiz do repositório: o padrão não depende do cwd.
RAIZ_REPOSITORIO = Path(__file__).resolve().parents[3]


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RADAR_")

    database_url: str = "postgresql+psycopg://radar:radar@localhost:5432/radar"
    log_sql: bool = False
    # Critérios do filtro (linhas, UFs, modalidades). Na Lambda, aponte para o arquivo do pacote.
    config_filtro: Path = RAIZ_REPOSITORIO / "config" / "filtro.toml"
    # Serverless + Neon: sem pool no processo (o pooler do Neon cuida das conexões).
    db_nullpool: bool = False
    # Fila das mensagens da outbox: local (consome no processo) ou SQS.
    fila: Literal["local", "sqs"] = "local"
    fila_url: str | None = None
    fila_falhas_url: str | None = None
    # Fila das janelas do PNCP (coletar_janela) e a DLQ dela.
    fila_janelas_url: str | None = None
    fila_janelas_falhas_url: str | None = None
    # Observabilidade: traces (X-Ray por OTLP) e métricas (EMF na Lambda, PutMetricData no
    # Fargate). Desligadas por padrão: notebook, CI e testes não falam com a AWS.
    telemetria: Literal["desligada", "xray"] = "desligada"
    metricas: Literal["desligada", "emf", "cloudwatch"] = "desligada"
    traces_fracao: float = Field(default=1.0, ge=0.0, le=1.0)


def obter_config() -> Config:
    return Config()


def regiao_aws() -> str:
    """Região do processo (Lambda e ECS definem `AWS_REGION`); padrão, a do projeto."""
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-2"
