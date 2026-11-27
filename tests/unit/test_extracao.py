from pathlib import Path

from radar.analise.extracao import TextoExtraido, eh_pdf, extrair_paginas, status_do_texto

DADOS = Path(__file__).resolve().parents[1] / "dados" / "ia"


def test_eh_pdf() -> None:
    assert eh_pdf(b"%PDF-1.7\n...")
    assert not eh_pdf(b"PK\x03\x04")
    assert not eh_pdf(b"")


def test_extrair_paginas_pdf_de_texto() -> None:
    texto = extrair_paginas((DADOS / "edital_texto.pdf").read_bytes())
    assert len(texto.paginas) == 2
    assert "capital social" in texto.paginas[0]
    assert "consórcio" in texto.paginas[1]
    assert texto.paginas_sem_texto == 0
    assert status_do_texto(texto) == "extraido"


def test_escaneado_vira_sem_texto() -> None:
    texto = extrair_paginas((DADOS / "edital_escaneado.pdf").read_bytes())
    assert len(texto.paginas) == 2
    assert texto.paginas_sem_texto == 2
    assert status_do_texto(texto) == "sem_texto"


def test_metade_sem_texto_ainda_extrai() -> None:
    metade = TextoExtraido(paginas=["a" * 300, ""], paginas_sem_texto=1)
    assert status_do_texto(metade) == "extraido"
    assert status_do_texto(TextoExtraido(paginas=[], paginas_sem_texto=0)) == "sem_texto"
