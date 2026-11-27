from alembic import context
from sqlalchemy import create_engine

from radar.analise import modelos as _analise_modelos  # noqa: F401  (registra as tabelas)
from radar.coleta import modelos as _coleta_modelos  # noqa: F401  (registra as tabelas)
from radar.comum import Base, obter_config
from radar.editais import modelos as _editais_modelos  # noqa: F401  (registra as tabelas)

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    sobrescrita = config.attributes.get("url")
    if sobrescrita:
        return str(sobrescrita)
    return obter_config().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    try:
        with engine.connect() as conexao:
            context.configure(connection=conexao, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
