from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from radar.comum.config import Config


class Base(DeclarativeBase):
    pass


def criar_engine(config: Config) -> Engine:
    if config.db_nullpool:
        return create_engine(config.database_url, echo=config.log_sql, poolclass=NullPool)
    return create_engine(config.database_url, echo=config.log_sql, pool_pre_ping=True)


def fabrica_sessao(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
