import pytest

from radar.coleta import ClienteEducado, criar_http_educado
from radar.coleta.dae_jundiai import (
    BASE,
    GRADE,
    URL_LISTA,
    FonteDaeJundiai,
    dados_postback,
    ler_detalhe,
    ler_lista,
)


@pytest.mark.rede
def test_dae_jundiai_real_pagina_1_postback_da_2_e_um_detalhe() -> None:
    fonte = FonteDaeJundiai()
    with ClienteEducado(
        criar_http_educado(), marcador_bloqueio=fonte.marcador_bloqueio, intervalo_s=3.0
    ) as c:
        html = c.get(URL_LISTA).text
        p1 = ler_lista(html)
        assert p1.linhas and 2 in p1.paginas

        p2 = ler_lista(c.postback_leitura(URL_LISTA, dados_postback(html, GRADE, "Page$2")).text)
        assert p2.pagina_atual == 2
        assert {linha.cdcompra for linha in p2.linhas}.isdisjoint(
            {linha.cdcompra for linha in p1.linhas}
        )

        ne = next(linha for linha in p1.linhas if linha.sistema == "NE")
        detalhe = ler_detalhe(c.get(f"{BASE}{ne.caminho}").text, ne.cdcompra)
        assert ne.numero.split("/")[0] in detalhe.titulo
        assert detalhe.publicacao is not None
