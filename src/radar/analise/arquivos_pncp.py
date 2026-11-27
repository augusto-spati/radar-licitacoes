"""Documentos de uma contratação do PNCP (endpoint público de arquivos, sem token)."""

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from radar.analise.normalizacao import sem_acento

logger = logging.getLogger(__name__)

URL_BASE = "https://pncp.gov.br/pncp-api/v1"
TIMEOUT_SEGUNDOS = 120.0
USER_AGENT = "radar-licitacoes-estudo (github.com/augusto-spati/radar-licitacoes)"
STATUS_RETENTAVEIS = frozenset({429, 500, 502, 503, 504})
LIMITE_BYTES = 20_000_000
MAX_DOCUMENTOS = 3
_TIPOS_ANALISADOS = ("edital", "termo de referencia")
_ID_PNCP = re.compile(r"^(\d{14})-\d+-(\d+)/(\d{4})$")


def chave_pncp(id_na_fonte: str) -> tuple[str, int, int] | None:
    """`<cnpj>-1-<sequencial>/<ano>` → (cnpj, ano, sequencial); outro formato → None."""
    casamento = _ID_PNCP.match(id_na_fonte)
    if casamento is None:
        return None
    cnpj, sequencial, ano = casamento.groups()
    return cnpj, int(ano), int(sequencial)


@dataclass(frozen=True)
class ArquivoPNCP:
    sequencial: int
    titulo: str
    tipo_nome: str
    url: str


class ErroArquivos(Exception):
    pass


class GrandeDemais(Exception):
    pass


class _FalhaTransitoria(Exception):
    pass


def criar_http_arquivos() -> httpx.Client:
    return httpx.Client(
        base_url=URL_BASE,
        timeout=TIMEOUT_SEGUNDOS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


def escolher_documentos(arquivos: list[ArquivoPNCP]) -> list[ArquivoPNCP]:
    """Edital ou termo de referência (tipo sem acento), até 3, na ordem do sequencial."""
    elegiveis = [
        a for a in arquivos if any(t in sem_acento(a.tipo_nome) for t in _TIPOS_ANALISADOS)
    ]
    return sorted(elegiveis, key=lambda a: a.sequencial)[:MAX_DOCUMENTOS]


class ClienteArquivosPNCP:
    """Lista e baixa documentos com cortesia: pausa entre requisições e poucas tentativas."""

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
        self._espera = wait_exponential(multiplier=espera_base, max=60)
        self._ultima: float | None = None

    def fechar(self) -> None:
        self._http.close()

    def compra(self, cnpj: str, ano: int, sequencial: int) -> dict[str, Any]:
        """A contratação (para o `valorTotalEstimado` no preparo dos evals)."""
        corpo = self._com_tentativas(self._obter_json, f"/orgaos/{cnpj}/compras/{ano}/{sequencial}")
        if not isinstance(corpo, dict):
            raise ErroArquivos(f"contratação com formato inesperado: {str(corpo)[:200]}")
        return corpo

    def listar(self, cnpj: str, ano: int, sequencial: int) -> list[ArquivoPNCP]:
        caminho = f"/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos"
        corpo = self._com_tentativas(self._listar, caminho)
        return [
            ArquivoPNCP(
                sequencial=int(item["sequencialDocumento"]),
                titulo=str(item.get("titulo") or ""),
                tipo_nome=str(item.get("tipoDocumentoNome") or ""),
                url=str(item["url"]),
            )
            for item in corpo
            if item.get("statusAtivo", True) is not False
        ]

    def baixar(self, url: str, limite_bytes: int = LIMITE_BYTES) -> bytes:
        dados: bytes = self._com_tentativas(self._baixar, url, limite_bytes)
        return dados

    def _com_tentativas(self, funcao: Any, *args: Any) -> Any:
        tentativas = Retrying(
            stop=stop_after_attempt(self._tentativas),
            wait=self._espera,
            retry=retry_if_exception_type(_FalhaTransitoria),
            before_sleep=self._registrar_retentativa,
            reraise=True,
        )
        try:
            return tentativas(funcao, *args)
        except _FalhaTransitoria as falha:
            raise ErroArquivos(
                f"PNCP (arquivos) falhou após {self._tentativas} tentativas: {falha}"
            ) from falha

    def _pausar(self) -> None:
        if self._ultima is not None:
            restante = self._pausa - (time.monotonic() - self._ultima)
            if restante > 0:
                time.sleep(restante)
        self._ultima = time.monotonic()

    def _verificar(self, resposta: httpx.Response) -> None:
        if resposta.status_code in STATUS_RETENTAVEIS:
            raise _FalhaTransitoria(f"HTTP {resposta.status_code}")
        if resposta.status_code >= 400:
            raise ErroArquivos(f"HTTP {resposta.status_code}")

    def _listar(self, caminho: str) -> list[dict[str, Any]]:
        corpo = self._obter_json(caminho)
        if corpo is None:
            return []
        if not isinstance(corpo, list):
            raise ErroArquivos(f"listagem com formato inesperado: {str(corpo)[:200]}")
        return corpo

    def _obter_json(self, caminho: str) -> Any:
        self._pausar()
        try:
            resposta = self._http.get(caminho)
        except httpx.TransportError as erro:
            raise _FalhaTransitoria(f"{type(erro).__name__}: {erro}") from erro
        self._verificar(resposta)
        if resposta.status_code == 204:
            return None
        try:
            return resposta.json()
        except ValueError as erro:
            raise ErroArquivos(f"resposta não é JSON: {resposta.text[:200]!r}") from erro

    def _baixar(self, url: str, limite_bytes: int) -> bytes:
        self._pausar()
        try:
            with self._http.stream("GET", url) as resposta:
                self._verificar(resposta)
                declarado = resposta.headers.get("Content-Length")
                if declarado and declarado.isdigit() and int(declarado) > limite_bytes:
                    raise GrandeDemais(f"{declarado} bytes (limite {limite_bytes})")
                partes: list[bytes] = []
                total = 0
                for parte in resposta.iter_bytes():
                    total += len(parte)
                    if total > limite_bytes:
                        raise GrandeDemais(f"mais de {limite_bytes} bytes")
                    partes.append(parte)
        except httpx.TransportError as erro:
            raise _FalhaTransitoria(f"{type(erro).__name__}: {erro}") from erro
        return b"".join(partes)

    def _registrar_retentativa(self, estado: RetryCallState) -> None:
        falha = estado.outcome.exception() if estado.outcome else None
        logger.warning(
            "pncp_arquivos_retentativa",
            extra={"tentativa": estado.attempt_number, "motivo": str(falha)},
        )
