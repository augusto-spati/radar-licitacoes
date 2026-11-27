import io
import zipfile
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest

from radar.analise.arquivos_compesa import (
    ORIGEM_NUVEM,
    ORIGEM_PORTAL,
    DocumentosCompesa,
    escolher_no_pacote,
    legivel,
)
from radar.analise.arquivos_pncp import ClienteArquivosPNCP, ErroArquivos

TOKEN = "af3df4f519060b50c7389e8af13f3557"
LINK = f"https://nuvem.compesa.com.br/public.php?service=files&t={TOKEN}"
BRUTO = {"numero": "LC 168/2026 CPL", "campos": {"ZIP_ANEXOS": "LC168-2026CPL.ZIP"}}

# Nomes vistos nos pacotes reais (27/11/2026).
LC213 = [
    "INTERNET-COMPESA-LC213.2026/",
    "INTERNET-COMPESA-LC213.2026/ANEXO-I-MOD.PROP.PRECO.xlsx",
    "INTERNET-COMPESA-LC213.2026/ANEXO-III-ORDEM-COMPRA.pdf",
    "INTERNET-COMPESA-LC213.2026/ANEXO-VI-TERMO-REFERENCIA.pdf",
    "INTERNET-COMPESA-LC213.2026/ANEXO-X-MATRIZ-RISCO.pdf",
    "INTERNET-COMPESA-LC213.2026/EDITAL-LC213-2026.pdf",
]
LC217 = [
    "EDITAL E SEUS ANEXOS/ANEXO-I-PROPOSTA.pdf",
    "EDITAL E SEUS ANEXOS/ANEXO-VI-TR.pdf",
    "EDITAL E SEUS ANEXOS/EDITA-LC217-2026.pdf",
]
LC168 = [
    "LC 168 2026 DPP CPL/ANEXO V - TR SEGURAN\xc7A DO TRABALHO.pdf",
    "LC 168 2026 DPP CPL/ANEXO XIII - TERMO REFER\xcaNCIA.docx",
    "LC 168 2026 DPP CPL/ANEXO XV - PROJETOS/LICITACAO.zip",
    "LC 168 2026 DPP CPL/ANEXO XX - TERMO REFER\xcaNCIA ELABORA\xc7\xc3O PROJ EXECUTIVO.docx",
    "LC 168 2026 DPP CPL/EDITAL LC 168 2026 DPP CPL.docx",
    "LC 168 2026 DPP CPL/Thumbs.db",
    "LC 168 2026 DPP CPL/~$ITAL LC 230 2025 DPP CPL.docx",
]


@pytest.mark.parametrize(
    ("nomes", "esperado"),
    [
        (LC213, ["EDITAL-LC213-2026.pdf", "ANEXO-VI-TERMO-REFERENCIA.pdf"]),
        (LC217, ["EDITA-LC217-2026.pdf", "ANEXO-VI-TR.pdf"]),
        (LC168, ["EDITAL LC 168 2026 DPP CPL.docx", "ANEXO XIII - TERMO REFER\xcaNCIA.docx"]),
    ],
)
def test_escolhe_edital_e_termo_de_referencia(nomes: list[str], esperado: list[str]) -> None:
    escolhidos = escolher_no_pacote(nomes)
    assert [e.nome.rsplit("/", 1)[-1] for e in escolhidos] == esperado
    assert escolhidos[0].tipo == "Edital"


def test_nome_utf8_sem_marca_volta_legivel() -> None:
    embaralhado = "EDITAL LICITAÇÃO.pdf".encode().decode("cp437")
    assert legivel(embaralhado) == "EDITAL LICITAÇÃO.pdf"
    assert legivel("ANEXO V - TR SEGURAN\xc7A.pdf") == "ANEXO V - TR SEGURAN\xc7A.pdf"


def _zip(arquivos: dict[str, bytes]) -> bytes:
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w") as pacote:
        for nome, dados in arquivos.items():
            pacote.writestr(nome, dados)
    return saida.getvalue()


def _documentos(
    tratar: Callable[[httpx.Request], httpx.Response],
) -> tuple[DocumentosCompesa, list[httpx.Request]]:
    pedidos: list[httpx.Request] = []

    def registrar(requisicao: httpx.Request) -> httpx.Response:
        pedidos.append(requisicao)
        return tratar(requisicao)

    def cliente(origem: tuple[str, str]) -> ClienteArquivosPNCP:
        http = httpx.Client(transport=httpx.MockTransport(registrar))
        return ClienteArquivosPNCP(http, pausa_s=0, espera_base=0, origem=origem)

    return DocumentosCompesa(cliente(ORIGEM_PORTAL), cliente(ORIGEM_NUVEM)), pedidos


def _regra(ident: str) -> httpx.Response:
    return httpx.Response(200, text=f"$mainform().document._ruleReturn = {ident};\r\n")


def test_compra_com_pdfs_no_zip_do_portal() -> None:
    pacote = _zip({"X/EDITAL-LC213-2026.pdf": b"%PDF edital", "X/ANEXO-I.xlsx": b"x"})

    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("executeRule.do"):
            return _regra("13904")
        assert req.url.path.endswith("/Upload/LC168-2026CPL13904.ZIP")
        return httpx.Response(200, content=pacote)

    documentos, pedidos = _documentos(tratar)
    [arquivo] = documentos.listar(BRUTO)
    assert arquivo.titulo == "EDITAL-LC213-2026.pdf"
    assert arquivo.url.endswith("LC168-2026CPL13904.ZIP#X/EDITAL-LC213-2026.pdf")
    assert documentos.baixar(arquivo.url) == b"%PDF edital"
    regra = parse_qs(pedidos[0].url.query.decode())
    assert regra["P_0"] == ["LC 168/2026 CPL"]
    assert regra["P_1"] == ["NÃO IDENTIFICADO"]
    assert all(p.method == "GET" for p in pedidos)


def test_obra_segue_o_link_do_doc_ate_a_nuvem() -> None:
    doc = b"\xd0\xcf lixo binario HYPERLINK " + LINK.encode() + b" fim"
    nuvem = _zip({"LC/EDITAL LC 168.docx": b"PK docx", "LC/ANEXO II - CRONOGRAMA.xlsx": b"x"})

    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("executeRule.do"):
            return _regra("99")
        if req.url.host == "nuvem.compesa.com.br":
            assert "download" in req.url.query.decode()
            return httpx.Response(200, content=nuvem)
        return httpx.Response(200, content=_zip({"LINKEDITAL1682026.doc": doc}))

    documentos, _ = _documentos(tratar)
    [arquivo] = documentos.listar(BRUTO)
    assert arquivo.titulo == "EDITAL LC 168.docx"
    assert arquivo.url == f"{LINK}#LC/EDITAL LC 168.docx"
    assert documentos.baixar(arquivo.url) == b"PK docx"


def test_nuvem_com_um_arquivo_so() -> None:
    doc = LINK.encode()

    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("executeRule.do"):
            return _regra("1")
        if req.url.host == "nuvem.compesa.com.br":
            return httpx.Response(200, content=b"%PDF edital avulso")
        return httpx.Response(200, content=_zip({"LINK.doc": doc}))

    documentos, _ = _documentos(tratar)
    [arquivo] = documentos.listar(BRUTO)
    assert documentos.baixar(arquivo.url) == b"%PDF edital avulso"


def test_sem_anexo_no_portal_ou_sem_dados_no_bruto() -> None:
    documentos, pedidos = _documentos(lambda req: _regra("''"))
    assert documentos.listar(BRUTO) == []
    assert len(pedidos) == 1
    assert documentos.listar({"numero": "LC 1/2026"}) == []
    assert documentos.listar(None) == []
    assert len(pedidos) == 1  # sem número ou ZIP_ANEXOS, nem pergunta ao portal


def test_pasta_da_nuvem_vazia_nao_tem_documento() -> None:
    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("executeRule.do"):
            return _regra("5")
        if req.url.host == "nuvem.compesa.com.br":
            return httpx.Response(200, content=_zip({"LC 220 2026 DAS CPL/": b""}))
        return httpx.Response(200, content=_zip({"LINK.doc": LINK.encode()}))

    documentos, _ = _documentos(tratar)
    assert documentos.listar(BRUTO) == []


def test_regra_fora_do_formato_e_documento_nao_listado() -> None:
    documentos, _ = _documentos(lambda req: httpx.Response(200, text="Request Rejected"))
    with pytest.raises(ErroArquivos, match="fora do formato"):
        documentos.listar(BRUTO)
    with pytest.raises(ErroArquivos, match="não listado"):
        documentos.baixar("https://portalscl.compesa.com.br:8743/x")


def test_link_para_outro_host_no_doc_e_ignorado() -> None:
    doc = b"https://exemplo.com/public.php?service=files&t=" + TOKEN.encode()

    def tratar(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("executeRule.do"):
            return _regra("5")
        assert req.url.host == "portalscl.compesa.com.br"
        return httpx.Response(200, content=_zip({"LINK.doc": doc}))

    documentos, _ = _documentos(tratar)
    assert documentos.listar(BRUTO) == []
