from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RADAR_")

    database_url: str = "postgresql+psycopg://radar:radar@localhost:5432/radar"
    log_sql: bool = False


def obter_config() -> Config:
    return Config()
