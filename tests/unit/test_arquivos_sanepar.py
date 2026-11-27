from pathlib import Path

import httpx
import pytest

from radar.analise.arquivos_pncp import ClienteArquivosPNCP, ErroArquivos
from radar.analise.arquivos_sanepar import ORIGEM_SANEPAR, arquivos_sanepar
from radar.coleta.sanepar import ler_detalhe

DADOS = Path(__file__).resolve().parents[1] / "dados" / "fontes"


def _bruto(anexos: object) -> dict[str, object]:
    return {"detalhe": {"anexos": anexos}}


def test_amostra_real_escolhe_o_pdf_do_edital_e_deixa_o_zip() -> None:
    detalhe = ler_detalhe((DADOS / "detalhe-345-26-vazamentos.html").read_text(encoding="utf-8"))
    [arquivo] = arquivos_sanepar(_bruto(detalhe.anexos))
    assert arquivo.url == "https://www.sanepar.com.br/licitacao/arquivo/221507"
    assert arquivo.sequencial == 221507
    assert arquivo.titulo == "EDITAL LE 345 2026"


def test_filtra_por_nome_ordena_pelo_id_e_limita_a_tres() -> None:
    anexos = [
        {"nome": "ERRATA DO EDITAL", "id": "10"},
        {"nome": "Termo de Referência", "id": "9"},
        {"nome": "EDITAL LE 1 2026", "id": "8"},
        {"nome": "Planilha", "id": "7"},
        {"nome": "EDITAL_ELEMENTOS.ZIP", "id": "LC12026Edital_Elementos.zip"},
        {"nome": "EDITAL retificado", "id": "11"},
        {"nome": "Edital anexo II", "id": "12"},
    ]
    escolhidos = arquivos_sanepar(_bruto(anexos))
    assert [a.sequencial for a in escolhidos] == [8, 9, 11]


@pytest.mark.parametrize(
    "bruto",
    [None, {}, {"detalhe": None}, _bruto(None), _bruto("x"), _bruto(["x", {"nome": None}])],
)
def test_bruto_sem_anexos_nao_quebra(bruto: dict[str, object] | None) -> None:
    assert arquivos_sanepar(bruto) == []


def _cliente(tratar: object) -> ClienteArquivosPNCP:
    http = httpx.Client(transport=httpx.MockTransport(tratar))  # type: ignore[arg-type]
    return ClienteArquivosPNCP(http, pausa_s=0, espera_base=0, origem=ORIGEM_SANEPAR)


def test_cliente_da_sanepar_recusa_outro_host() -> None:
    with pytest.raises(ErroArquivos, match="fora de https://www.sanepar.com.br"):
        _cliente(lambda r: httpx.Response(200)).baixar("https://pncp.gov.br/x")


def test_cliente_da_sanepar_recusa_redirect_para_fora() -> None:
    def tratar(requisicao: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://exemplo.com/edital.pdf"})

    with pytest.raises(ErroArquivos, match="recusado"):
        _cliente(tratar).baixar("https://www.sanepar.com.br/licitacao/arquivo/1")


def test_cliente_da_sanepar_baixa_do_portal() -> None:
    dados = _cliente(lambda r: httpx.Response(200, content=b"%PDF-1.4")).baixar(
        "https://www.sanepar.com.br/licitacao/arquivo/1"
    )
    assert dados == b"%PDF-1.4"
