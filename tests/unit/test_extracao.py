import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from radar.analise import extracao
from radar.analise.extracao import (
    ErroExtracao,
    TextoExtraido,
    com_prazo,
    eh_pdf,
    extrair_paginas,
    status_do_texto,
)

DADOS = Path(__file__).resolve().parents[1] / "dados" / "ia"
PDF_TEXTO = (DADOS / "edital_texto.pdf").read_bytes()


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


@pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="limite por SIGALRM só em POSIX")
def test_com_prazo_interrompe_extracao_demorada() -> None:
    def demorada() -> None:
        fim = time.monotonic() + 5
        while time.monotonic() < fim:
            pass

    inicio = time.monotonic()
    with pytest.raises(ErroExtracao, match="prazo"):
        com_prazo(demorada, 0.2)
    assert time.monotonic() - inicio < 2
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_com_prazo_devolve_o_resultado() -> None:
    assert com_prazo(lambda: 42, 5) == 42


def test_com_prazo_fora_da_thread_principal_roda_sem_limite() -> None:
    resultado: list[int] = []
    thread = threading.Thread(target=lambda: resultado.append(com_prazo(lambda: 7, 0.01)))
    thread.start()
    thread.join()
    assert resultado == [7]


def test_extrair_paginas_usa_o_prazo(monkeypatch: pytest.MonkeyPatch) -> None:
    prazos: list[float] = []

    def falso(funcao: Callable[[], object], prazo_s: float) -> object:
        prazos.append(prazo_s)
        return funcao()

    monkeypatch.setattr(extracao, "com_prazo", falso)
    extrair_paginas(PDF_TEXTO, prazo_s=12)
    assert prazos == [12]
