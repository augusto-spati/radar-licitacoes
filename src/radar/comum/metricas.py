"""Métricas customizadas do radar: catálogo fechado, um emissor por ambiente.

O plano grátis do CloudWatch tem 10 métricas customizadas, e cada combinação de nome e
dimensões é uma métrica cobrada. Por isso o catálogo é fechado no código: métrica ou dimensão
fora dele é `ValueError` (erro de programação, não de produção). O recorte por fonte que não
vale uma dimensão (o frescor) vai como propriedade: no EMF e no log, nunca como dimensão.

Emissores (`RADAR_METRICAS`):

- `emf` (Lambdas): uma linha JSON com `_aws` pelo `logging`; o CloudWatch Logs extrai a
  métrica sem chamada de rede no caminho da mensagem.
- `cloudwatch` (Fargate): agrega em memória (valores e contagens, o que preserva percentis) e
  chama `PutMetricData` em `descarregar()`.
- `desligada` (padrão): nada sai além do log `metrica_registrada`.

Emissão nunca levanta para quem chama: erro vira o log `metricas_erro`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from radar.comum.config import Config, regiao_aws

logger = logging.getLogger("radar.metricas")

NAMESPACE = "Radar"
FONTES = frozenset({"pncp", "sanepar", "compesa"})
MAXIMO_DATUMS = 1000  # por chamada de PutMetricData
MAXIMO_VALORES = 150  # valores distintos por datum


@dataclass(frozen=True)
class DefinicaoMetrica:
    nome: str
    unidade: str
    dimensoes: frozenset[str]
    valores_dimensao: Mapping[str, frozenset[str]]


def _metrica(nome: str, unidade: str, **valores: frozenset[str]) -> DefinicaoMetrica:
    return DefinicaoMetrica(nome, unidade, frozenset(valores), valores)


CATALOGO: Mapping[str, DefinicaoMetrica] = {
    d.nome: d
    for d in (
        # Horas desde o dado mais novo que já chegou a edital; o Maximum é a pior fonte.
        _metrica("FrescorHoras", "None"),
        # 0 ou 1 por página tentada: Average é a taxa de erro, SampleCount o número de páginas.
        _metrica("PaginaComErro", "None"),
        # Duração de cada tentativa HTTP, sem a pausa de cortesia nem a espera da retentativa.
        _metrica("LatenciaRequisicao", "Seconds", fonte=FONTES),
        # 1 ou 0 no fim de cada execução agendada.
        _metrica("ExecucaoOk", "None"),
    )
}


class Emissor(Protocol):
    def registrar(
        self,
        nome: str,
        valor: float,
        dimensoes: Mapping[str, str],
        propriedades: Mapping[str, str | float],
    ) -> None: ...

    def descarregar(self) -> None: ...


class EmissorEMF:
    """Embedded Metric Format: o `FormatadorJson` põe o `extra` no topo do objeto JSON."""

    def registrar(
        self,
        nome: str,
        valor: float,
        dimensoes: Mapping[str, str],
        propriedades: Mapping[str, str | float],
    ) -> None:
        definicao = CATALOGO[nome]
        bloco = {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [sorted(dimensoes)],
                    "Metrics": [{"Name": nome, "Unit": definicao.unidade}],
                }
            ],
        }
        logger.info(
            "metrica_registrada",
            extra={
                **propriedades,
                **dimensoes,
                "_aws": bloco,
                nome: valor,
                "metrica": nome,
                "valor": valor,
            },
        )

    def descarregar(self) -> None:
        return None


class EmissorCloudWatch:
    """Agrega por (nome, dimensões) e envia por `PutMetricData` em `descarregar()`."""

    def __init__(self, cliente: Any | None = None, regiao: str | None = None) -> None:
        self._cliente = cliente
        self._regiao = regiao
        self._trava = threading.Lock()
        self._acumulado: dict[tuple[str, tuple[tuple[str, str], ...]], dict[float, int]] = {}

    def _obter_cliente(self) -> Any:
        if self._cliente is None:
            import boto3  # type: ignore[import-untyped]

            self._cliente = boto3.client("cloudwatch", region_name=self._regiao or regiao_aws())
        return self._cliente

    def registrar(
        self,
        nome: str,
        valor: float,
        dimensoes: Mapping[str, str],
        propriedades: Mapping[str, str | float],
    ) -> None:
        chave = (nome, tuple(sorted(dimensoes.items())))
        with self._trava:
            contagens = self._acumulado.setdefault(chave, {})
            contagens[valor] = contagens.get(valor, 0) + 1

    def _datums(
        self, acumulado: dict[tuple[str, tuple[tuple[str, str], ...]], dict[float, int]]
    ) -> list[dict[str, Any]]:
        datums: list[dict[str, Any]] = []
        for (nome, dimensoes), contagens in acumulado.items():
            pares = list(contagens.items())
            for inicio in range(0, len(pares), MAXIMO_VALORES):
                fatia = pares[inicio : inicio + MAXIMO_VALORES]
                datums.append(
                    {
                        "MetricName": nome,
                        "Dimensions": [{"Name": k, "Value": v} for k, v in dimensoes],
                        "Values": [v for v, _ in fatia],
                        "Counts": [c for _, c in fatia],
                        "Unit": CATALOGO[nome].unidade,
                    }
                )
        return datums

    def descarregar(self) -> None:
        with self._trava:
            acumulado, self._acumulado = self._acumulado, {}
        if not acumulado:
            return
        datums = self._datums(acumulado)
        try:
            cliente = self._obter_cliente()
            for inicio in range(0, len(datums), MAXIMO_DATUMS):
                cliente.put_metric_data(
                    Namespace=NAMESPACE, MetricData=datums[inicio : inicio + MAXIMO_DATUMS]
                )
        except Exception:  # o que não saiu é perdido: métrica não segura a coleta
            logger.warning("metricas_erro", extra={"datums": len(datums)}, exc_info=True)


_emissor: Emissor | None = None


def emissor_atual() -> Emissor | None:
    return _emissor


def usar_emissor(emissor: Emissor | None) -> None:
    """Troca o emissor do módulo (testes e `configurar_metricas`)."""
    global _emissor
    _emissor = emissor


def configurar_metricas(config: Config, emissor: Emissor | None = None) -> None:
    """Escolhe o emissor por `RADAR_METRICAS`; idempotente (um emissor ligado fica)."""
    if _emissor is not None:
        return
    if emissor is not None:
        usar_emissor(emissor)
    elif config.metricas == "emf":
        usar_emissor(EmissorEMF())
    elif config.metricas == "cloudwatch":
        usar_emissor(EmissorCloudWatch())


def _validar(nome: str, dimensoes: Mapping[str, str]) -> None:
    definicao = CATALOGO.get(nome)
    if definicao is None:
        raise ValueError(f"métrica fora do catálogo: {nome!r}")
    if set(dimensoes) != definicao.dimensoes:
        raise ValueError(
            f"{nome}: dimensões {sorted(dimensoes)} != {sorted(definicao.dimensoes)} do catálogo"
        )
    for chave, valor in dimensoes.items():
        if valor not in definicao.valores_dimensao[chave]:
            raise ValueError(f"{nome}: {chave}={valor!r} fora do catálogo")


def registrar(
    nome: str,
    valor: float,
    dimensoes: Mapping[str, str] | None = None,
    propriedades: Mapping[str, str | float] | None = None,
) -> None:
    """Registra uma medição. Valida contra o catálogo mesmo desligado (pega o erro no CI)."""
    dimensoes = dict(dimensoes or {})
    propriedades = dict(propriedades or {})
    _validar(nome, dimensoes)
    emissor = _emissor
    if not isinstance(emissor, EmissorEMF):  # no EMF, a própria linha EMF é este log
        logger.info(
            "metrica_registrada",
            extra={**propriedades, **dimensoes, "metrica": nome, "valor": valor},
        )
    if emissor is None:
        return
    try:
        emissor.registrar(nome, float(valor), dimensoes, propriedades)
    except Exception:
        logger.warning("metricas_erro", extra={"metrica": nome}, exc_info=True)


def descarregar() -> None:
    """Envia o que o emissor acumulou (no EMF, nada a fazer). Nunca levanta."""
    emissor = _emissor
    if emissor is None:
        return
    try:
        emissor.descarregar()
    except Exception:
        logger.warning("metricas_erro", exc_info=True)
