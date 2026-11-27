import httpx
import pytest

from radar.analise.arquivos_pncp import (
    ArquivoPNCP,
    ClienteArquivosPNCP,
    ErroArquivos,
    GrandeDemais,
    chave_pncp,
    criar_http_arquivos,
    escolher_documentos,
)

BASE = "https://pncp.gov.br/pncp-api/v1"


def _doc(seq: int, tipo: str, ativo: bool = True) -> dict[str, object]:
    url = f"{BASE}/orgaos/78200482000110/compras/2026/298/arquivos/{seq}"
    return {
        "uri": url,
        "url": url,
        "cnpj": "78200482000110",
        "anoCompra": 2026,
        "sequencialCompra": 298,
        "sequencialDocumento": seq,
        "statusAtivo": ativo,
        "titulo": f"documento {seq}.pdf",
        "tipoDocumentoId": 2,
        "tipoDocumentoNome": tipo,
    }


def _cliente(respostas: list[httpx.Response]) -> tuple[ClienteArquivosPNCP, list[httpx.Request]]:
    chamadas: list[httpx.Request] = []
    fila = iter(respostas)

    def tratar(requisicao: httpx.Request) -> httpx.Response:
        chamadas.append(requisicao)
        return next(fila)

    http = httpx.Client(base_url=BASE, transport=httpx.MockTransport(tratar))
    return ClienteArquivosPNCP(http, pausa_s=0, espera_base=0), chamadas


def test_chave_pncp() -> None:
    assert chave_pncp("78200482000110-1-000298/2026") == ("78200482000110", 2026, 298)
    assert chave_pncp("sanepar-123") is None
    assert chave_pncp("78200482000110-1-000298") is None


def test_listar_le_documentos() -> None:
    cliente, chamadas = _cliente(
        [httpx.Response(200, json=[_doc(1, "Edital"), _doc(2, "Anexo", ativo=False)])]
    )
    arquivos = cliente.listar("78200482000110", 2026, 298)
    assert chamadas[0].url.path == "/pncp-api/v1/orgaos/78200482000110/compras/2026/298/arquivos"
    assert arquivos == [
        ArquivoPNCP(
            sequencial=1,
            titulo="documento 1.pdf",
            tipo_nome="Edital",
            url=f"{BASE}/orgaos/78200482000110/compras/2026/298/arquivos/1",
        )
    ]


def test_listar_204_vazio() -> None:
    cliente, _ = _cliente([httpx.Response(204)])
    assert cliente.listar("1", 2026, 1) == []


def test_listar_corpo_inesperado_falha() -> None:
    cliente, _ = _cliente([httpx.Response(200, json={"erro": "x"})])
    with pytest.raises(ErroArquivos):
        cliente.listar("1", 2026, 1)


def test_429_tenta_de_novo_e_depois_falha() -> None:
    cliente, chamadas = _cliente([httpx.Response(429)] * 3)
    with pytest.raises(ErroArquivos):
        cliente.listar("1", 2026, 1)
    assert len(chamadas) == 3


def test_500_depois_200_funciona() -> None:
    cliente, chamadas = _cliente([httpx.Response(503), httpx.Response(200, json=[])])
    assert cliente.listar("1", 2026, 1) == []
    assert len(chamadas) == 2


def test_404_falha_sem_tentar_de_novo() -> None:
    cliente, chamadas = _cliente([httpx.Response(404)])
    with pytest.raises(ErroArquivos):
        cliente.listar("1", 2026, 1)
    assert len(chamadas) == 1


def test_baixar_devolve_bytes() -> None:
    cliente, _ = _cliente([httpx.Response(200, content=b"%PDF-1.7 abc")])
    assert cliente.baixar(f"{BASE}/x/1") == b"%PDF-1.7 abc"


def test_baixar_corta_no_limite() -> None:
    cliente, _ = _cliente([httpx.Response(200, content=b"x" * 30)])
    with pytest.raises(GrandeDemais):
        cliente.baixar(f"{BASE}/x/1", limite_bytes=10)


def test_baixar_transporte_falha_3_vezes() -> None:
    chamadas: list[httpx.Request] = []

    def tratar(requisicao: httpx.Request) -> httpx.Response:
        chamadas.append(requisicao)
        raise httpx.ConnectError("sem rede")

    http = httpx.Client(base_url=BASE, transport=httpx.MockTransport(tratar))
    cliente = ClienteArquivosPNCP(http, pausa_s=0, espera_base=0)
    with pytest.raises(ErroArquivos):
        cliente.baixar(f"{BASE}/x/1")
    assert len(chamadas) == 3


def test_criar_http_arquivos_tem_user_agent() -> None:
    with criar_http_arquivos() as http:
        assert "radar-licitacoes" in http.headers["User-Agent"]
        assert str(http.base_url).startswith(BASE)
        assert http.timeout.read == 120


def _arquivo(seq: int, tipo: str) -> ArquivoPNCP:
    return ArquivoPNCP(sequencial=seq, titulo=f"d{seq}", tipo_nome=tipo, url=f"u{seq}")


def test_escolher_documentos() -> None:
    arquivos = [
        _arquivo(5, "Edital"),
        _arquivo(2, "Termo de Referência"),
        _arquivo(3, "Planilha"),
        _arquivo(1, "Edital"),
        _arquivo(4, "Edital"),
    ]
    escolhidos = escolher_documentos(arquivos)
    assert [a.sequencial for a in escolhidos] == [1, 2, 4]


def test_compra_le_valor_estimado() -> None:
    cliente, chamadas = _cliente([httpx.Response(200, json={"valorTotalEstimado": 1234.5})])
    assert cliente.compra("78200482000110", 2026, 298)["valorTotalEstimado"] == 1234.5
    assert str(chamadas[0].url) == (
        "https://pncp.gov.br/api/consulta/v1/orgaos/78200482000110/compras/2026/298"
    )


def test_301_e_erro() -> None:
    cliente, _ = _cliente([httpx.Response(301, json={"status": "301"})])
    with pytest.raises(ErroArquivos):
        cliente.listar("1", 2026, 1)


def test_baixar_estoura_prazo_total_mesmo_recebendo_aos_poucos() -> None:
    # Servidor que pinga bytes devagar: o timeout de leitura nunca dispara, o prazo total sim.
    instantes = iter(float(t) for t in range(0, 10_000, 100))

    def relogio() -> float:
        return next(instantes)

    def aos_poucos() -> object:
        for _ in range(10):
            yield b"x" * 10

    http = httpx.Client(
        base_url=BASE,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=aos_poucos())),
    )
    cliente = ClienteArquivosPNCP(http, pausa_s=0, espera_base=0, prazo_download_s=180,
                                  relogio=relogio)  # fmt: skip
    with pytest.raises(ErroArquivos, match="prazo"):
        cliente.baixar(f"{BASE}/x/1")


@pytest.mark.parametrize(
    "url",
    [
        "http://pncp.gov.br/pncp-api/v1/x/1",
        "https://pncp.gov.br.evil.example/x/1",
        "https://evil.example/pncp.gov.br/x",
        "https://usuario@evil.example/x",
    ],
)
def test_baixar_so_de_https_pncp(url: str) -> None:
    cliente, chamadas = _cliente([httpx.Response(200, content=b"%PDF")])
    with pytest.raises(ErroArquivos, match="fora de https://pncp.gov.br"):
        cliente.baixar(url)
    assert chamadas == []


def test_redirect_para_fora_do_pncp_nao_e_seguido() -> None:
    cliente, chamadas = _cliente(
        [httpx.Response(302, headers={"Location": "https://evil.example/edital.pdf"})]
    )
    with pytest.raises(ErroArquivos, match="fora de https://pncp.gov.br"):
        cliente.baixar(f"{BASE}/x/1")
    assert [str(c.url.host) for c in chamadas] == ["pncp.gov.br"]


def test_redirect_dentro_do_pncp_e_seguido() -> None:
    cliente, chamadas = _cliente(
        [
            httpx.Response(301, headers={"Location": "https://pncp.gov.br/pncp-api/v1/y/1"}),
            httpx.Response(200, content=b"%PDF-1.7 abc"),
        ]
    )
    assert cliente.baixar(f"{BASE}/x/1") == b"%PDF-1.7 abc"
    assert [c.url.path for c in chamadas] == ["/pncp-api/v1/x/1", "/pncp-api/v1/y/1"]
