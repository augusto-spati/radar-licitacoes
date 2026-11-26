from datetime import date
from unittest.mock import patch

import httpx
import pytest

from radar.coleta.pncp import ClientePNCP, ErroPNCP, Pagina

CORPO_OK = {
    "data": [{"numeroControlePNCP": "1-1-1/2026"}, {"numeroControlePNCP": "2-1-1/2026"}],
    "totalRegistros": 2,
    "totalPaginas": 1,
    "numeroPagina": 1,
    "paginasRestantes": 0,
    "empty": False,
}


def cliente_com(respostas: list[httpx.Response]) -> tuple[ClientePNCP, list[httpx.Request]]:
    chamadas: list[httpx.Request] = []
    fila = iter(respostas)

    def tratar(requisicao: httpx.Request) -> httpx.Response:
        chamadas.append(requisicao)
        return next(fila)

    http = httpx.Client(
        base_url="https://pncp.gov.br/api/consulta/v1", transport=httpx.MockTransport(tratar)
    )
    return ClientePNCP(http, tentativas=4, espera_base=0), chamadas


def buscar(cliente: ClientePNCP) -> Pagina:
    return cliente.buscar("publicacao", date(2026, 11, 1), date(2026, 11, 1), 6, 2)


def test_monta_parametros() -> None:
    cliente, chamadas = cliente_com([httpx.Response(200, json=CORPO_OK)])
    buscar(cliente)
    requisicao = chamadas[0]
    assert requisicao.url.path == "/api/consulta/v1/contratacoes/publicacao"
    assert dict(requisicao.url.params) == {
        "dataInicial": "20261101",
        "dataFinal": "20261101",
        "codigoModalidadeContratacao": "6",
        "pagina": "2",
        "tamanhoPagina": "50",
    }


def test_endpoint_atualizacao() -> None:
    cliente, chamadas = cliente_com([httpx.Response(200, json=CORPO_OK)])
    cliente.buscar("atualizacao", date(2026, 11, 1), date(2026, 11, 2), 6, 1)
    assert chamadas[0].url.path.endswith("/contratacoes/atualizacao")


def test_endpoint_desconhecido() -> None:
    cliente, chamadas = cliente_com([])
    with pytest.raises(ValueError):
        cliente.buscar("outro", date(2026, 11, 1), date(2026, 11, 1), 6, 1)
    assert chamadas == []


def test_retenta_500_e_depois_sucesso() -> None:
    cliente, chamadas = cliente_com(
        [httpx.Response(500), httpx.Response(500), httpx.Response(200, json=CORPO_OK)]
    )
    pagina = buscar(cliente)
    assert len(chamadas) == 3
    assert len(pagina.registros) == 2
    assert pagina.total_paginas == 1
    assert pagina.numero_pagina == 1
    assert pagina.bruto == CORPO_OK


def test_retenta_429() -> None:
    cliente, chamadas = cliente_com(
        [
            httpx.Response(429, html="<html>Too Many Requests</html>"),
            httpx.Response(200, json=CORPO_OK),
        ]
    )
    pagina = buscar(cliente)
    assert len(chamadas) == 2
    assert len(pagina.registros) == 2


def test_esgota_tentativas() -> None:
    cliente, chamadas = cliente_com([httpx.Response(500)] * 4)
    with pytest.raises(ErroPNCP) as erro:
        buscar(cliente)
    assert erro.value.status == 500
    assert len(chamadas) == 4


def test_nao_retenta_400() -> None:
    cliente, chamadas = cliente_com([httpx.Response(400, text="ruim"), httpx.Response(200)])
    with pytest.raises(ErroPNCP) as erro:
        buscar(cliente)
    assert erro.value.status == 400
    assert len(chamadas) == 1


def test_retenta_timeout() -> None:
    estado = {"n": 0}

    def tratar(requisicao: httpx.Request) -> httpx.Response:
        estado["n"] += 1
        if estado["n"] == 1:
            raise httpx.ReadTimeout("lento", request=requisicao)
        return httpx.Response(200, json=CORPO_OK)

    http = httpx.Client(base_url="https://x/api", transport=httpx.MockTransport(tratar))
    pagina = buscar(ClientePNCP(http, espera_base=0))
    assert estado["n"] == 2
    assert len(pagina.registros) == 2


def test_timeout_esgotado_levanta_erro_sem_status() -> None:
    def tratar(requisicao: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=requisicao)

    http = httpx.Client(base_url="https://x/api", transport=httpx.MockTransport(tratar))
    with pytest.raises(ErroPNCP) as erro:
        buscar(ClientePNCP(http, tentativas=2, espera_base=0))
    assert erro.value.status is None


def test_204_e_pagina_vazia() -> None:
    cliente, chamadas = cliente_com([httpx.Response(204)])
    pagina = buscar(cliente)
    assert pagina == Pagina(registros=[], total_paginas=0, numero_pagina=2, bruto={})
    assert len(chamadas) == 1


@pytest.mark.parametrize(
    "corpo",
    [
        {"message": "erro"},
        {"data": []},
        {"data": [], "totalPaginas": "3"},
        {"data": None, "totalPaginas": 1},
        {"data": {}, "totalPaginas": 1},
        [],
    ],
)
def test_200_malformado_vira_erro(corpo: object) -> None:
    cliente, chamadas = cliente_com([httpx.Response(200, json=corpo)] * 4)
    with pytest.raises(ErroPNCP) as erro:
        buscar(cliente)
    assert erro.value.status == 200
    assert len(chamadas) == 4  # retentado até esgotar


def test_200_html_vira_erro() -> None:
    cliente, _ = cliente_com([httpx.Response(200, html="<html>manutencao</html>")] * 4)
    with pytest.raises(ErroPNCP):
        buscar(cliente)


def test_200_malformado_e_retentado() -> None:
    cliente, chamadas = cliente_com(
        [httpx.Response(200, json={"message": "erro"}), httpx.Response(200, json=CORPO_OK)]
    )
    assert len(buscar(cliente).registros) == 2
    assert len(chamadas) == 2


def test_espera_minima_apos_429() -> None:
    esperas: list[float] = []
    respostas = iter([httpx.Response(429, html="x"), httpx.Response(200, json=CORPO_OK)])
    http = httpx.Client(
        base_url="https://x/api", transport=httpx.MockTransport(lambda r: next(respostas))
    )
    with patch("tenacity.nap.time.sleep", side_effect=esperas.append):
        ClientePNCP(http, espera_base=0.5).buscar(
            "publicacao", date(2026, 11, 1), date(2026, 11, 1), 6, 1
        )
    assert len(esperas) == 1
    assert esperas[0] >= 0.5 * 12


def test_espera_apos_500_nao_tem_piso_de_429() -> None:
    esperas: list[float] = []
    respostas = iter([httpx.Response(500), httpx.Response(200, json=CORPO_OK)])
    http = httpx.Client(
        base_url="https://x/api", transport=httpx.MockTransport(lambda r: next(respostas))
    )
    with patch("tenacity.nap.time.sleep", side_effect=esperas.append):
        ClientePNCP(http, espera_base=0.5).buscar(
            "publicacao", date(2026, 11, 1), date(2026, 11, 1), 6, 1
        )
    assert esperas[0] < 0.5 * 12
