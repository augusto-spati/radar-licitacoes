"""Cliente HTTP da API de consulta do PNCP, com retentativas."""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

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
    def __init__(self, http: httpx.Client, tentativas: int = 4, espera_base: float = 5.0) -> None:
        self._http = http
        self._tentativas = tentativas
        self._espera_base = espera_base
        self._espera_exponencial = wait_exponential_jitter(
            multiplier=espera_base, jitter=min(1.0, espera_base)
        )

    def buscar(
        self, endpoint: str, data_ini: date, data_fim: date, modalidade: int, pagina: int
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
        tentativas = Retrying(
            stop=stop_after_attempt(self._tentativas),
            wait=self._esperar,
            retry=retry_if_exception_type(_FalhaTransitoria),
            before_sleep=self._registrar_retentativa,
            reraise=True,
        )
        try:
            return tentativas(self._requisitar, caminho, parametros, pagina)
        except _FalhaTransitoria as falha:
            raise ErroPNCP(
                f"PNCP falhou após {self._tentativas} tentativas: {falha}", falha.status
            ) from falha

    def _requisitar(self, caminho: str, parametros: dict[str, Any], pagina: int) -> Pagina:
        try:
            resposta = self._http.get(caminho, params=parametros)
        except (httpx.TimeoutException, httpx.TransportError) as erro:
            raise _FalhaTransitoria(f"{type(erro).__name__}: {erro}") from erro
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
