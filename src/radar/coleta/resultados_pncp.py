"""Itens de uma contratação do PNCP e o resultado de cada um (pncp-api, sem token)."""

import logging
import time
from typing import Any

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

URL_BASE = "https://pncp.gov.br/pncp-api/v1"
TIMEOUT_SEGUNDOS = 60.0
USER_AGENT = "radar-licitacoes-estudo (github.com/augusto-spati/radar-licitacoes)"
STATUS_RETENTAVEIS = frozenset({429, 500, 502, 503, 504})
TAMANHO_PAGINA = 50  # máximo aceito pelo PNCP
FATOR_ESPERA_429 = 12  # o 429 vem sem Retry-After
MAX_PAGINAS = 200  # trava contra paginação que nunca termina


class ErroResultados(Exception):
    pass


class _FalhaTransitoria(Exception):
    def __init__(self, mensagem: str, status: int | None = None) -> None:
        super().__init__(mensagem)
        self.status = status


def criar_http_resultados() -> httpx.Client:
    return httpx.Client(
        base_url=URL_BASE,
        timeout=TIMEOUT_SEGUNDOS,
        headers={"User-Agent": USER_AGENT},
    )


class ClienteResultadosPNCP:
    """Itens e resultados de uma contratação, uma requisição por vez e com pausa entre elas."""

    def __init__(
        self,
        http: httpx.Client,
        pausa_s: float = 1.0,
        tentativas: int = 3,
        espera_base: float = 5.0,
    ) -> None:
        self._http = http
        self._pausa = pausa_s
        self._tentativas = tentativas
        self._espera_base = espera_base
        self._espera_exponencial = wait_exponential(multiplier=espera_base, max=60)
        self._ultima: float | None = None

    def fechar(self) -> None:
        self._http.close()

    def itens_com_resultados(self, cnpj: str, ano: int, sequencial: int) -> list[dict[str, Any]]:
        base = f"/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens"
        itens: list[dict[str, Any]] = []
        vistos: set[Any] = set()
        for pagina in range(1, MAX_PAGINAS + 1):
            lote = self._lista(base, {"pagina": pagina, "tamanhoPagina": TAMANHO_PAGINA})
            novos = [i for i in lote if i.get("numeroItem") not in vistos]
            if not novos:  # servidor repetindo a página: não há mais o que ler
                break
            vistos.update(i.get("numeroItem") for i in novos)
            itens.extend(novos)
            if len(lote) < TAMANHO_PAGINA:
                break
        else:
            raise ErroResultados(f"mais de {MAX_PAGINAS} páginas de itens em {base}")
        for item in itens:
            if item.get("temResultado"):
                item["resultados"] = self._lista(f"{base}/{item['numeroItem']}/resultados", None)
        return itens

    def _lista(self, caminho: str, params: dict[str, Any] | None) -> list[dict[str, Any]]:
        corpo = self._com_tentativas(caminho, params)
        if corpo is None:
            return []
        if not isinstance(corpo, list):
            raise ErroResultados(f"formato inesperado em {caminho}: {str(corpo)[:200]}")
        return corpo

    def _com_tentativas(self, caminho: str, params: dict[str, Any] | None) -> Any:
        tentativas = Retrying(
            stop=stop_after_attempt(self._tentativas),
            wait=self._esperar,
            retry=retry_if_exception_type(_FalhaTransitoria),
            before_sleep=self._registrar_retentativa,
            reraise=True,
        )
        try:
            return tentativas(self._obter, caminho, params)
        except _FalhaTransitoria as falha:
            raise ErroResultados(
                f"PNCP (itens) falhou após {self._tentativas} tentativas: {falha}"
            ) from falha

    def _pausar(self) -> None:
        if self._ultima is not None:
            restante = self._pausa - (time.monotonic() - self._ultima)
            if restante > 0:
                time.sleep(restante)
        self._ultima = time.monotonic()

    def _obter(self, caminho: str, params: dict[str, Any] | None) -> Any:
        """JSON do 200; None para 204 e 404 (nada a devolver)."""
        self._pausar()
        try:
            resposta = self._http.get(caminho, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as erro:
            raise _FalhaTransitoria(f"{type(erro).__name__}: {erro}") from erro
        status = resposta.status_code
        if status in STATUS_RETENTAVEIS:
            raise _FalhaTransitoria(f"HTTP {status}", status)
        if status in (204, 404):
            return None
        if status >= 400:
            raise ErroResultados(f"HTTP {status}: {resposta.text[:200]}")
        try:
            return resposta.json()
        except ValueError as erro:
            raise ErroResultados(f"resposta não é JSON: {resposta.text[:200]!r}") from erro

    def _esperar(self, estado: RetryCallState) -> float:
        espera = float(self._espera_exponencial(estado))
        falha = estado.outcome.exception() if estado.outcome else None
        if isinstance(falha, _FalhaTransitoria) and falha.status == 429:
            espera = max(espera, self._espera_base * FATOR_ESPERA_429)
        return espera

    def _registrar_retentativa(self, estado: RetryCallState) -> None:
        falha = estado.outcome.exception() if estado.outcome else None
        logger.warning(
            "pncp_resultados_retentativa",
            extra={"tentativa": estado.attempt_number, "motivo": str(falha)},
        )
