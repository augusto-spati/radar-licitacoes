from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from radar.comum.config import Config


class Base(DeclarativeBase):
    pass


def criar_engine(config: Config) -> Engine:
    return create_engine(config.database_url, echo=config.log_sql)


def fabrica_sessao(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
