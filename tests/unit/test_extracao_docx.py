import io
import zipfile

import pytest

from radar.analise import extracao
from radar.analise.extracao import ErroExtracao, eh_docx, extrair_paginas_docx, status_do_texto

PARAGRAFO = "Exige-se capital social mínimo de R$ 100.000,00 &amp; atestado técnico. " * 4


def docx(corpo: str) -> bytes:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="w"><w:body>'
        + corpo
        + "</w:body></w:document>"
    )
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w") as arquivo:
        arquivo.writestr("[Content_Types].xml", "<Types/>")
        arquivo.writestr("word/document.xml", xml)
    return saida.getvalue()


def p(texto: str, antes: str = "") -> str:
    return f'<w:p><w:r>{antes}<w:t xml:space="preserve">{texto}</w:t></w:r></w:p>'


def test_reconhece_docx_e_nao_confunde_com_zip_qualquer_ou_pdf() -> None:
    assert eh_docx(docx(p("x")))
    outro = io.BytesIO()
    with zipfile.ZipFile(outro, "w") as arquivo:
        arquivo.writestr("a.txt", "x")
    assert not eh_docx(outro.getvalue())
    assert not eh_docx(b"%PDF-1.4")
    assert not eh_docx(b"PK\x03\x04 quebrado")


def test_paginas_pelas_quebras_do_word_e_entidades() -> None:
    corpo = (
        p(PARAGRAFO)
        + p(PARAGRAFO, antes="<w:lastRenderedPageBreak/>")
        + p(PARAGRAFO, antes='<w:br w:type="page"/>')
    )
    texto = extrair_paginas_docx(docx(corpo))
    assert len(texto.paginas) == 3
    assert "capital social mínimo de R$ 100.000,00 & atestado" in texto.paginas[1]
    assert status_do_texto(texto) == "extraido"


def test_sem_quebra_corta_em_blocos_de_paragrafos() -> None:
    corpo = "".join(p(f"{i} {PARAGRAFO}") for i in range(40))
    texto = extrair_paginas_docx(docx(corpo))
    assert len(texto.paginas) > 1
    assert all(
        len(pagina) <= extracao.CARACTERES_POR_BLOCO + len(PARAGRAFO) + 10
        for pagina in texto.paginas
    )
    assert "".join(texto.paginas).count("capital social") == 40 * 4


def test_docx_sem_document_xml_ou_grande_demais(monkeypatch: pytest.MonkeyPatch) -> None:
    outro = io.BytesIO()
    with zipfile.ZipFile(outro, "w") as arquivo:
        arquivo.writestr("a.txt", "x")
    with pytest.raises(ErroExtracao):
        extrair_paginas_docx(outro.getvalue())
    monkeypatch.setattr(extracao, "LIMITE_XML_DOCX", 10)
    with pytest.raises(ErroExtracao, match="document.xml"):
        extrair_paginas_docx(docx(p(PARAGRAFO)))
