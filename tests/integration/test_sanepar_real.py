import pytest

from radar.coleta import ClienteEducado, criar_http_educado
from radar.coleta.sanepar import BASE, FonteSanepar, ler_detalhe, ler_lista


@pytest.mark.rede
def test_sanepar_real_pagina_0_e_um_detalhe() -> None:
    fonte = FonteSanepar()
    with ClienteEducado(criar_http_educado(), marcador_bloqueio=fonte.marcador_bloqueio) as c:
        lista = ler_lista(c.get(f"{BASE}/licitacoes?Tipo=OE&Situacao=Abertura&page=0").text)
        assert lista.total > 0
        assert lista.linhas
        primeira = lista.linhas[0]
        assert primeira.numero and primeira.data_abertura is not None

        detalhe = ler_detalhe(c.get(f"{BASE}{primeira.caminho_detalhe}").text)
        assert primeira.numero in detalhe.titulo
        assert detalhe.objeto
        assert detalhe.propostas is not None or detalhe.data_abertura is not None
