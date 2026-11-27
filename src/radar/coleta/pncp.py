"""Cliente HTTP da API de consulta do PNCP, com retentativas."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
from opentelemetry.trace import SpanKind, StatusCode
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from radar.comum import metricas
from radar.comum.telemetria import tracer

logger = logging.getLogger(__name__)

URL_BASE = "https://pncp.gov.br/api/consulta/v1"
TIMEOUT_SEGUNDOS = 90.0  # o PNCP leva de 20 a 26 s por resposta
TAMANHO_PAGINA = 50  # máximo aceito pelo PNCP
STATUS_RETENTAVEIS = frozenset({429, 500, 502, 503, 504})
FATOR_ESPERA_429 = 12  # o 429 vem sem Retry-After; espera mínima = espera_base * 12
CAMINHOS = {
    "publicacao": "/contratacoes/publicacao",
    "atualizacao": "/contratacoes/atualizacao",
}


def criar_http() -> httpx.Client:
    return httpx.Client(base_url=URL_BASE, timeout=TIMEOUT_SEGUNDOS)


@dataclass(frozen=True)
class Pagina:
    registros: list[dict[str, Any]]
    total_paginas: int
    numero_pagina: int
    bruto: dict[str, Any]


class ErroPNCP(Exception):
    def __init__(self, mensagem: str, status: int | None = None) -> None:
        super().__init__(mensagem)
        self.status = status


class _FalhaTransitoria(Exception):
    def __init__(self, mensagem: str, status: int | None = None) -> None:
        super().__init__(mensagem)
        self.status = status


def _interpretar(resposta: httpx.Response, pagina: int) -> Pagina:
    """Valida o corpo de um 200; corpo inesperado é falha, nunca página vazia."""
    try:
        corpo = resposta.json()
    except ValueError as erro:
        raise _FalhaTransitoria(f"corpo 200 não é JSON: {resposta.text[:200]!r}", 200) from erro
    registros = corpo.get("data") if isinstance(corpo, dict) else None
    total = corpo.get("totalPaginas") if isinstance(corpo, dict) else None
    if not isinstance(registros, list) or not isinstance(total, int) or isinstance(total, bool):
        raise _FalhaTransitoria(f"corpo 200 com formato inesperado: {str(corpo)[:200]}", 200)
    return Pagina(
        registros=registros,
        total_paginas=total,
        numero_pagina=corpo.get("numeroPagina", pagina),
        bruto=corpo,
    )


class ClientePNCP:
    """Busca páginas do PNCP com retentativa (tenacity).

    Telemetria: um span `pncp.buscar` por página e um `GET` (CLIENT) por tentativa; a
    latência de cada tentativa (só o `http.get`, sem a espera da retentativa) vai para
    `LatenciaRequisicao{fonte=pncp}` e o desfecho da página para `PaginaComErro` (0/1).
    Nenhum cabeçalho de trace sai para o PNCP.
    """

    def __init__(
        self,
        http: httpx.Client,
        tentativas: int = 4,
        espera_base: float = 5.0,
        cronometro: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._cronometro = cronometro
        self._tentativas = tentativas
        self._espera_base = espera_base
        self._espera_exponencial = wait_exponential_jitter(
            multiplier=espera_base, jitter=min(1.0, espera_base)
        )

    def buscar(
        self,
        endpoint: str,
        data_ini: date,
        data_fim: date,
        modalidade: int,
        pagina: int,
        uf: str | None = None,
    ) -> Pagina:
        if endpoint not in CAMINHOS:
            raise ValueError(f"endpoint desconhecido: {endpoint!r}")
        caminho = CAMINHOS[endpoint]
        parametros = {
            "dataInicial": data_ini.strftime("%Y%m%d"),
            "dataFinal": data_fim.strftime("%Y%m%d"),
            "codigoModalidadeContratacao": modalidade,
            "pagina": pagina,
            "tamanhoPagina": TAMANHO_PAGINA,
        }
        if uf is not None:
            parametros["uf"] = uf
        tentativas = Retrying(
            stop=stop_after_attempt(self._tentativas),
            wait=self._esperar,
            retry=retry_if_exception_type(_FalhaTransitoria),
            before_sleep=self._registrar_retentativa,
            reraise=True,
        )
        contador = [0]
        with tracer().start_as_current_span(
            "pncp.buscar",
            attributes={"radar.endpoint": endpoint, "radar.pagina": pagina},
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                resultado = tentativas(self._requisitar, caminho, parametros, pagina, contador)
            except (_FalhaTransitoria, ErroPNCP) as falha:
                erro = (
                    falha
                    if isinstance(falha, ErroPNCP)
                    else ErroPNCP(
                        f"PNCP falhou após {self._tentativas} tentativas: {falha}", falha.status
                    )
                )
                span.set_attribute("radar.tentativas", contador[0])
                span.record_exception(erro)
                span.set_status(StatusCode.ERROR, str(erro)[:200])
                metricas.registrar("PaginaComErro", 1)
                if erro is falha:
                    raise
                raise erro from falha
            span.set_attribute("radar.tentativas", contador[0])
            metricas.registrar("PaginaComErro", 0)
            return resultado

    def _requisitar(
        self, caminho: str, parametros: dict[str, Any], pagina: int, contador: list[int]
    ) -> Pagina:
        contador[0] += 1
        host = self._http.base_url.host or "pncp.gov.br"
        with tracer().start_as_current_span(
            "GET",
            kind=SpanKind.CLIENT,
            attributes={
                "http.request.method": "GET",
                "server.address": host,
                "radar.fonte": "pncp",
                "radar.tentativa": contador[0],
            },
        ) as span:
            inicio = self._cronometro()
            status: int | None = None
            try:
                resposta = self._http.get(caminho, params=parametros)
                status = resposta.status_code
            except (httpx.TimeoutException, httpx.TransportError) as erro:
                span.set_attribute("error.type", type(erro).__name__)
                raise _FalhaTransitoria(f"{type(erro).__name__}: {erro}") from erro
            finally:
                duracao = self._cronometro() - inicio
                metricas.registrar("LatenciaRequisicao", duracao, {"fonte": "pncp"})
                logger.info(
                    "requisicao_http",
                    extra={
                        "fonte": "pncp",
                        "host": host,
                        "status": status,
                        "duracao_s": round(duracao, 3),
                        "tentativa": contador[0],
                    },
                )
            span.set_attribute("http.response.status_code", status)
            if status >= 400:
                span.set_attribute("error.type", str(status))
                span.set_status(StatusCode.ERROR)
            return self._interpretar_status(resposta, pagina)

    def _interpretar_status(self, resposta: httpx.Response, pagina: int) -> Pagina:
        status = resposta.status_code
        if status in STATUS_RETENTAVEIS:
            raise _FalhaTransitoria(f"HTTP {status}", status)
        if status >= 400:
            raise ErroPNCP(f"HTTP {status}: {resposta.text[:200]}", status)
        if status == 204:
            return Pagina(registros=[], total_paginas=0, numero_pagina=pagina, bruto={})
        return _interpretar(resposta, pagina)

    def _esperar(self, estado: RetryCallState) -> float:
        espera = self._espera_exponencial(estado)
        falha = estado.outcome.exception() if estado.outcome else None
        if isinstance(falha, _FalhaTransitoria) and falha.status == 429:
            espera = max(espera, self._espera_base * FATOR_ESPERA_429)
        return espera

    def _registrar_retentativa(self, estado: RetryCallState) -> None:
        falha = estado.outcome.exception() if estado.outcome else None
        logger.warning(
            "pncp_retentativa",
            extra={
                "tentativa": estado.attempt_number,
                "motivo": str(falha),
                "status": getattr(falha, "status", None),
                "espera_s": round(estado.next_action.sleep, 2) if estado.next_action else None,
            },
        )
