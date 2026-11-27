"""Handlers da coletora e do planejador, sem banco nem AWS de verdade."""

import importlib
import json
from collections.abc import Iterator
from datetime import date, datetime
from types import ModuleType, SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from radar.comum import Mensagem, ResumoPublicacao


def _registro(message_id: str, janela_id: int, lote: int = 0) -> dict[str, Any]:
    carga = {"janela_id": janela_id, "lote": lote}
    corpo = json.dumps({"tipo": "coletar_janela", "carga": carga, "outbox_id": 100 + janela_id})
    return {"messageId": message_id, "body": corpo, "eventSource": "aws:sqs"}


class _SessaoFalsa:
    def __enter__(self) -> "_SessaoFalsa":
        return self

    def __exit__(self, *_: object) -> None:
        return None


CONTEXTO = SimpleNamespace(aws_request_id="req-1")
Vistas = list[tuple[Mensagem, str, float | None]]


@pytest.fixture
def coletora(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.setenv("RADAR_DATABASE_URL", "postgresql+psycopg://x:x@localhost:1/x")
    import radar.processador.lambda_coletora as m

    recarregado = importlib.reload(m)
    monkeypatch.setattr(recarregado, "configurar_logs", lambda: None)
    monkeypatch.setattr(recarregado, "_criar_sessao", lambda: _SessaoFalsa())
    monkeypatch.setattr(recarregado, "_criar_fila", lambda: object())
    monkeypatch.setattr(recarregado, "_criar_cliente", lambda: object())
    yield recarregado


class Relay:
    """`publicar_outbox` falso: conta as chamadas; levanta ou recusa nas chamadas pedidas."""

    def __init__(
        self, falhar_em: set[int] | None = None, recusar_em: set[int] | None = None
    ) -> None:
        self.chamadas = 0
        self.falhar_em = falhar_em or set()
        self.recusar_em = recusar_em or set()

    def __call__(self, sessao: Any, fila: Any, **_: Any) -> ResumoPublicacao:
        self.chamadas += 1
        if self.chamadas in self.falhar_em:
            raise RuntimeError("SQS fora do ar")
        recusadas = 1 if self.chamadas in self.recusar_em else 0
        return ResumoPublicacao(publicadas=1, recusadas=recusadas)


def _processar(desfechos: dict[int, Any], vistas: Vistas) -> Any:
    def processar(
        sessao: Any, mensagem: Mensagem, cliente: Any, token: str, orcamento_s: float | None
    ) -> str:
        assert isinstance(sessao, _SessaoFalsa)
        vistas.append((mensagem, token, orcamento_s))
        desfecho = desfechos[mensagem.carga["janela_id"]]
        if isinstance(desfecho, BaseException):
            raise desfecho
        return str(desfecho)

    return processar


@pytest.mark.parametrize(
    "desfecho",
    ["concluida", "continua", "dividida", "ocupada", "obsoleta", "inexistente", "lease_perdida"],
)
def test_desfechos_que_confirmam(
    coletora: ModuleType, monkeypatch: pytest.MonkeyPatch, desfecho: str
) -> None:
    vistas: Vistas = []
    relay = Relay()
    monkeypatch.setattr(coletora, "processar_janela", _processar({1: desfecho}, vistas))
    monkeypatch.setattr(coletora, "publicar_outbox", relay)

    resposta = coletora.handler({"Records": [_registro("m1", 1)]}, CONTEXTO)

    assert resposta == {"batchItemFailures": []}
    assert relay.chamadas == 2  # no começo e no fim
    assert [(m.carga, token, orc) for m, token, orc in vistas] == [
        ({"janela_id": 1, "lote": 0}, "req-1", 540)
    ]


@pytest.mark.parametrize("desfecho", ["falha", RuntimeError("boom")])
def test_falha_e_excecao_voltam(
    coletora: ModuleType, monkeypatch: pytest.MonkeyPatch, desfecho: Any
) -> None:
    monkeypatch.setattr(coletora, "processar_janela", _processar({1: desfecho, 2: "continua"}, []))
    monkeypatch.setattr(coletora, "publicar_outbox", Relay())

    resposta = coletora.handler({"Records": [_registro("m1", 1), _registro("m2", 2)]}, CONTEXTO)

    assert resposta == {"batchItemFailures": [{"itemIdentifier": "m1"}]}


def test_corpo_invalido_volta(coletora: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coletora, "processar_janela", _processar({}, []))
    monkeypatch.setattr(coletora, "publicar_outbox", Relay())
    sem_lote = json.dumps({"tipo": "coletar_janela", "carga": {"janela_id": 1}, "outbox_id": 1})
    outro_tipo = json.dumps({"tipo": "pagina_coletada", "carga": {"pagina_id": 1}, "outbox_id": 2})
    registros = [
        {"messageId": "x", "body": "lixo"},
        {"messageId": "y", "body": sem_lote},
        {"messageId": "z", "body": outro_tipo},
    ]

    resposta = coletora.handler({"Records": registros}, CONTEXTO)

    assert resposta == {
        "batchItemFailures": [
            {"itemIdentifier": "x"},
            {"itemIdentifier": "y"},
            {"itemIdentifier": "z"},
        ]
    }


def test_relay_do_fim_falha_e_a_reentrega_sai_obsoleta(
    coletora: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1ª invocação: a fatia commita a continuação (lote 1) e o relay do fim cai.
    desfechos: dict[int, Any] = {1: "continua"}
    relay = Relay(falhar_em={2})
    monkeypatch.setattr(coletora, "processar_janela", _processar(desfechos, []))
    monkeypatch.setattr(coletora, "publicar_outbox", relay)

    primeira = coletora.handler({"Records": [_registro("m1", 1)]}, CONTEXTO)

    assert primeira == {"batchItemFailures": [{"itemIdentifier": "m1"}]}

    # Reentrega da mesma mensagem: o relay do começo publica a continuação, e a mensagem
    # velha (lote 0) encontra a janela no lote 1.
    desfechos[1] = "obsoleta"
    segunda = coletora.handler({"Records": [_registro("m1", 1)]}, CONTEXTO)

    assert segunda == {"batchItemFailures": []}
    assert relay.chamadas == 4


def test_relay_do_fim_com_recusa_devolve_a_mensagem(
    coletora: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(coletora, "processar_janela", _processar({1: "continua"}, []))
    monkeypatch.setattr(coletora, "publicar_outbox", Relay(recusar_em={2}))

    resposta = coletora.handler({"Records": [_registro("m1", 1)]}, CONTEXTO)

    assert resposta == {"batchItemFailures": [{"itemIdentifier": "m1"}]}


def test_relay_do_comeco_falhando_nao_impede_a_coleta(
    coletora: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    vistas: Vistas = []
    monkeypatch.setattr(coletora, "processar_janela", _processar({1: "concluida"}, vistas))
    monkeypatch.setattr(coletora, "publicar_outbox", Relay(falhar_em={1}))

    resposta = coletora.handler({"Records": [_registro("m1", 1)]}, CONTEXTO)

    assert resposta == {"batchItemFailures": []}
    assert len(vistas) == 1


@pytest.mark.parametrize("nome", ["lambda_coletora", "lambda_planejador"])
def test_importar_nao_le_o_ssm_nem_abre_conexao(monkeypatch: pytest.MonkeyPatch, nome: str) -> None:
    import radar.comum.segredos as segredos

    lidas: list[str] = []
    monkeypatch.setattr(segredos, "_cliente_ssm", lambda: lidas.append("ssm"))
    monkeypatch.delenv("RADAR_DATABASE_URL", raising=False)
    modulo = importlib.import_module(f"radar.processador.{nome}")

    importlib.reload(modulo)

    assert lidas == []
    assert modulo._fabrica is None


# --- planejador -------------------------------------------------------------------------


@pytest.fixture
def planejador(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.setenv("RADAR_DATABASE_URL", "postgresql+psycopg://x:x@localhost:1/x")
    import radar.processador.lambda_planejador as m

    recarregado = importlib.reload(m)
    monkeypatch.setattr(recarregado, "configurar_logs", lambda: None)
    monkeypatch.setattr(recarregado, "_criar_sessao", lambda: _SessaoFalsa())
    monkeypatch.setattr(recarregado, "_criar_fila", lambda: object())
    monkeypatch.setattr(recarregado, "carregar_criterios", lambda _caminho: "criterios")
    yield recarregado


def test_planejador_planeja_enfileira_e_publica(
    planejador: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    pedidos: list[tuple[Any, date, int]] = []

    def planejar(sessao: Any, criterios: Any, hoje: date, recuo: int = 10) -> tuple[int, int]:
        pedidos.append((criterios, hoje, recuo))
        return 3, 11

    monkeypatch.setattr(planejador, "planejar_e_enfileirar", planejar)
    monkeypatch.setattr(planejador, "publicar_outbox", Relay())
    monkeypatch.setattr(planejador, "_hoje", lambda: date(2026, 11, 27))

    resposta = planejador.handler({}, CONTEXTO)

    assert resposta == {"janelas_novas": 3, "enfileiradas": 11, "publicadas": 1}
    assert pedidos == [("criterios", date(2026, 11, 27), 10)]


def test_planejador_hoje_e_em_brasilia(planejador: ModuleType) -> None:
    assert planejador._hoje() == datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def test_planejador_propaga_erro(planejador: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(planejador, "planejar_e_enfileirar", lambda *a, **k: (0, 1))
    monkeypatch.setattr(planejador, "publicar_outbox", Relay(falhar_em={1}))

    with pytest.raises(RuntimeError):
        planejador.handler({}, CONTEXTO)


def test_planejador_com_recusa_levanta(
    planejador: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(planejador, "planejar_e_enfileirar", lambda *a, **k: (0, 1))
    monkeypatch.setattr(planejador, "publicar_outbox", Relay(recusar_em={1}))

    with pytest.raises(RuntimeError, match="recusad"):
        planejador.handler({}, CONTEXTO)
