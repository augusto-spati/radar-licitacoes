"""Traces OpenTelemetry do radar: tracer próprio, propagação pela outbox/SQS, ciclo de vida.

O `TracerProvider` é deste módulo, não o global do OTel (que só aceita ser definido uma vez).
Sem configuração, `tracer()` é no-op e nada é exportado. Só a API do OTel é importada aqui;
o SDK e o exportador OTLP (assinado com SigV4) moram em `telemetria_sdk`, importado só por
`configurar_telemetria` com `RADAR_TELEMETRIA=xray`.

Telemetria nunca levanta exceção para quem chama: erro vira o log `telemetria_erro`.

Nada aqui injeta contexto em requisições HTTP: o `traceparent` só viaja na outbox e no
atributo da mensagem SQS.
"""

from __future__ import annotations

import functools
import logging
import re
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

from opentelemetry import trace
from opentelemetry.trace import Link, SpanContext, TraceFlags

from radar.comum.config import Config, obter_config, regiao_aws

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SpanExporter

logger = logging.getLogger("radar.telemetria")

NOME_TRACER = "radar"
_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")

_provedor: TracerProvider | None = None
_trava = threading.Lock()

P = ParamSpec("P")
R = TypeVar("R")


def tracer() -> trace.Tracer:
    """O tracer `radar` do provedor deste módulo; no-op sem configuração."""
    provedor = _provedor
    if provedor is None:
        return trace.NoOpTracer()
    return provedor.get_tracer(NOME_TRACER)


def usar_provedor(provedor: TracerProvider | None) -> None:
    """Troca o provedor do módulo (testes e `configurar_telemetria`). Não encerra o anterior."""
    global _provedor
    _provedor = provedor


def configurar_telemetria(
    config: Config, servico: str, exportador: SpanExporter | None = None
) -> None:
    """Liga os traces conforme `RADAR_TELEMETRIA`. Idempotente; nunca levanta.

    `desligada`: nada muda (no-op). `xray`: amostrador do radar, `BatchSpanProcessor` e o
    exportador dado ou, sem ele, o OTLP/HTTP assinado para o X-Ray da região.
    """
    with _trava:
        if _provedor is not None or config.telemetria == "desligada":
            return
        try:
            from radar.comum.telemetria_sdk import criar_provedor

            usar_provedor(criar_provedor(servico, config.traces_fracao, regiao_aws(), exportador))
        except Exception:
            logger.warning("telemetria_erro", extra={"etapa": "configurar"}, exc_info=True)


def descarregar(timeout_s: float = 2.0) -> None:
    """Exporta os spans pendentes, esperando no máximo `timeout_s`. Nunca levanta."""
    provedor = _provedor
    if provedor is None:
        return
    try:
        if not provedor.force_flush(int(timeout_s * 1000)):
            logger.warning("telemetria_erro", extra={"etapa": "descarregar", "motivo": "timeout"})
    except Exception:
        logger.warning("telemetria_erro", extra={"etapa": "descarregar"}, exc_info=True)


def encerrar(timeout_s: float = 5.0) -> None:
    """Descarrega e encerra o provedor (fim do processo), em no máximo ~`timeout_s`."""
    provedor = _provedor
    if provedor is None:
        return
    usar_provedor(None)
    descarregar(timeout_s)

    def _encerrar() -> None:
        try:
            provedor.shutdown()
        except Exception:
            logger.warning("telemetria_erro", extra={"etapa": "encerrar"}, exc_info=True)

    linha = threading.Thread(target=_encerrar, name="radar-telemetria-encerrar", daemon=True)
    linha.start()
    linha.join(timeout_s)
    if linha.is_alive():
        logger.warning("telemetria_erro", extra={"etapa": "encerrar", "motivo": "timeout"})


# ---------- Propagação (W3C traceparent) ----------


def traceparent_atual() -> str | None:
    """O `traceparent` W3C do span corrente, ou None fora de span (ou com o tracer no-op)."""
    contexto = trace.get_current_span().get_span_context()
    if not contexto.is_valid:
        return None
    # Só o bit "sampled": o resto das flags (ex. "random" do W3C nível 2) não viaja.
    amostrado = int(contexto.trace_flags) & 0x01
    return f"00-{contexto.trace_id:032x}-{contexto.span_id:016x}-{amostrado:02x}"


def link_de(traceparent: str | None) -> Link | None:
    """Link para o contexto do `traceparent`; None se ausente ou malformado."""
    if not traceparent:
        return None
    achado = _TRACEPARENT.match(traceparent.strip())
    if achado is None:
        return None
    trace_id, span_id, flags = (int(parte, 16) for parte in achado.groups())
    contexto = SpanContext(trace_id, span_id, is_remote=True, trace_flags=TraceFlags(flags))
    if not contexto.is_valid:
        return None
    return Link(contexto)


def atributos_sqs(traceparent: str | None) -> dict[str, dict[str, str]]:
    """`MessageAttributes` do `SendMessage*` com o `traceparent` (vazio sem contexto)."""
    if not traceparent:
        return {}
    return {"traceparent": {"DataType": "String", "StringValue": traceparent}}


def traceparent_do_registro(registro: Mapping[str, Any]) -> str | None:
    """`messageAttributes.traceparent.stringValue` de um registro do event source mapping."""
    atributos = registro.get("messageAttributes")
    if not isinstance(atributos, Mapping):
        return None
    atributo = atributos.get("traceparent")
    if not isinstance(atributo, Mapping):
        return None
    valor = atributo.get("stringValue")
    return valor if isinstance(valor, str) and valor else None


# ---------- Ciclo de vida nas portas de entrada ----------


def _configurar_tudo(servico: str) -> bool:
    """Telemetria e métricas pelo `Config`; devolve se este chamado ligou a telemetria."""
    from radar.comum import metricas

    ja_tinha = _provedor is not None
    try:
        config = obter_config()
    except Exception:
        logger.warning("telemetria_erro", extra={"etapa": "config"}, exc_info=True)
        return False
    configurar_telemetria(config, servico)
    metricas.configurar_metricas(config)
    return not ja_tinha and _provedor is not None


def _descarregar_metricas() -> None:
    from radar.comum import metricas

    metricas.descarregar()


def com_telemetria(servico: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorador de handler Lambda: configura na 1ª chamada e descarrega no `finally`.

    O `finally` roda mesmo quando o handler levanta: a Lambda congela o container logo
    depois de responder, e o que ficou no buffer só sairia na próxima invocação (ou nunca).
    """

    def decorar(handler: Callable[P, R]) -> Callable[P, R]:
        configurado = False

        @functools.wraps(handler)
        def envolto(*args: P.args, **kwargs: P.kwargs) -> R:
            nonlocal configurado
            if not configurado:
                _configurar_tudo(servico)
                configurado = True
            try:
                return handler(*args, **kwargs)
            finally:
                descarregar()
                _descarregar_metricas()

        return envolto

    return decorar


@contextmanager
def telemetria_do_processo(servico: str) -> Iterator[None]:
    """Ciclo de vida da CLI: configura e, na saída, descarrega métricas e encerra (≤ 5 s).

    Encerra só o provedor que ligou (um provedor de teste continua ligado). Nunca levanta.
    """
    ligou = _configurar_tudo(servico)
    try:
        yield
    finally:
        try:
            _descarregar_metricas()
        except Exception:
            logger.warning("telemetria_erro", extra={"etapa": "metricas"}, exc_info=True)
        if ligou:
            encerrar(5.0)
        else:
            descarregar()
