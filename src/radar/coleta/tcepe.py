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
- ``Remessa_InstrumentoJuridicoParticipantes``, filtro ``instrumentoJuridicoIdId`` (sic, com
  "IdId"; ``instrumentoJuridicoId`` é ignorado e devolve 100 mil). A empresa contratada:
  ``participanteNome``, ``participanteDocumento`` (CNPJ; CPF vem mascarado, "***094194**"),
  ``tipoParticipanteId`` ("PJ"/"PF"). Confirmado em 2026-11-28 (contrato 60314628 da Compesa:
  LEENNE CONSTRUCOES LTDA).
- ``Remessa_ProcessoContratacaoParticipantes``, filtro ``processoContratacaoId``. Participantes
  do processo com ``valorHomologado`` por participante (a lista costuma ter só o vencedor).

Formato: ``{"resposta": {"status": "OK", "tamanhoResultado": "N", "limiteResultado": "100000",
"conteudo": [...]}}``, todos os valores como texto. O ``BDI`` vem em escalas diferentes conforme
o jurisdicionado ("0.2467" na Compesa, "20.9700" em Pedra): quem interpreta decide.

Quirks tratados aqui:
- ``charset=ISO-8859-1``. Caractere fora do Latin-1 vira "?"; uns poucos textos guardam U+FFFD
  como bytes UTF-8 (lidos como "ï¿½"), que voltam a ser U+FFFD.
- Filtro com nome errado é ignorado em silêncio (devolve os 100 mil primeiros): cada registro é
  conferido contra o filtro pedido, e resultado no limite é tratado como truncado.
- ``NumeroProcessoContratacao`` casa por substring ("44" traz também o "144"): o registro que
  só contém o número é descartado, sem erro.
- ``NumeroProcessoContratacao`` aceita texto: ``009.CELOSE`` (UJ 800, 2026) devolveu o contrato
  072/2026 desse processo (chamada real em 2026-11-27). Número não numérico ("009.CELOSE",
  "CELOEII-023", "260202DE00003") confere por igualdade sem diferenciar maiúsculas; o que só
  contém o texto pedido é descartado como na substring numérica.
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
PARTICIPANTES_INSTRUMENTO = "Remessa_InstrumentoJuridicoParticipantes"
PARTICIPANTES_PROCESSO = "Remessa_ProcessoContratacaoParticipantes"


class ErroTcepe(Exception):
    pass


class NumeroSemBusca(ErroTcepe):
    """O número do processo não serve para buscar contratos (vazio); não é falha do TCE-PE."""


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


def _mesmo_numero(registro: Any, campo: str, valor: str) -> bool:
    """O registro tem `campo` com o mesmo número, ignorando zeros à esquerda ("016" == "16").

    Valor não numérico (número de processo como "009.CELOSE") confere por igualdade, sem
    diferenciar maiúsculas. Campo ausente não confere: é sinal de que o filtro foi ignorado.
    """
    if not isinstance(registro, dict) or campo not in registro:
        return False
    texto = str(registro[campo]).strip()
    if not valor.isdigit():
        return texto.casefold() == valor.casefold()
    return texto.isdigit() and int(texto) == int(valor)


def _contem_numero(registro: Any, campo: str, valor: str) -> bool:
    """O campo traz o número pedido dentro dele ("144" contém "44"): casamento por substring."""
    if not isinstance(registro, dict) or campo not in registro:
        return False
    texto = str(registro[campo]).strip()
    if not valor.isdigit():
        return valor.casefold() in texto.casefold()
    return str(int(valor)) in texto


# Filtros que o servidor aplica como substring (LIKE): `NumeroProcessoContratacao=44` devolve
# também o processo "144" (visto em 2026-11-27, UJ 66). Todo registro que não bate exatamente
# (ignorando zeros à esquerda) mas contém o número é descartado ("144" e "0440" para "44");
# o que nem contém continua sendo filtro ignorado (erro).
_FILTROS_SUBSTRING = frozenset({"NumeroProcessoContratacao"})


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
        """Contratos do processo `numero` (texto, ex. "016" ou "009.CELOSE") da UJ no ano.

        Número vazio levanta `NumeroSemBusca` sem chamar: não há como buscar o contrato.
        """
        numero = str(numero).strip()
        if not numero:
            raise NumeroSemBusca("processo sem número: não dá para buscar o contrato")
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

    def participantes_instrumento(self, instrumento_id: int | str) -> list[dict[str, Any]]:
        """Contratado(s) do instrumento jurídico (CNPJ e razão social)."""
        return self._consultar(
            PARTICIPANTES_INSTRUMENTO,
            {"instrumentoJuridicoIdId": _id(instrumento_id, "instrumento")},
        )

    def participantes_processo(self, processo_id: int | str) -> list[dict[str, Any]]:
        """Participantes do processo, com o valor homologado de cada um."""
        return self._consultar(
            PARTICIPANTES_PROCESSO, {"processoContratacaoId": _id(processo_id, "processo")}
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
        conferidos = []
        for registro in registros:
            descartar = False
            for campo, valor in filtros.items():
                if _mesmo_numero(registro, campo, valor):
                    continue
                if campo in _FILTROS_SUBSTRING and _contem_numero(registro, campo, valor):
                    descartar = True
                    continue
                raise ErroTcepe(
                    f"{entidade} ignorou o filtro {campo}={valor}: veio {str(registro)[:200]}"
                )
            if descartar:
                logger.debug("tcepe_descartado_por_substring entidade=%s", entidade)
            else:
                conferidos.append(registro)
        return conferidos

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
