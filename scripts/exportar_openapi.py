"""Grava frontend/openapi.json a partir da API. Uso: uv run python scripts/exportar_openapi.py"""

from pathlib import Path

from radar.api.contrato import gerar_openapi_json

RAIZ = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    destino = RAIZ / "frontend" / "openapi.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(gerar_openapi_json(RAIZ / "config" / "filtro.toml").encode("utf-8"))
    print(f"gravado {destino}")
