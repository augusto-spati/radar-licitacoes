"""Contrato OpenAPI serializado de forma determinística (arquivo commitado em frontend/)."""

import json
from pathlib import Path

from sqlalchemy.orm import Session

from radar.api.app import criar_app


def gerar_openapi_json(config_filtro: Path) -> str:
    def sem_banco() -> Session:
        raise RuntimeError("o contrato não acessa o banco")

    app = criar_app(sem_banco, config_filtro)
    return json.dumps(app.openapi(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
