from datetime import date, timedelta

import pytest

from radar.coleta.pncp import ClientePNCP, criar_http


def ultimo_dia_util() -> date:
    dia = date.today() - timedelta(days=1)
    while dia.weekday() >= 5:
        dia -= timedelta(days=1)
    return dia


@pytest.mark.rede
def test_pncp_real_publicacao() -> None:
    dia = ultimo_dia_util()
    with criar_http() as http:
        pagina = ClientePNCP(http).buscar("publicacao", dia, dia, 6, 1)
    assert pagina.total_paginas > 0
    assert "numeroControlePNCP" in pagina.registros[0]
