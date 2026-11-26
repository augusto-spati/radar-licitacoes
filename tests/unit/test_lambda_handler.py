"""Handler Lambda (Mangum): eventos HTTP API v2, app criado uma vez, leitura do SSM."""

import importlib
import json
from datetime import datetime
from types import ModuleType
from typing import Any

import pytest

from radar.comum.config import RAIZ_REPOSITORIO


def _evento(raw_path: str, metodo: str = "GET") -> dict[str, Any]:
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": raw_path,
        "rawQueryString": "",
        "headers": {"host": "api.example.com", "accept": "application/json"},
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "api",
            "domainName": "api.example.com",
            "http": {
                "method": metodo,
                "path": raw_path,
                "protocol": "HTTP/1.1",
                "sourceIp": "127.0.0.1",
                "userAgent": "pytest",
            },
            "requestId": "id",
            "stage": "$default",
            "time": "26/Nov/2026:09:00:00 +0000",
            "timeEpoch": 1795000000000,
        },
        "isBase64Encoded": False,
    }


@pytest.fixture
def modulo(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("RADAR_DATABASE_URL", "postgresql+psycopg://x:x@localhost:1/x")
    monkeypatch.setenv("RADAR_DB_NULLPOOL", "true")
    monkeypatch.setenv("RADAR_CONFIG_FILTRO", str(RAIZ_REPOSITORIO / "config" / "filtro.toml"))
    import radar.api.lambda_handler as m

    return importlib.reload(m)


def test_handler_status(modulo: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("radar.api.app.ultima_atualizacao", lambda sessao: datetime(2026, 1, 1))
    resposta = modulo.handler(_evento("/api/status"), None)
    assert resposta["statusCode"] == 200
    assert "atualizado_em" in json.loads(resposta["body"])


@pytest.mark.parametrize(
    "raw_path", ["/api/editais/sanepar~OE-345%2F26", "/api/editais/sanepar~OE-345/26"]
)
def test_handler_id_com_barra(
    modulo: ModuleType, monkeypatch: pytest.MonkeyPatch, raw_path: str
) -> None:
    recebidos: list[str] = []

    def falso(sessao: object, numero: str, criterios: object) -> None:
        recebidos.append(numero)

    monkeypatch.setattr("radar.api.app.obter_edital", falso)
    resposta = modulo.handler(_evento(raw_path), None)
    assert resposta["statusCode"] == 404  # chegou na rota de detalhe (id inexistente)
    assert recebidos == ["sanepar~OE-345/26"]


def test_app_criado_uma_vez(modulo: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    import radar.api.app as app_mod

    chamadas: list[int] = []
    original = app_mod.criar_app

    def contando(*args: Any, **kwargs: Any) -> Any:
        chamadas.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(app_mod, "criar_app", contando)
    monkeypatch.setattr(app_mod, "ultima_atualizacao", lambda sessao: None)
    m = importlib.reload(modulo)
    for _ in range(3):
        assert m.handler(_evento("/api/status"), None)["statusCode"] == 200
    assert chamadas == [1]


def test_import_nao_abre_conexao(modulo: ModuleType) -> None:
    # a URL aponta para uma porta fechada: importar não pode ter tentado conectar
    assert modulo.handler is not None


class _SsmFalso:
    def __init__(self) -> None:
        self.pedidos: list[dict[str, Any]] = []

    def get_parameter(self, **kwargs: Any) -> dict[str, Any]:
        self.pedidos.append(kwargs)
        return {"Parameter": {"Value": "postgresql+psycopg://do-ssm"}}


def test_le_ssm_quando_sem_env(modulo: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    falso = _SsmFalso()
    monkeypatch.delenv("RADAR_DATABASE_URL", raising=False)
    monkeypatch.setattr(modulo, "_cliente_ssm", lambda: falso)
    modulo._garantir_url_do_banco()
    import os

    assert os.environ["RADAR_DATABASE_URL"] == "postgresql+psycopg://do-ssm"
    assert falso.pedidos == [{"Name": "/radar/prod/database_url", "WithDecryption": True}]


def test_nao_le_ssm_com_env(modulo: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode() -> None:
        raise AssertionError("não deveria consultar o SSM")

    monkeypatch.setenv("RADAR_DATABASE_URL", "postgresql+psycopg://local")
    monkeypatch.setattr(modulo, "_cliente_ssm", explode)
    modulo._garantir_url_do_banco()
