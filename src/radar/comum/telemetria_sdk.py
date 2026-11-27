"""SDK do OpenTelemetry: amostrador do radar, assinatura SigV4 e o provedor com exportador OTLP.

Importado só quando a telemetria é ligada (`configurar_telemetria` com `xray`) e nos testes:
importar um handler não paga o SDK, o `requests` nem o `protobuf`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

import requests
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import (
    Decision,
    ParentBased,
    Sampler,
    SamplingResult,
    TraceIdRatioBased,
)
from opentelemetry.trace import Link, SpanKind
from opentelemetry.trace.span import TraceState
from opentelemetry.util.types import Attributes

from radar import __version__

logger = logging.getLogger("radar.telemetria")

# `botocore.credentials.Credentials` (o botocore não tem tipos): congela com
# `get_frozen_credentials()`.
Credentials = Any

TIMEOUT_EXPORTADOR_S = 10


class _RaizComLinks(Sampler):
    """Span sem pai: amostra se algum link foi amostrado; senão, pela fração do trace id."""

    def __init__(self, fracao: float) -> None:
        self._fracao = fracao
        self._razao = TraceIdRatioBased(fracao)

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        if links and any(link.context.trace_flags.sampled for link in links):
            return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes)
        return self._razao.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    def get_description(self) -> str:
        return f"RaizComLinks{{{self._fracao}}}"


class AmostradorDoRadar(ParentBased):
    """1. com pai, segue o pai; 2. sem pai e com link amostrado, amostra; 3. senão, a fração.

    A regra 2 mantém a cadeia inteira: o consumo de uma página cuja coleta foi amostrada
    também é, mesmo sendo outro trace (ligado por link, não por pai).
    """

    def __init__(self, fracao: float) -> None:
        if not 0.0 <= fracao <= 1.0:
            raise ValueError(f"fração de traces fora de 0..1: {fracao}")
        super().__init__(root=_RaizComLinks(fracao))


def _credenciais_padrao() -> Callable[[], Credentials | None]:
    import botocore.session  # type: ignore[import-untyped]

    sessao = botocore.session.get_session()
    cache: list[Credentials] = []
    avisou = False

    def obter() -> Credentials | None:
        # Ausência não vai para o cache: a próxima assinatura tenta de novo (avisa uma vez).
        nonlocal avisou
        if not cache:
            credenciais = sessao.get_credentials()
            if credenciais is None:
                if not avisou:
                    avisou = True
                    logger.warning("telemetria_erro", extra={"etapa": "credenciais"})
                return None
            cache.append(credenciais)
        return cache[0]

    return obter


class AssinaturaSigV4(requests.auth.AuthBase):
    """Assina cada requisição do exportador com SigV4 (serviço `xray` por padrão).

    As credenciais vêm da cadeia padrão do botocore (role da task ou da Lambda) e são
    congeladas a cada assinatura, o que as renova quando a role troca as temporárias.
    """

    def __init__(
        self,
        regiao: str,
        servico: str = "xray",
        credenciais: Callable[[], Credentials | None] | None = None,
    ) -> None:
        self._regiao = regiao
        self._servico = servico
        self._credenciais = credenciais or _credenciais_padrao()

    def __call__(self, r: requests.PreparedRequest) -> requests.PreparedRequest:
        from botocore.auth import SigV4Auth  # type: ignore[import-untyped]
        from botocore.awsrequest import AWSRequest  # type: ignore[import-untyped]

        credenciais = self._credenciais()
        if credenciais is None:
            raise RuntimeError("sem credenciais AWS para assinar o envio dos traces")
        corpo: Any = r.body
        if isinstance(corpo, str):
            corpo = corpo.encode()
        cabecalhos = {
            k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-encoding")
        }
        pedido = AWSRequest(method=r.method, url=r.url, data=corpo or b"", headers=cabecalhos)
        SigV4Auth(credenciais.get_frozen_credentials(), self._servico, self._regiao).add_auth(
            pedido
        )
        for chave in ("Authorization", "X-Amz-Date", "X-Amz-Security-Token"):
            if chave in pedido.headers:
                r.headers[chave] = pedido.headers[chave]
        return r


def exportador_xray(regiao: str) -> SpanExporter:
    """OTLP/HTTP (protobuf, gzip, timeout 10 s) para o endpoint de traces do X-Ray."""
    from opentelemetry.exporter.otlp.proto.http import Compression
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    sessao = requests.Session()
    sessao.auth = AssinaturaSigV4(regiao)
    return OTLPSpanExporter(
        endpoint=f"https://xray.{regiao}.amazonaws.com/v1/traces",
        session=sessao,
        compression=Compression.Gzip,
        timeout=TIMEOUT_EXPORTADOR_S,
    )


def criar_provedor(
    servico: str, fracao: float, regiao: str, exportador: SpanExporter | None = None
) -> TracerProvider:
    """Provedor com o amostrador do radar e exportação em lote (thread própria).

    `shutdown_on_exit=False`: quem encerra é `telemetria.encerrar`, com limite de tempo; o
    `atexit` do SDK poderia segurar a saída do processo esperando o X-Ray.
    """
    recurso = Resource.create(
        {
            "service.name": servico,
            "service.version": __version__,
            "deployment.environment": "prod",
            "cloud.provider": "aws",
            "cloud.region": regiao,
        }
    )
    provedor = TracerProvider(
        sampler=AmostradorDoRadar(fracao), resource=recurso, shutdown_on_exit=False
    )
    provedor.add_span_processor(BatchSpanProcessor(exportador or exportador_xray(regiao)))
    return provedor
