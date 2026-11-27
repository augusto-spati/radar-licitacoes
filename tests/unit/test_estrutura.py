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


def test_cli_nao_importa_a_analise_ao_carregar() -> None:
    """Coletor e relay não pagam (nem quebram com) o import da análise: só `analisar`/`avaliar`."""
    import subprocess
    import sys

    codigo = "import sys, radar.cli; print('radar.analise' in sys.modules)"
    saida = subprocess.run(
        [sys.executable, "-c", codigo], capture_output=True, text=True, check=True
    ).stdout
    assert saida.strip() == "False"
