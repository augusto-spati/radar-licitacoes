import logging
from collections.abc import Iterator, Sequence
from typing import Any

import pytest
import requests
from botocore.credentials import Credentials
from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import Decision
from opentelemetry.trace import Link, SpanContext, TraceFlags

from radar.comum import telemetria as t
from radar.comum.config import Config
from radar.comum.telemetria_sdk import AmostradorDoRadar, AssinaturaSigV4, criar_provedor

TP = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


@pytest.fixture(autouse=True)
def _sem_provedor() -> Iterator[None]:
    t.usar_provedor(None)
    yield
    t.usar_provedor(None)


def _link(flags: int) -> Link:
    return Link(SpanContext(1, 2, is_remote=True, trace_flags=TraceFlags(flags)))


def test_sem_configuracao_e_noop() -> None:
    with t.tracer().start_as_current_span("x") as span:
        assert not span.get_span_context().is_valid
        assert t.traceparent_atual() is None


def test_configurar_desligada_e_noop() -> None:
    t.configurar_telemetria(Config(telemetria="desligada"), "radar-teste")
    assert t.traceparent_atual() is None
    with t.tracer().start_as_current_span("x"):
        assert t.traceparent_atual() is None


def test_configurar_xray_usa_exportador_dado_e_e_idempotente() -> None:
    exportador = InMemorySpanExporter()
    t.configurar_telemetria(Config(telemetria="xray"), "radar-teste", exportador)
    t.configurar_telemetria(Config(telemetria="xray"), "radar-outro", InMemorySpanExporter())
    with t.tracer().start_as_current_span("x"):
        pass
    t.descarregar()
    (span,) = exportador.get_finished_spans()
    assert span.resource.attributes["service.name"] == "radar-teste"
    assert span.resource.attributes["deployment.environment"] == "prod"
    t.encerrar()


def test_amostrador_segue_pai() -> None:
    amostrador = AmostradorDoRadar(0.0)
    provedor = TracerProvider(sampler=AmostradorDoRadar(1.0))
    with provedor.get_tracer("t").start_as_current_span("pai"):
        from opentelemetry.context import get_current

        resultado = amostrador.should_sample(get_current(), 123, "filho")
    assert resultado.decision == Decision.RECORD_AND_SAMPLE


def test_amostrador_link_amostrado_forca() -> None:
    resultado = AmostradorDoRadar(0.0).should_sample(Context(), 123, "x", links=[_link(1)])
    assert resultado.decision == Decision.RECORD_AND_SAMPLE


def test_amostrador_link_nao_amostrado_nao_forca() -> None:
    resultado = AmostradorDoRadar(0.0).should_sample(Context(), 123, "x", links=[_link(0)])
    assert resultado.decision == Decision.DROP


def test_amostrador_fracao_zero_sem_link_descarta() -> None:
    assert AmostradorDoRadar(0.0).should_sample(Context(), 123, "x").decision == Decision.DROP
    assert (
        AmostradorDoRadar(1.0).should_sample(Context(), 123, "x").decision
        == Decision.RECORD_AND_SAMPLE
    )


def test_amostrador_recusa_fracao_invalida() -> None:
    with pytest.raises(ValueError):
        AmostradorDoRadar(1.5)


def test_traceparent_ida_e_volta(spans: InMemorySpanExporter) -> None:
    with t.tracer().start_as_current_span("x") as span:
        tp = t.traceparent_atual()
    assert tp is not None and tp.startswith("00-") and tp.endswith("-01")
    link = t.link_de(tp)
    assert link is not None
    assert link.context.span_id == span.get_span_context().span_id
    assert link.context.trace_id == span.get_span_context().trace_id
    assert link.context.trace_flags.sampled


@pytest.mark.parametrize(
    "valor",
    ["x", "", None, "00-" + "0" * 32 + "-b7ad6b7169203331-01", "01-abc", TP + "-extra"],
)
def test_link_de_malformado_e_none(valor: str | None) -> None:
    assert t.link_de(valor) is None


def test_atributos_sqs() -> None:
    assert t.atributos_sqs(TP) == {"traceparent": {"DataType": "String", "StringValue": TP}}
    assert t.atributos_sqs(None) == {}


def test_traceparent_do_registro() -> None:
    registro = {"messageAttributes": {"traceparent": {"stringValue": TP, "dataType": "String"}}}
    assert t.traceparent_do_registro(registro) == TP
    assert t.traceparent_do_registro({"messageAttributes": {}}) is None
    assert t.traceparent_do_registro({}) is None
    assert t.traceparent_do_registro({"messageAttributes": {"traceparent": "x"}}) is None


def test_assinatura_sigv4_assina_sem_enviar() -> None:
    credenciais = Credentials("AKIDEXEMPLO", "segredo", "token-da-sessao")
    auth = AssinaturaSigV4("us-east-2", credenciais=lambda: credenciais)
    pedido = requests.Request(
        "POST",
        "https://xray.us-east-2.amazonaws.com/v1/traces",
        data=b"x",
        headers={"Content-Type": "application/x-protobuf", "Content-Encoding": "gzip"},
    ).prepare()
    assinado = auth(pedido)
    autorizacao = assinado.headers["Authorization"]
    assert autorizacao.startswith("AWS4-HMAC-SHA256")
    assert "/us-east-2/xray/aws4_request" in autorizacao
    assert "content-encoding" in autorizacao  # o gzip entra na assinatura
    assert assinado.headers["X-Amz-Security-Token"] == "token-da-sessao"
    assert "X-Amz-Date" in assinado.headers


def test_assinatura_sem_credenciais_levanta_no_exportador() -> None:
    auth = AssinaturaSigV4("us-east-2", credenciais=lambda: None)
    pedido = requests.Request("POST", "https://xray.us-east-2.amazonaws.com/v1/traces").prepare()
    with pytest.raises(RuntimeError):
        auth(pedido)


class _ExportadorQuebrado(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        raise RuntimeError("X-Ray fora")

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        raise RuntimeError("X-Ray fora")


class _ProvedorQuebrado(TracerProvider):
    def __init__(self) -> None:
        super().__init__(shutdown_on_exit=False)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        raise RuntimeError("quebrado")

    def shutdown(self) -> None:
        raise RuntimeError("quebrado")


def test_descarregar_engole_erro(caplog: pytest.LogCaptureFixture) -> None:
    t.usar_provedor(_ProvedorQuebrado())
    with caplog.at_level(logging.WARNING):
        t.descarregar()
        t.encerrar()
    assert any(r.getMessage() == "telemetria_erro" for r in caplog.records)


def test_exportador_quebrado_nao_propaga() -> None:
    provedor = criar_provedor("radar-teste", 1.0, "us-east-2", _ExportadorQuebrado())
    t.usar_provedor(provedor)
    with t.tracer().start_as_current_span("x"):
        pass
    t.descarregar(0.5)
    t.encerrar(0.5)


def test_com_telemetria_descarrega_no_finally(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[str] = []
    monkeypatch.setattr(t, "descarregar", lambda timeout_s=2.0: chamadas.append("traces"))
    monkeypatch.setattr(t, "_descarregar_metricas", lambda: chamadas.append("metricas"))

    @t.com_telemetria("radar-teste")
    def handler(evento: dict[str, Any], contexto: Any) -> None:
        raise RuntimeError("falhou")

    with pytest.raises(RuntimeError):
        handler({}, None)
    assert chamadas == ["traces", "metricas"]


def test_com_telemetria_devolve_o_resultado() -> None:
    @t.com_telemetria("radar-teste")
    def handler(evento: dict[str, Any], contexto: Any) -> dict[str, int]:
        return {"ok": 1}

    assert handler({}, None) == {"ok": 1}


def test_importar_telemetria_nao_importa_o_sdk() -> None:
    import subprocess
    import sys

    codigo = (
        "import sys, radar.comum.telemetria, radar.processador.lambda_handler;"
        "print(any(m.startswith(('opentelemetry.sdk', 'opentelemetry.exporter', 'requests'))"
        " for m in sys.modules))"
    )
    saida = subprocess.run(
        [sys.executable, "-c", codigo], capture_output=True, text=True, check=True
    ).stdout
    assert saida.strip() == "False"
