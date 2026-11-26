from pathlib import Path

from radar.api.contrato import gerar_openapi_json

RAIZ = Path(__file__).resolve().parent.parent.parent


def test_openapi_commitado_esta_atualizado() -> None:
    texto = (RAIZ / "frontend" / "openapi.json").read_bytes().decode("utf-8")
    commitado = texto.replace("\r\n", "\n")

    assert commitado == gerar_openapi_json(RAIZ / "config" / "filtro.toml"), (
        "frontend/openapi.json desatualizado: rode `uv run python scripts/exportar_openapi.py`"
    )
