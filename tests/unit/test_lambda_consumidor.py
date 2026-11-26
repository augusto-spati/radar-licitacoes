"""Handler do consumidor: lote do SQS com ReportBatchItemFailures, sem banco de verdade."""

import importlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType
from typing import Any

import pytest

from radar.comum import Mensagem
from radar.processador import MensagemAdiada


def _registro(message_id: str, corpo: str) -> dict[str, Any]:
    return {"messageId": message_id, "body": corpo, "eventSource": "aws:sqs"}


def _corpo(outbox_id: int, pagina_id: int) -> str:
    return json.dumps(
        {"tipo": "pagina_coletada", "carga": {"pagina_id": pagina_id}, "outbox_id": outbox_id}
    )


class _SessaoFalsa:
    def __init__(self, abertas: list["_SessaoFalsa"]) -> None:
        self.fechada = False
        abertas.append(self)

    def __enter__(self) -> "_SessaoFalsa":
        return self

    def __exit__(self, *_: object) -> None:
        self.fechada = True


@pytest.fixture
def modulo(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.setenv("RADAR_DATABASE_URL", "postgresql+psycopg://x:x@localhost:1/x")
    monkeypatch.setenv("RADAR_DB_NULLPOOL", "true")
    import radar.processador.lambda_handler as m

    recarregado = importlib.reload(m)
    monkeypatch.setattr(recarregado, "configurar_logs", lambda: None)  # não mexe nos do pytest
    yield recarregado


@contextmanager
def _falso(
    modulo: ModuleType, monkeypatch: pytest.MonkeyPatch, desfechos: dict[int, Any]
) -> Iterator[tuple[list[Mensagem], list[_SessaoFalsa]]]:
    vistas: list[Mensagem] = []
    abertas: list[_SessaoFalsa] = []

    def processar(sessao: Any, mensagem: Mensagem, fontes: Any, agora: Any) -> str:
        assert isinstance(sessao, _SessaoFalsa) and not sessao.fechada
        assert set(fontes) == {"sanepar", "compesa"}
        vistas.append(mensagem)
        desfecho = desfechos[mensagem.outbox_id]
        if isinstance(desfecho, BaseException):
            raise desfecho
        return str(desfecho)

    monkeypatch.setattr(modulo, "processar_mensagem", processar)
    monkeypatch.setattr(modulo, "_criar_sessao", lambda: _SessaoFalsa(abertas))
    yield vistas, abertas


def test_lista_so_os_que_falharam(modulo: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    desfechos = {1: "ok", 2: MensagemAdiada("trava"), 3: RuntimeError("boom"), 4: "ok"}
    evento = {"Records": [_registro(f"m{i}", _corpo(i, 10 + i)) for i in desfechos]}

    with _falso(modulo, monkeypatch, desfechos) as (vistas, abertas):
        resposta = modulo.handler(evento, None)

    assert resposta == {"batchItemFailures": [{"itemIdentifier": "m2"}, {"itemIdentifier": "m3"}]}
    assert [m.outbox_id for m in vistas] == [1, 2, 3, 4]
    assert vistas[0].carga == {"pagina_id": 11}
    assert len(abertas) == 4 and all(s.fechada for s in abertas)  # uma sessão por mensagem


def test_falha_definitiva_nao_entra(modulo: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    desfechos = {1: "falha_definitiva", 2: "ok"}
    evento = {"Records": [_registro(f"m{i}", _corpo(i, i)) for i in desfechos]}

    with _falso(modulo, monkeypatch, desfechos):
        assert modulo.handler(evento, None) == {"batchItemFailures": []}


def test_corpo_invalido_entra(modulo: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    evento = {
        "Records": [
            _registro("ruim", "não é json"),
            _registro("tipo", json.dumps({"tipo": "x", "carga": {}, "outbox_id": 1})),
            _registro("bom", _corpo(5, 5)),
        ]
    }

    with _falso(modulo, monkeypatch, {5: "ok"}) as (vistas, _):
        resposta = modulo.handler(evento, None)

    assert resposta == {
        "batchItemFailures": [{"itemIdentifier": "ruim"}, {"itemIdentifier": "tipo"}]
    }
    assert [m.outbox_id for m in vistas] == [5]


def test_import_nao_abre_conexao(modulo: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    def proibido(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("importar o handler não pode abrir conexão nem ler o SSM")

    monkeypatch.setattr("sqlalchemy.engine.Engine.connect", proibido)
    monkeypatch.setattr("radar.comum.segredos._cliente_ssm", proibido)
    m = importlib.reload(modulo)
    assert m.handler is not None


def test_engine_criado_uma_vez(modulo: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    criados: list[Any] = []

    def criar_engine(config: Any) -> Any:
        assert config.db_nullpool is True
        criados.append(config)
        return object()

    monkeypatch.setattr(modulo, "criar_engine", criar_engine)
    monkeypatch.setattr(modulo, "fabrica_sessao", lambda engine: lambda: ("sessao", engine))
    primeira = modulo._criar_sessao()
    segunda = modulo._criar_sessao()
    assert len(criados) == 1
    assert primeira[1] is segunda[1]
