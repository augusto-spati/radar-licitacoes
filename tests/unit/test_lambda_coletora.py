"""Handlers da coletora e do planejador, sem banco nem AWS de verdade."""

import importlib
import json
from collections.abc import Iterator
from datetime import date, datetime
from types import ModuleType, SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from radar.coleta import ResultadoFatia
from radar.comum import Mensagem, ResumoPublicacao
from radar.comum.telemetria import traceparent_atual
from telemetria_memoria import EmissorEmMemoria


def _registro(message_id: str, janela_id: int, lote: int = 0) -> dict[str, Any]:
    carga = {"janela_id": janela_id, "lote": lote}
    corpo = json.dumps({"tipo": "coletar_janela", "carga": carga, "outbox_id": 100 + janela_id})
    return {"messageId": message_id, "body": corpo, "eventSource": "aws:sqs"}


class _SessaoFalsa:
    def __enter__(self) -> "_SessaoFalsa":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _contexto(restante_ms: int = 900_000) -> SimpleNamespace:
    return SimpleNamespace(aws_request_id="req-1", get_remaining_time_in_millis=lambda: restante_ms)


CONTEXTO = _contexto()
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
    monkeypatch.setattr(recarregado, "_nao_publicadas", lambda ids: set())
    yield recarregado


def _continuacao(janela_id: int) -> int:
    return 500 + janela_id


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
    ) -> ResultadoFatia:
        assert isinstance(sessao, _SessaoFalsa)
        vistas.append((mensagem, token, orcamento_s))
        janela_id = mensagem.carga["janela_id"]
        desfecho = desfechos[janela_id]
        if isinstance(desfecho, BaseException):
            raise desfecho
        continuacoes = (_continuacao(janela_id),) if desfecho in ("continua", "dividida") else ()
        return ResultadoFatia(desfecho, 0, 0, continuacoes=continuacoes)

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
    monkeypatch.setattr(coletora, "_nao_publicadas", lambda ids: set(ids))

    primeira = coletora.handler({"Records": [_registro("m1", 1)]}, CONTEXTO)

    assert primeira == {"batchItemFailures": [{"itemIdentifier": "m1"}]}

    # Reentrega da mesma mensagem: o relay do começo publica a continuação, e a mensagem
    # velha (lote 0) encontra a janela no lote 1.
    desfechos[1] = "obsoleta"
    segunda = coletora.handler({"Records": [_registro("m1", 1)]}, CONTEXTO)

    assert segunda == {"batchItemFailures": []}
    assert relay.chamadas == 4


def test_so_volta_a_mensagem_cuja_continuacao_nao_saiu(
    coletora: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    pedidas: list[set[int]] = []

    def nao_publicadas(ids: set[int]) -> set[int]:
        pedidas.append(set(ids))
        return {_continuacao(2)}

    desfechos = {1: "continua", 2: "continua", 3: "concluida", 4: "dividida"}
    monkeypatch.setattr(coletora, "processar_janela", _processar(desfechos, []))
    monkeypatch.setattr(coletora, "publicar_outbox", Relay(recusar_em={2}))
    monkeypatch.setattr(coletora, "_nao_publicadas", nao_publicadas)
    registros = [_registro(f"m{n}", n) for n in (1, 2, 3, 4)]

    resposta = coletora.handler({"Records": registros}, CONTEXTO)

    assert resposta == {"batchItemFailures": [{"itemIdentifier": "m2"}]}
    assert pedidas == [{_continuacao(1), _continuacao(2), _continuacao(4)}]


def test_relay_do_fim_caido_confirma_quem_nao_tem_continuacao(
    coletora: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def conferencia_caida(ids: set[int]) -> set[int]:
        raise RuntimeError("banco fora do ar")

    monkeypatch.setattr(
        coletora, "processar_janela", _processar({1: "concluida", 2: "continua"}, [])
    )
    monkeypatch.setattr(coletora, "publicar_outbox", Relay(falhar_em={2}))
    monkeypatch.setattr(coletora, "_nao_publicadas", conferencia_caida)

    resposta = coletora.handler({"Records": [_registro("m1", 1), _registro("m2", 2)]}, CONTEXTO)

    assert resposta == {"batchItemFailures": [{"itemIdentifier": "m2"}]}


@pytest.mark.parametrize(
    ("restante_ms", "orcamento"), [(900_000, 540), (600_000, 240), (300_000, 0)]
)
def test_orcamento_respeita_o_tempo_restante(
    coletora: ModuleType, monkeypatch: pytest.MonkeyPatch, restante_ms: int, orcamento: float
) -> None:
    vistas: Vistas = []
    monkeypatch.setattr(coletora, "processar_janela", _processar({1: "concluida"}, vistas))
    monkeypatch.setattr(coletora, "publicar_outbox", Relay())

    coletora.handler({"Records": [_registro("m1", 1)]}, _contexto(restante_ms))

    assert [orc for *_, orc in vistas] == [orcamento]


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


# --- telemetria -------------------------------------------------------------------------

TP = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def test_coletora_leva_o_traceparent_da_mensagem(
    coletora: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    vistas: Vistas = []
    monkeypatch.setattr(
        coletora, "processar_janela", _processar({1: "concluida", 2: "concluida"}, vistas)
    )
    monkeypatch.setattr(coletora, "publicar_outbox", Relay())
    registro = {
        **_registro("m1", 1),
        "messageAttributes": {"traceparent": {"stringValue": TP, "dataType": "String"}},
    }
    coletora.handler({"Records": [registro, _registro("m2", 2)]}, CONTEXTO)
    assert [m.traceparent for m, _, _ in vistas] == [TP, None]


def test_coletora_sem_atributo_segue_sem_contexto(
    coletora: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    vistas: Vistas = []
    monkeypatch.setattr(coletora, "processar_janela", _processar({1: "concluida"}, vistas))
    monkeypatch.setattr(coletora, "publicar_outbox", Relay())
    assert coletora.handler({"Records": [_registro("m1", 1)]}, CONTEXTO) == {
        "batchItemFailures": []
    }
    assert vistas[0][0].traceparent is None


def test_planejador_e_raiz_e_registra_execucao_ok(
    planejador: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    spans: InMemorySpanExporter,
    metricas: EmissorEmMemoria,
) -> None:
    contextos: list[str | None] = []

    def planejar(sessao: Any, criterios: Any, hoje: date, recuo: int = 10) -> tuple[int, int]:
        contextos.append(traceparent_atual())  # o que enfileirar() gravaria na outbox
        return 3, 11

    monkeypatch.setattr(planejador, "planejar_e_enfileirar", planejar)
    monkeypatch.setattr(planejador, "publicar_outbox", Relay())
    planejador.handler({}, CONTEXTO)
    (span,) = [s for s in spans.get_finished_spans() if s.name == "coleta.planejar"]
    assert span.parent is None
    assert contextos[0] is not None and contextos[0].split("-")[2] == f"{span.context.span_id:016x}"
    assert metricas.valores("ExecucaoOk") == [1.0]


def test_planejador_com_erro_registra_execucao_ok_zero(
    planejador: ModuleType, monkeypatch: pytest.MonkeyPatch, metricas: EmissorEmMemoria
) -> None:
    monkeypatch.setattr(planejador, "planejar_e_enfileirar", lambda *a, **k: (0, 1))
    monkeypatch.setattr(planejador, "publicar_outbox", Relay(recusar_em={1}))
    with pytest.raises(RuntimeError):
        planejador.handler({}, CONTEXTO)
    assert metricas.valores("ExecucaoOk") == [0.0]
    assert metricas.descarregamentos >= 1  # com_telemetria descarrega no finally
