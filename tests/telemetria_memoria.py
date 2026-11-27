"""Telemetria e métricas em memória para os testes (nada sai do processo)."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

import pytest

from radar.comum import metricas as modulo_metricas


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
