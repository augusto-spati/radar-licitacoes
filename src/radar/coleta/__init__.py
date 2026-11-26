"""Módulo coleta."""

from radar.coleta.janelas import (
    Auditoria,
    ResumoColeta,
    auditar,
    coletar_pendentes,
    ler_paginas_nao_processadas,
    marcar_processada,
    planejar_janelas,
    ultima_atualizacao,
)
from radar.coleta.pncp import ClientePNCP, ErroPNCP, criar_http
from radar.coleta.retratos import (
    ClienteEducado,
    ErroFonte,
    FonteRetrato,
    InfoRetrato,
    PaginaColetada,
    ResumoRetrato,
    criar_http_educado,
    executar_retrato,
    ids_do_retrato,
    marcar_retrato_falha,
    trava_fonte,
    ultimo_retrato,
)
from radar.coleta.sanepar import FonteSanepar

__all__ = [
    "Auditoria",
    "ClienteEducado",
    "ClientePNCP",
    "ErroFonte",
    "ErroPNCP",
    "FonteRetrato",
    "FonteSanepar",
    "InfoRetrato",
    "PaginaColetada",
    "ResumoColeta",
    "ResumoRetrato",
    "auditar",
    "coletar_pendentes",
    "criar_http",
    "criar_http_educado",
    "executar_retrato",
    "ids_do_retrato",
    "ler_paginas_nao_processadas",
    "marcar_processada",
    "marcar_retrato_falha",
    "planejar_janelas",
    "trava_fonte",
    "ultima_atualizacao",
    "ultimo_retrato",
]
