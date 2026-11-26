from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from radar.coleta.modelos import ColetaJanela


def test_migracao_cria_tabelas(engine: Engine) -> None:
    tabelas = set(inspect(engine).get_table_names())

    assert {"coleta_janela", "pagina_bruta", "edital"} <= tabelas


def _janela() -> ColetaJanela:
    return ColetaJanela(
        endpoint="publicacao",
        data_ini=date(2026, 1, 1),
        data_fim=date(2026, 1, 7),
        modalidade=6,
        status="pendente",
        paginas=0,
        registros=0,
    )


def test_janela_unica(sessao: Session) -> None:
    sessao.add(_janela())
    sessao.flush()

    sessao.add(_janela())
    with pytest.raises(IntegrityError):
        sessao.flush()


RAIZ = Path(__file__).resolve().parents[2]


def test_modelos_e_migracoes_sem_diferencas(engine: Engine) -> None:

    config = AlembicConfig(str(RAIZ / "alembic.ini"))
    config.set_main_option("script_location", str(RAIZ / "migrations"))
    config.attributes["url"] = engine.url.render_as_string(hide_password=False)

    command.check(config)
