from pathlib import Path
from typing import Literal

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


def obter_config() -> Config:
    return Config()
