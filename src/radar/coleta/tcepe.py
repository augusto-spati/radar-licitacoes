"""Dados abertos do TCE-PE (sistema "Remessa"): processos, orçamento e contratos por item.

Base ``https://sistemas.tcepe.tc.br/DadosAbertos/{Entidade}!json?{filtros}``, sem login.
Nomes confirmados com chamadas reais em 2026-11-27 (e no catálogo do pacote R ``tceper``):

- ``Remessa_ProcessoContratacao``, filtro ``anoContratacao``. Campos: ``processoContratacaoId``,
  ``unidadeJurisdicionadaId``, ``unidadeJurisdicionadaNome``, ``unidadeJurisdicionadaCodigoTCE``,
  ``numeroContratacao``, ``anoContratacao``, ``modalidadeNome``, ``naturezaNome``,
  ``estagioNome``, ``objeto``, ``valorGlobal`` (estimado), ``valorHomologado``,
  ``dataResultado``, ``orcamentoSigiloso`` ("0"/"1"), ``agrupamentoOrcamento``.
  O ano inteiro vem numa resposta só (2026: 31.643 processos, ~31 MB).
- ``Remessa_ProcessoContratacaoOrcamento``, filtro ``processoContratacaoId``. Campos por item:
  ``itemId``, ``itemNumero``, ``loteId``, ``loteNome``, ``itemDescricao``, ``UnidadeMedida``,
  ``Quantidade``, ``ValorUnitario``, ``ValorTotal``, ``BDI``, ``FonteReferencia``,
  ``CodigoReferencia``, ``DataReferencia``, ``MaterialServico``, ``OrcamentoSituacaoNome``.
- ``Remessa_InstrumentoJuridico``, filtros ``unidadeJurisdicionadaId``,
  ``NumeroProcessoContratacao`` (o ``numeroContratacao`` do processo, texto com zeros à
  esquerda, ex. "016") e ``AnoProcessoContratacao``. Campos: ``instrumentoJuridicoId``,
  ``Numero``, ``Ano``, ``ValorGlobalAtualizado``, ``DataAssinatura``, ``Objeto``,
  ``tipoInstrumentoJuridicoNome``, ``estagioInstrumentoJuridicoNome``. Não traz o
  ``processoContratacaoId``: o filtro por número pode devolver contrato de outro processo.
- ``Remessa_InstrumentoJuridicoItens``, filtro ``instrumentoJuridicoId``. Campos por item:
  ``itemContratoId``, ``itemDescricao``, ``UnidadeMedida``, ``Quantidade``, ``ValorUnitario``,
  ``ValorTotal``, ``BDI``, ``MaterialServico``.

Formato: ``{"resposta": {"status": "OK", "tamanhoResultado": "N", "limiteResultado": "100000",
"conteudo": [...]}}``, todos os valores como texto. O ``BDI`` vem em escalas diferentes conforme
o jurisdicionado ("0.2467" na Compesa, "20.9700" em Pedra): quem interpreta decide.

Quirks tratados aqui:
- ``charset=ISO-8859-1``. Caractere fora do Latin-1 vira "?"; uns poucos textos guardam U+FFFD
  como bytes UTF-8 (lidos como "ï¿½"), que voltam a ser U+FFFD.
- Filtro com nome errado é ignorado em silêncio (devolve os 100 mil primeiros): cada registro é
  conferido contra o filtro pedido, e resultado no limite é tratado como truncado.
- Filtro numérico com valor não numérico dá HTTP 500 com corpo "ERRO": os ids são validados antes.
- JSON inválido ("Extra data") aconteceu uma vez e passou na repetição: é transitório.
"""

import json
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

URL_BASE = "https://sistemas.tcepe.tc.br/DadosAbertos"
TIMEOUT_SEGUNDOS = 120.0  # o ano inteiro de processos tem ~31 MB
USER_AGENT = "radar-licitacoes-estudo (github.com/augusto-spati/radar-licitacoes)"
STATUS_RETENTAVEIS = frozenset({429, 500, 502, 503, 504})
FATOR_ESPERA_429 = 12
ENCODING = "iso-8859-1"
_FFFD_COMO_LATIN1 = "ï¿½"  # bytes EF BF BD lidos como ISO-8859-1

PROCESSOS = "Remessa_ProcessoContratacao"
ORCAMENTO = "Remessa_ProcessoContratacaoOrcamento"
INSTRUMENTOS = "Remessa_InstrumentoJuridico"
ITENS_INSTRUMENTO = "Remessa_InstrumentoJuridicoItens"


class ErroTcepe(Exception):
    pass


class _FalhaTransitoria(Exception):
    def __init__(self, mensagem: str, status: int | None = None) -> None:
        super().__init__(mensagem)
        self.status = status


def criar_http_tcepe() -> httpx.Client:
    return httpx.Client(
        base_url=URL_BASE,
        timeout=TIMEOUT_SEGUNDOS,
        headers={"User-Agent": USER_AGENT},
    )


def decodificar(conteudo: bytes) -> str:
    """Texto da resposta: ISO-8859-1, com o U+FFFD guardado em UTF-8 restaurado."""
    return conteudo.decode(ENCODING).replace(_FFFD_COMO_LATIN1, "�")


def _id(valor: int | str, nome: str) -> str:
    texto = str(valor).strip()
    if not texto.isdigit():
        raise ErroTcepe(f"{nome} precisa ser numérico: {valor!r}")
    return texto


def _mesmo_numero(a: Any, b: str) -> bool:
    """Compara números guardados como texto, ignorando zeros à esquerda ("016" == "16")."""
    return str(a).strip().lstrip("0") == b.strip().lstrip("0")


class ClienteTcepe:
    """Uma requisição por vez, com pausa entre elas; cada registro conferido contra o filtro."""

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

    def processos(self, ano: int) -> list[dict[str, Any]]:
        return self._consultar(PROCESSOS, {"anoContratacao": _id(ano, "ano")})

    def orcamento(self, processo_id: int | str) -> list[dict[str, Any]]:
        return self._consultar(ORCAMENTO, {"processoContratacaoId": _id(processo_id, "processo")})

    def instrumentos(self, uj_id: int | str, numero: str, ano: int) -> list[dict[str, Any]]:
        numero = str(numero).strip()
        if not numero:
            raise ErroTcepe("número do processo vazio")
        return self._consultar(
            INSTRUMENTOS,
            {
                "unidadeJurisdicionadaId": _id(uj_id, "unidade jurisdicionada"),
                "NumeroProcessoContratacao": numero,
                "AnoProcessoContratacao": _id(ano, "ano"),
            },
        )

    def itens_instrumento(self, instrumento_id: int | str) -> list[dict[str, Any]]:
        return self._consultar(
            ITENS_INSTRUMENTO, {"instrumentoJuridicoId": _id(instrumento_id, "instrumento")}
        )

    def _consultar(self, entidade: str, filtros: dict[str, str]) -> list[dict[str, Any]]:
        corpo = self._com_tentativas(entidade, filtros)
        resposta = corpo.get("resposta") if isinstance(corpo, dict) else None
        if not isinstance(resposta, dict):
            raise ErroTcepe(f"formato inesperado em {entidade}: {str(corpo)[:200]}")
        if resposta.get("status") != "OK":
            raise ErroTcepe(f"{entidade} respondeu status {resposta.get('status')!r}")
        registros = resposta.get("conteudo")
        if not isinstance(registros, list):
            raise ErroTcepe(f"{entidade} sem lista de conteúdo: {str(resposta)[:200]}")
        limite = str(resposta.get("limiteResultado", "")).strip()
        if limite.isdigit() and len(registros) >= int(limite):
            raise ErroTcepe(f"{entidade} chegou ao limite de {limite} registros (truncado)")
        for registro in registros:
            for campo, valor in filtros.items():
                if not isinstance(registro, dict) or not _mesmo_numero(
                    registro.get(campo, ""), valor
                ):
                    raise ErroTcepe(
                        f"{entidade} ignorou o filtro {campo}={valor}: veio {str(registro)[:200]}"
                    )
        return registros

    def _com_tentativas(self, entidade: str, filtros: dict[str, str]) -> Any:
        tentativas = Retrying(
            stop=stop_after_attempt(self._tentativas),
            wait=self._esperar,
            retry=retry_if_exception_type(_FalhaTransitoria),
            before_sleep=self._registrar_retentativa,
            reraise=True,
        )
        try:
            return tentativas(self._obter, entidade, filtros)
        except _FalhaTransitoria as falha:
            raise ErroTcepe(
                f"TCE-PE ({entidade}) falhou após {self._tentativas} tentativas: {falha}"
            ) from falha

    def _pausar(self) -> None:
        if self._ultima is not None:
            restante = self._pausa - (time.monotonic() - self._ultima)
            if restante > 0:
                time.sleep(restante)
        self._ultima = time.monotonic()

    def _obter(self, entidade: str, filtros: dict[str, str]) -> Any:
        self._pausar()
        try:
            resposta = self._http.get(f"/{entidade}!json", params=filtros)
        except (httpx.TimeoutException, httpx.TransportError) as erro:
            raise _FalhaTransitoria(f"{type(erro).__name__}: {erro}") from erro
        status = resposta.status_code
        if status in STATUS_RETENTAVEIS:
            raise _FalhaTransitoria(f"HTTP {status}", status)
        texto = decodificar(resposta.content)
        if status >= 400:
            raise ErroTcepe(f"HTTP {status}: {texto[:200]}")
        try:
            return json.loads(texto)
        except ValueError as erro:  # "Extra data": passou na repetição
            raise _FalhaTransitoria(f"JSON inválido: {erro}") from erro

    def _esperar(self, estado: RetryCallState) -> float:
        espera = float(self._espera_exponencial(estado))
        falha = estado.outcome.exception() if estado.outcome else None
        if isinstance(falha, _FalhaTransitoria) and falha.status == 429:
            espera = max(espera, self._espera_base * FATOR_ESPERA_429)
        return espera

    def _registrar_retentativa(self, estado: RetryCallState) -> None:
        falha = estado.outcome.exception() if estado.outcome else None
        logger.warning(
            "tcepe_retentativa",
            extra={"tentativa": estado.attempt_number, "motivo": str(falha)},
        )
