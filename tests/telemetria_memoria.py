"""Telemetria e métricas em memória para os testes (nada sai do processo)."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from radar.comum import metricas as modulo_metricas
from radar.comum import telemetria
from radar.comum.telemetria_sdk import AmostradorDoRadar


@dataclass(frozen=True)
class Registro:
    nome: str
    valor: float
    dimensoes: Mapping[str, str]
    propriedades: Mapping[str, str | float]


@dataclass
class EmissorEmMemoria:
    registros: list[Registro] = field(default_factory=list)
    descarregamentos: int = 0

    def registrar(
        self,
        nome: str,
        valor: float,
        dimensoes: Mapping[str, str],
        propriedades: Mapping[str, str | float],
    ) -> None:
        self.registros.append(Registro(nome, valor, dict(dimensoes), dict(propriedades)))

    def descarregar(self) -> None:
        self.descarregamentos += 1

    def valores(self, nome: str, **dimensoes: str) -> list[float]:
        return [
            r.valor
            for r in self.registros
            if r.nome == nome and all(r.dimensoes.get(k) == v for k, v in dimensoes.items())
        ]


@pytest.fixture
def metricas() -> Iterator[EmissorEmMemoria]:
    emissor = EmissorEmMemoria()
    modulo_metricas.usar_emissor(emissor)
    try:
        yield emissor
    finally:
        modulo_metricas.usar_emissor(None)


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    """Provedor em memória (fração 1.0, exportação síncrona), sem o provedor global do OTel."""
    exportador = InMemorySpanExporter()
    provedor = TracerProvider(sampler=AmostradorDoRadar(1.0), shutdown_on_exit=False)
    provedor.add_span_processor(SimpleSpanProcessor(exportador))
    telemetria.usar_provedor(provedor)
    try:
        yield exportador
    finally:
        telemetria.usar_provedor(None)
        provedor.shutdown()
