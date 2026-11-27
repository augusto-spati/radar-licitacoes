import httpx
import pytest

from radar.coleta import ClienteResultadosPNCP, ErroResultados, criar_http_resultados

CNPJ = "12345678000199"
BASE = f"/pncp-api/v1/orgaos/{CNPJ}/compras/2026/7/itens"


def _cliente(handler):
    http = httpx.Client(
        base_url="https://pncp.gov.br/pncp-api/v1", transport=httpx.MockTransport(handler)
    )
    return ClienteResultadosPNCP(http, pausa_s=0, tentativas=3, espera_base=0)


def test_pagina_120_itens_sem_repetir():
    paginas = []

    def handler(req):
        pagina = int(req.url.params["pagina"])
        paginas.append(pagina)
        assert req.url.params["tamanhoPagina"] == "50"
        ini = (pagina - 1) * 50
        fim = min(ini + 50, 120)
        return httpx.Response(
            200, json=[{"numeroItem": n, "temResultado": False} for n in range(ini + 1, fim + 1)]
        )

    itens = _cliente(handler).itens_com_resultados(CNPJ, 2026, 7)
    assert [i["numeroItem"] for i in itens] == list(range(1, 121))
    assert paginas == [1, 2, 3]


def test_http_do_projeto():
    http = criar_http_resultados()
    assert http.headers["User-Agent"].startswith("radar-licitacoes-estudo")
    assert str(http.base_url).startswith("https://pncp.gov.br/pncp-api/v1")
    http.close()


def test_resultados_204_vira_lista_vazia_e_item_sem_resultado_nao_chama():
    chamadas = []

    def handler(req):
        chamadas.append(req.url.path)
        if req.url.path.endswith("/itens"):
            return httpx.Response(
                200,
                json=[
                    {"numeroItem": 1, "temResultado": True},
                    {"numeroItem": 2, "temResultado": False},
                ],
            )
        return httpx.Response(204)

    itens = _cliente(handler).itens_com_resultados(CNPJ, 2026, 7)
    assert itens[0]["resultados"] == []
    assert "resultados" not in itens[1]
    assert chamadas == [BASE, f"{BASE}/1/resultados"]


def test_resultados_com_dados():
    def handler(req):
        if req.url.path.endswith("/itens"):
            return httpx.Response(200, json=[{"numeroItem": 3, "temResultado": True}])
        return httpx.Response(200, json=[{"niFornecedor": "1", "valorTotalHomologado": 9.5}])

    itens = _cliente(handler).itens_com_resultados(CNPJ, 2026, 7)
    assert itens[0]["resultados"][0]["valorTotalHomologado"] == 9.5


def test_429_seguido_de_200_tenta_de_novo():
    estados = iter([429, 200])

    def handler(req):
        if next(estados) == 429:
            return httpx.Response(429)
        return httpx.Response(200, json=[{"numeroItem": 1}])

    assert len(_cliente(handler).itens_com_resultados(CNPJ, 2026, 7)) == 1


def test_404_nos_itens_devolve_lista_vazia():
    assert _cliente(lambda r: httpx.Response(404)).itens_com_resultados(CNPJ, 2026, 7) == []


def test_500_tres_vezes_levanta_erro():
    n = []

    def handler(req):
        n.append(1)
        return httpx.Response(500)

    with pytest.raises(ErroResultados):
        _cliente(handler).itens_com_resultados(CNPJ, 2026, 7)
    assert len(n) == 3


def test_4xx_levanta_erro_sem_retentar():
    n = []

    def handler(req):
        n.append(1)
        return httpx.Response(400, text="ruim")

    with pytest.raises(ErroResultados):
        _cliente(handler).itens_com_resultados(CNPJ, 2026, 7)
    assert len(n) == 1


def test_transporte_esgota_tentativas():
    def handler(req):
        raise httpx.ConnectError("caiu")

    with pytest.raises(ErroResultados):
        _cliente(handler).itens_com_resultados(CNPJ, 2026, 7)
