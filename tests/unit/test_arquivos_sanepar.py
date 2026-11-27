import io
import zipfile
from pathlib import Path

import httpx
import pytest

from radar.analise.arquivos_pncp import ClienteArquivosPNCP, ErroArquivos
from radar.analise.arquivos_sanepar import (
    ORIGEM_SANEPAR,
    DocumentosSanepar,
    arquivos_sanepar,
    pacotes_sanepar,
)
from radar.analise.pacotes import escolher_no_pacote
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


# --- ZIP de elementos -------------------------------------------------------------------------
# Nomes do EDITAL_ELEMENTOS.ZIP da LE 345/2026 (27/11/2026).
ELEMENTOS_345 = [
    "LC_345_2026/",
    "LC_345_2026/ANEXO A/274331_Termo_Ref_Deteccao_Vazamentov14_set2026.pdf",
    "LC_345_2026/ANEXO B/Capa Anexo B.pdf",
    "LC_345_2026/ANEXO C/Anexo C -15.pdf",
    "LC_345_2026/ANEXO D/RelPorServicoCompleto-1623.PDF",
    "LC_345_2026/RESOLUÇÃO/Resolução_Habilitação_Econômico_Financeira.pdf",
]
PDF_DIRETO = {"nome": "EDITAL LE 345 2026", "id": "221507"}
ZIP_345 = {"nome": "EDITAL_ELEMENTOS.ZIP", "id": "LC3452026Edital_Elementos.zip"}


def test_escolha_no_zip_real_pega_tr_abreviado_e_habilitacao() -> None:
    escolhidos = escolher_no_pacote(ELEMENTOS_345)
    assert [(e.nome.rsplit("/", 1)[-1], e.tipo) for e in escolhidos] == [
        ("274331_Termo_Ref_Deteccao_Vazamentov14_set2026.pdf", "Termo de referência"),
        ("Resolução_Habilitação_Econômico_Financeira.pdf", "Habilitação"),
    ]


def test_habilitacao_deixa_de_fora_declaracao_e_modelo() -> None:
    nomes = ["X/Declaracao_Habilitacao.pdf", "X/Modelo habilitação.docx", "X/Habilitacao.xlsx"]
    assert escolher_no_pacote(nomes) == []


def test_url_do_zip_pelo_nome_do_arquivo() -> None:
    assert pacotes_sanepar(_bruto([PDF_DIRETO, ZIP_345])) == [
        "https://www.sanepar.com.br/licitacao/documento?nome=LC3452026Edital_Elementos.zip"
    ]


def _zip(nomes: list[str]) -> bytes:
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w") as pacote:
        for nome in nomes:
            if not nome.endswith("/"):
                pacote.writestr(nome, b"%PDF " + nome.encode())
    return saida.getvalue()


def _documentos(zip_resposta: httpx.Response) -> DocumentosSanepar:
    def tratar(requisicao: httpx.Request) -> httpx.Response:
        if requisicao.url.path == "/licitacao/documento":
            return zip_resposta
        return httpx.Response(200, content=b"%PDF edital direto")

    return DocumentosSanepar(_cliente(tratar))


def test_pdf_direto_mais_tr_e_habilitacao_do_zip() -> None:
    documentos = _documentos(httpx.Response(200, content=_zip(ELEMENTOS_345)))
    arquivos = documentos.listar(_bruto([PDF_DIRETO, ZIP_345]))
    assert [(a.sequencial, a.tipo_nome) for a in arquivos] == [
        (221507, "Edital"),
        (2, "Termo de referência"),
        (3, "Habilitação"),
    ]
    assert arquivos[1].titulo == "274331_Termo_Ref_Deteccao_Vazamentov14_set2026.pdf"
    assert documentos.baixar(arquivos[0].url) == b"%PDF edital direto"
    assert documentos.baixar(arquivos[1].url).startswith(b"%PDF LC_345_2026/ANEXO A/")


def test_sem_pdf_direto_o_edital_vem_do_zip() -> None:
    nomes = ["P/EDITAL LE 9 2026.pdf", "P/Termo de Referencia.pdf"]
    documentos = _documentos(httpx.Response(200, content=_zip(nomes)))
    arquivos = documentos.listar(_bruto([ZIP_345]))
    assert [a.tipo_nome for a in arquivos] == ["Edital", "Termo de referência"]


def test_zip_que_falha_nao_derruba_o_pdf_direto() -> None:
    documentos = _documentos(httpx.Response(404))
    assert [a.sequencial for a in documentos.listar(_bruto([PDF_DIRETO, ZIP_345]))] == [221507]
    documentos = _documentos(httpx.Response(200, content=b"<html>erro</html>"))
    assert [a.sequencial for a in documentos.listar(_bruto([PDF_DIRETO, ZIP_345]))] == [221507]
