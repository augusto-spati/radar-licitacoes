"""Ponto de entrada da Lambda `radar-coletora` (event source mapping de `radar-janelas`).

Uma mensagem `coletar_janela` por invocação (`batch_size = 1`), no máximo duas invocações ao
mesmo tempo (`maximum_concurrency = 2`). O relay roda no começo (publica o que uma invocação
anterior deixou na outbox) e no fim (as páginas desta fatia e a continuação). O token do
lease é o `aws_request_id`.

Confirmam a mensagem: `concluida`, `continua`, `dividida`, `ocupada`, `obsoleta`,
`inexistente` e `lease_perdida`. Voltam (`batchItemFailures`): `falha`, corpo inválido,
exceção e relay do fim que falhou ou foi recusado (a reentrega publica no começo e sai
`obsoleta`).

O engine, a fila e o cliente do PNCP são criados no primeiro uso, uma vez por container.
Importar este módulo não abre conexão nem lê o SSM.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from radar.coleta import ORCAMENTO_LAMBDA_S, ClientePNCP, criar_http
from radar.comum import (
    Fila,
    configurar_logs,
    criar_engine,
    de_corpo,
    fabrica_sessao,
    garantir_url_do_banco,
    obter_config,
    publicar_outbox,
)
from radar.processador.coletora import processar_janela
from radar.processador.fila_local import criar_fila

logger = logging.getLogger(__name__)

TENTATIVAS_PNCP = 3  # 3 x 90 s + esperas cabem entre os 540 s do orçamento e os 900 s

_fabrica: sessionmaker[Session] | None = None
_fila: Fila | None = None
_cliente: ClientePNCP | None = None
_logs_configurados = False


def _criar_sessao() -> Session:
    global _fabrica
    if _fabrica is None:
        garantir_url_do_banco()
        _fabrica = fabrica_sessao(criar_engine(obter_config()))
    return _fabrica()


def _agora() -> datetime:
    return datetime.now(UTC)


def _criar_fila() -> Fila:
    global _fila
    if _fila is None:
        _fila = criar_fila(obter_config(), _criar_sessao, {}, _agora)
    return _fila


def _criar_cliente() -> ClientePNCP:
    global _cliente
    if _cliente is None:
        _cliente = ClientePNCP(criar_http(), tentativas=TENTATIVAS_PNCP)
    return _cliente


def _publicar(momento: str) -> None:
    """Relay da outbox; recusa conta como erro (a continuação ficaria parada na outbox)."""
    with _criar_sessao() as sessao:
        resumo = publicar_outbox(sessao, _criar_fila())
    logger.info(
        "relay",
        extra={"momento": momento, "publicadas": resumo.publicadas, "recusadas": resumo.recusadas},
    )
    if resumo.recusadas:
        raise RuntimeError(f"relay do {momento}: {resumo.recusadas} mensagens recusadas")


def handler(evento: dict[str, Any], contexto: Any) -> dict[str, list[dict[str, str]]]:
    global _logs_configurados
    if not _logs_configurados:
        configurar_logs()  # JSON: o filtro de métrica procura `janela_concluida`
        _logs_configurados = True
    try:
        _publicar("começo")
    except Exception:  # o relay do fim tenta de novo
        logger.exception("relay_erro", extra={"momento": "começo"})
    token: str = contexto.aws_request_id
    falhas: list[dict[str, str]] = []
    confirmadas: list[dict[str, str]] = []
    for registro in evento.get("Records", []):
        message_id: str = registro["messageId"]
        item = {"itemIdentifier": message_id}
        dados = {"message_id": message_id}
        try:
            mensagem = de_corpo(registro["body"])
            dados |= {
                "outbox_id": str(mensagem.outbox_id),
                **{k: str(v) for k, v in mensagem.carga.items()},
            }
            with _criar_sessao() as sessao:
                desfecho = processar_janela(
                    sessao, mensagem, _criar_cliente(), token, ORCAMENTO_LAMBDA_S
                )
            logger.info("mensagem_processada", extra={**dados, "desfecho": desfecho})
        except Exception:
            logger.exception("mensagem_erro", extra=dados)
            falhas.append(item)
            continue
        (falhas if desfecho == "falha" else confirmadas).append(item)
    try:
        _publicar("fim")
    except Exception:  # a mensagem volta; a reentrega publica no começo e sai obsoleta
        logger.exception("relay_erro", extra={"momento": "fim"})
        falhas.extend(confirmadas)
    return {"batchItemFailures": falhas}
