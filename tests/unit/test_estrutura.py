import importlib

import pytest

MODULOS = [
    "radar.coleta",
    "radar.editais",
    "radar.analise",
    "radar.comum",
    "radar.cli",
    "radar.processador",
]


@pytest.mark.parametrize("nome", MODULOS)
def test_modulos_importam(nome: str) -> None:
    assert importlib.import_module(nome) is not None
