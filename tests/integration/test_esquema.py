from datetime import date

import pytest
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
