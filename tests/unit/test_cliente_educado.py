"""Telemetria do ClienteEducado: span por GET, latência sem a pausa, páginas com erro."""

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from radar.coleta import ClienteEducado, ErroFonte
from radar.comum.telemetria import tracer
from telemetria_memoria import EmissorEmMemoria


class Relogio:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _cliente(
    respostas: list[httpx.Response], chamadas: list[httpx.Request], relogio: Relogio | None = None
) -> ClienteEducado:
    fila = iter(respostas)
    relogio = relogio or Relogio()

    def tratar(requisicao: httpx.Request) -> httpx.Response:
        chamadas.append(requisicao)
        relogio.t += 0.25  # a fonte leva 0,25 s para responder
        return next(fila)

    def dormir(segundos: float) -> None:
        relogio.t += segundos  # a pausa de cortesia passa no mesmo relógio

    return ClienteEducado(
        httpx.Client(transport=httpx.MockTransport(tratar)),
        dormir=dormir,
        fonte="sanepar",
        cronometro=relogio,
    )


def test_latencia_nao_inclui_pausa(metricas: EmissorEmMemoria) -> None:
    chamadas: list[httpx.Request] = []
    cliente = _cliente([httpx.Response(200, text="a"), httpx.Response(200, text="b")], chamadas)
    cliente.get("https://sanepar.test/p1")
    cliente.get("https://sanepar.test/p2")  # dorme 3 s antes
    latencias = metricas.valores("LatenciaRequisicao", fonte="sanepar")
    assert latencias == [0.25, 0.25]
    assert metricas.valores("PaginaComErro") == [0.0, 0.0]


def test_get_vira_span_client(spans: InMemorySpanExporter) -> None:
    cliente = _cliente([httpx.Response(200, text="a")], [])
    cliente.get("https://sanepar.test/p1?x=1")
    (span,) = spans.get_finished_spans()
    assert span.name == "GET" and span.kind == SpanKind.CLIENT
    assert span.attributes is not None
    assert span.attributes["server.address"] == "sanepar.test"
    assert span.attributes["http.response.status_code"] == 200
    assert span.attributes["radar.fonte"] == "sanepar"
    assert "x=1" not in str(dict(span.attributes))


def test_erro_conta_pagina_com_erro(
    spans: InMemorySpanExporter, metricas: EmissorEmMemoria
) -> None:
    cliente = _cliente([httpx.Response(403)], [])
    with pytest.raises(ErroFonte):
        cliente.get("https://sanepar.test/p1")
    assert metricas.valores("PaginaComErro") == [1.0]
    (span,) = spans.get_finished_spans()
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes is not None and span.attributes["error.type"] == "403"


def test_fonte_fora_do_catalogo_nao_registra_latencia(metricas: EmissorEmMemoria) -> None:
    cliente = ClienteEducado(
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        dormir=lambda _s: None,
    )
    cliente.get("https://x.test/")
    assert metricas.valores("LatenciaRequisicao") == []
    assert metricas.valores("PaginaComErro") == [0.0]


def test_nenhum_traceparent_nas_fontes(spans: InMemorySpanExporter) -> None:
    chamadas: list[httpx.Request] = []
    cliente = _cliente([httpx.Response(200), httpx.Response(200)], chamadas)
    with tracer().start_as_current_span("coleta.retrato"):
        cliente.get("https://sanepar.test/p1")
        cliente.get("https://sanepar.test/p2", headers={"Accept": "text/html"})
    for requisicao in chamadas:
        nomes = {k.lower() for k in requisicao.headers}
        assert not nomes & {"traceparent", "tracestate", "x-amzn-trace-id"}
