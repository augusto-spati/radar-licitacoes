"""Módulo editais."""

from radar.editais.filtro import Criterios, EditalResumo, carregar_criterios, filtrar
from radar.editais.registro import ResultadoRegistro, registrar

__all__ = [
    "Criterios",
    "EditalResumo",
    "ResultadoRegistro",
    "carregar_criterios",
    "filtrar",
    "registrar",
]
