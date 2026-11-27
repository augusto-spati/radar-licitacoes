"""URL do banco nas Lambdas: lida do SSM quando `RADAR_DATABASE_URL` não está definida.

`boto3` é importado só aí: importar este módulo não toca a AWS.
"""

import os
from typing import Any

PARAMETRO_URL_BANCO = "/radar/prod/database_url"


def _cliente_ssm() -> Any:
    import boto3  # type: ignore[import-untyped]

    return boto3.client("ssm")


def garantir_url_do_banco() -> None:
    """Põe a URL do SSM em `RADAR_DATABASE_URL`. A mensagem de erro não leva o valor."""
    if os.environ.get("RADAR_DATABASE_URL"):
        return
    try:
        resposta = _cliente_ssm().get_parameter(Name=PARAMETRO_URL_BANCO, WithDecryption=True)
        valor: str = resposta["Parameter"]["Value"]
    except Exception as exc:
        raise RuntimeError(f"não foi possível ler {PARAMETRO_URL_BANCO} do SSM") from exc
    os.environ["RADAR_DATABASE_URL"] = valor
