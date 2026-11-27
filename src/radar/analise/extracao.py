"""Texto por página de um PDF (pypdf; OCR fica fora desta etapa)."""

import io
import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

MINIMO_CARACTERES = 200


@dataclass(frozen=True)
class TextoExtraido:
    paginas: list[str]
    paginas_sem_texto: int


class ErroExtracao(Exception):
    pass


def eh_pdf(dados: bytes) -> bool:
    return dados[:4] == b"%PDF"


def extrair_paginas(dados: bytes) -> TextoExtraido:
    """Uma string por página. Página que falha vira texto vazio (e conta como sem texto)."""
    import pypdf  # só quem extrai paga o import

    try:
        leitor = pypdf.PdfReader(io.BytesIO(dados))
        if leitor.is_encrypted:
            leitor.decrypt("")
        paginas_pdf = list(leitor.pages)
    except Exception as erro:  # pypdf levanta tipos variados para PDF corrompido
        raise ErroExtracao(f"{type(erro).__name__}: {erro}") from erro
    paginas: list[str] = []
    for numero, pagina in enumerate(paginas_pdf, start=1):
        try:
            paginas.append(pagina.extract_text() or "")
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
