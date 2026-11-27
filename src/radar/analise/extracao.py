"""Texto por página de um PDF (pypdf; OCR fica fora desta etapa).

Limite de tempo da extração: um PDF patológico pode prender o pypdf por muito tempo. O limite
usa `signal.setitimer`/SIGALRM, que só existe em POSIX e só vale na thread principal; é o caso
da produção (task Linux do ECS rodando `radar analisar`). No Windows e fora da thread
principal a extração roda sem limite (a rodada ainda tem o orçamento de tempo do `analisar`).
Subprocesso daria o limite em toda plataforma, mas custa um processo por documento e o fork
de um processo com threads (boto3) não é seguro.

O sinal levanta `_PrazoEsgotado`, que herda de BaseException: o pypdf captura `Exception` em
volta de cada objeto e seguiria lendo. Só `com_prazo` a captura e a converte em
`ErroExtracao` (nenhum outro código deve capturá-la).
"""

import io
import logging
import signal
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from types import FrameType
from typing import Literal

logger = logging.getLogger(__name__)

MINIMO_CARACTERES = 200
PRAZO_EXTRACAO_S = 120.0


@dataclass(frozen=True)
class TextoExtraido:
    paginas: list[str]
    paginas_sem_texto: int


class ErroExtracao(Exception):
    pass


def eh_pdf(dados: bytes) -> bool:
    return dados[:4] == b"%PDF"


class _PrazoEsgotado(BaseException):
    """BaseException de propósito: `except Exception` do pypdf não a engole."""


def com_prazo[T](funcao: Callable[[], T], prazo_s: float) -> T:
    """Roda `funcao`; passou de `prazo_s` → `ErroExtracao`. Sem SIGALRM: sem limite."""
    if sys.platform == "win32":  # sem SIGALRM
        return funcao()
    if threading.current_thread() is not threading.main_thread():
        return funcao()

    def estourou(_sinal: int, _quadro: FrameType | None) -> None:
        raise _PrazoEsgotado

    anterior = signal.signal(signal.SIGALRM, estourou)
    signal.setitimer(signal.ITIMER_REAL, prazo_s)
    try:
        return funcao()
    except _PrazoEsgotado:
        raise ErroExtracao(f"extração passou do prazo de {prazo_s:g} s") from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, anterior)


def extrair_paginas(dados: bytes, prazo_s: float = PRAZO_EXTRACAO_S) -> TextoExtraido:
    """Uma string por página. Página que falha vira texto vazio (e conta como sem texto).

    Passou de `prazo_s` (onde há SIGALRM) → `ErroExtracao`.
    """
    return com_prazo(lambda: _extrair(dados), prazo_s)


def _extrair(dados: bytes) -> TextoExtraido:
    import pypdf  # só quem extrai paga o import

    try:
        leitor = pypdf.PdfReader(io.BytesIO(dados))
        if leitor.is_encrypted:
            leitor.decrypt("")
        paginas_pdf = list(leitor.pages)
    except ErroExtracao:
        raise
    except Exception as erro:  # pypdf levanta tipos variados para PDF corrompido
        raise ErroExtracao(f"{type(erro).__name__}: {erro}") from erro
    paginas: list[str] = []
    for numero, pagina in enumerate(paginas_pdf, start=1):
        try:
            paginas.append(pagina.extract_text() or "")
        except ErroExtracao:
            raise
        except Exception as erro:  # uma página ruim não derruba o documento
            logger.warning("pagina_sem_extracao", extra={"pagina": numero, "erro": str(erro)})
            paginas.append("")
    sem_texto = sum(1 for p in paginas if len(p.strip()) < MINIMO_CARACTERES)
    return TextoExtraido(paginas=paginas, paginas_sem_texto=sem_texto)


def status_do_texto(texto: TextoExtraido) -> Literal["extraido", "sem_texto"]:
    """Mais da metade das páginas sem texto (ou nenhuma página) → `sem_texto`."""
    if not texto.paginas or texto.paginas_sem_texto * 2 > len(texto.paginas):
        return "sem_texto"
    return "extraido"
