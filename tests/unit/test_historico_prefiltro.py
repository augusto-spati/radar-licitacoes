from datetime import date

import httpx

from radar.coleta import ClientePNCP, meses_ate, prefiltrar

CORPO = {"data": [], "totalRegistros": 0, "totalPaginas": 1, "numeroPagina": 1}


def test_prefiltrar_ignora_acento_e_caixa() -> None:
    registros = [
        {"objetoCompra": "Execução de REDE DE ÁGUA"},
        {"objetoCompra": "Instalação de rede lógica"},
        {"objetoCompra": None},
        {},
    ]
    assert prefiltrar(registros, ["rede de agua"]) == [registros[0]]


def _casa(objeto: str, termo: str) -> bool:
    return prefiltrar([{"objetoCompra": objeto}], [termo]) != []


def test_prefiltrar_aceita_plurais() -> None:
    assert _casa("Implantação de REDES DE ÁGUA", "rede de agua")
    assert _casa("Ligações de esgoto", "ligacao de esgoto")
    assert _casa("Serviços de religações", "religacao")
    assert _casa("Execução de   ligação  de\nágua", "ligacao de agua")
    assert _casa("Leitura de hidrômetros e medidores", "leitura de hidrometro")
    assert _casa("Leitura de medidores", "leitura de medidores")
    assert _casa("Aquisição de materiais hidráulicos", "material")


def test_prefiltrar_continua_recusando_o_que_nao_casa() -> None:
    assert not _casa("Instalação de rede lógica", "rede de agua")
    assert not _casa("Compra de papel", "religacao")


def test_meses_ate_exclui_o_mes_corrente() -> None:
    assert meses_ate(date(2026, 11, 27), 2) == [date(2026, 10, 1), date(2026, 9, 1)]


def test_meses_ate_atravessa_o_ano() -> None:
    assert meses_ate(date(2026, 2, 3), 3) == [
        date(2026, 1, 1),
        date(2025, 12, 1),
        date(2025, 11, 1),
    ]


def _consultas(**extra: str) -> dict[str, str]:
    vistas: list[httpx.Request] = []

    def tratar(req: httpx.Request) -> httpx.Response:
        vistas.append(req)
        return httpx.Response(200, json=CORPO)

    http = httpx.Client(
        base_url="https://pncp.gov.br/api/consulta/v1", transport=httpx.MockTransport(tratar)
    )
    ClientePNCP(http, espera_base=0).buscar(
        "publicacao", date(2026, 10, 1), date(2026, 10, 31), 6, 1, **extra
    )
    return dict(vistas[0].url.params)


def test_uf_so_vai_na_consulta_quando_dada() -> None:
    assert "uf" not in _consultas()
    assert _consultas(uf="PR")["uf"] == "PR"
