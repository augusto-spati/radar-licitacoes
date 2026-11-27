import json
import logging
import re
from datetime import datetime

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from radar.comum import configurar_logs
from radar.comum.telemetria import tracer


def test_log_e_json_com_campos_extras(capsys: pytest.CaptureFixture[str]) -> None:
    configurar_logs("INFO")
    logging.getLogger("radar.x").info("janela_concluida", extra={"janela_id": 7})

    saida = capsys.readouterr()
    linhas = [linha for linha in (saida.out + saida.err).splitlines() if linha.strip()]
    assert len(linhas) == 1
    registro = json.loads(linhas[0])
    assert registro["evento"] == "janela_concluida"
    assert registro["janela_id"] == 7
    assert registro["nivel"] == "INFO"
    datetime.fromisoformat(registro["momento"])


def _ultima_linha(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    saida = capsys.readouterr()
    linhas = [linha for linha in (saida.out + saida.err).splitlines() if linha.strip()]
    registro: dict[str, object] = json.loads(linhas[-1])
    return registro


def test_log_dentro_de_span_tem_trace_id(
    capsys: pytest.CaptureFixture[str], spans: InMemorySpanExporter
) -> None:
    configurar_logs("INFO")
    with tracer().start_as_current_span("x") as span:
        logging.getLogger("radar.x").info("dentro")
    registro = _ultima_linha(capsys)
    contexto = span.get_span_context()
    assert registro["trace_id"] == f"{contexto.trace_id:032x}"
    assert registro["span_id"] == f"{contexto.span_id:016x}"
    assert re.fullmatch(r"1-[0-9a-f]{8}-[0-9a-f]{24}", str(registro["xray_trace_id"]))
    assert (
        str(registro["xray_trace_id"]).replace("1-", "", 1).replace("-", "")
        == (registro["trace_id"])
    )


def test_log_fora_de_span_sem_trace_id(
    capsys: pytest.CaptureFixture[str], spans: InMemorySpanExporter
) -> None:
    configurar_logs("INFO")
    logging.getLogger("radar.x").info("fora")
    registro = _ultima_linha(capsys)
    assert "trace_id" not in registro and "span_id" not in registro
