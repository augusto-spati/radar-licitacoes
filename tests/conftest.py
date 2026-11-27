import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

RAIZ = Path(__file__).resolve().parent.parent

# O Ryuk (container de limpeza do testcontainers) costuma falhar no Docker Desktop do Windows.
if os.name == "nt":
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    with PostgresContainer("postgres:17", driver="psycopg") as postgres:
        url = postgres.get_connection_url()
        config_alembic = AlembicConfig(str(RAIZ / "alembic.ini"))
        config_alembic.set_main_option("script_location", str(RAIZ / "migrations"))
        config_alembic.attributes["url"] = url
        command.upgrade(config_alembic, "head")
        engine = create_engine(url)
        try:
            yield engine
        finally:
            engine.dispose()


@pytest.fixture(autouse=True)
def _sem_leases(request: pytest.FixtureRequest) -> Iterator[None]:
    """Apaga os leases de trava_fonte e o cache resposta_llm ao fim de todo teste com banco.

    A trava e o cache da IA commitam na própria conexão: o rollback da sessão de teste não os
    desfaz. Testes sem `engine`/`sessao` (os unitários) não sobem o container por causa disso.
    """
    yield
    if {"engine", "sessao"} & set(request.fixturenames):
        engine: Engine = request.getfixturevalue("engine")
        with engine.begin() as conexao:
            conexao.execute(text("DELETE FROM trava_fonte"))
            conexao.execute(text("DELETE FROM resposta_llm"))


@pytest.fixture
def sessao(engine: Engine) -> Iterator[Session]:
    """Sessão que desfaz tudo ao fim do teste, mesmo após commit ou IntegrityError."""
    with engine.connect() as conexao:
        transacao = conexao.begin()
        with Session(conexao, join_transaction_mode="create_savepoint") as sessao:
            yield sessao
        transacao.rollback()
