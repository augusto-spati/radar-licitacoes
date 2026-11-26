"""Ponto de entrada da AWS Lambda (HTTP API v2) via Mangum.

O app FastAPI é criado uma vez por container (nível do módulo); o engine usa NullPool
(`RADAR_DB_NULLPOOL=true`) e é preguiçoso: importar este módulo não abre conexão.

A URL do banco vem do SSM (`/radar/prod/database_url`) quando `RADAR_DATABASE_URL` não está
definida. `boto3` é importado só aí: ele não é dependência do projeto, vem na imagem base
`public.ecr.aws/lambda/python`.
"""

import os
from typing import Any

from mangum import Mangum

from radar.api.app import criar_app

PARAMETRO_URL_BANCO = "/radar/prod/database_url"


def _cliente_ssm() -> Any:
    import boto3  # type: ignore[import-not-found]  # só na imagem Lambda

    return boto3.client("ssm")


def _garantir_url_do_banco() -> None:
    if os.environ.get("RADAR_DATABASE_URL"):
        return
    resposta = _cliente_ssm().get_parameter(Name=PARAMETRO_URL_BANCO, WithDecryption=True)
    os.environ["RADAR_DATABASE_URL"] = resposta["Parameter"]["Value"]


_garantir_url_do_banco()
app = criar_app()
handler = Mangum(app, lifespan="off")
