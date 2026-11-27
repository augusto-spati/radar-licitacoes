"""Ponto de entrada da Lambda `radar-coletora` (event source mapping de `radar-janelas`).

Uma mensagem `coletar_janela` por invocação (`batch_size = 1`), no máximo duas invocações ao
mesmo tempo (`maximum_concurrency = 2`). O relay roda no começo (publica o que uma invocação
anterior deixou na outbox) e no fim (as páginas desta fatia e a continuação). O token do
lease é o `aws_request_id`.

Confirmam a mensagem: `concluida`, `continua`, `dividida`, `ocupada`, `obsoleta`,
`inexistente` e `lease_perdida`. Voltam (`batchItemFailures`): `falha`, corpo inválido,
exceção e a mensagem cuja própria continuação (o `outbox_id` que a fatia gravou) não foi
publicada pelo relay do fim (a reentrega publica no começo e sai `obsoleta`). Recusa de
outras linhas da outbox não devolve ninguém: o próximo relay as tenta de novo.

Orçamento da fatia: min(540 s, tempo restante − 360 s), para a última página (até 3 x 90 s
com esperas) e o relay do fim caberem antes do timeout.

O engine, a fila e o cliente do PNCP são criados no primeiro uso, uma vez por container.
Importar este módulo não abre conexão nem lê o SSM.

Telemetria (`com_telemetria`): a fatia abre o span raiz `coleta.janela` com link para o
`traceparent` do atributo SQS (o planejador ou a fatia anterior); latência e páginas com erro
saem por EMF; tudo é descarregado no `finally` da invocação.
"""

import dataclasses
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from radar.coleta import ORCAMENTO_LAMBDA_S, ClientePNCP, criar_http
from radar.comum import (
    Fila,
    Outbox,
    configurar_logs,
    criar_engine,
    de_corpo,
    fabrica_sessao,
    garantir_url_do_banco,
    obter_config,
    publicar_outbox,
)
from radar.comum.telemetria import com_telemetria, traceparent_do_registro
from radar.processador.coletora import processar_janela
from radar.processador.fila_local import criar_fila

logger = logging.getLogger(__name__)

TENTATIVAS_PNCP = 3  # 3 x 90 s + esperas cabem entre os 540 s do orçamento e os 900 s
RESERVA_S = 360  # tempo que sobra depois do orçamento: a última página e o relay do fim

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
    """Relay da outbox. Recusa só é logada: quem confere a continuação é o handler."""
    with _criar_sessao() as sessao:
        resumo = publicar_outbox(sessao, _criar_fila())
    logger.info(
        "relay",
        extra={"momento": momento, "publicadas": resumo.publicadas, "recusadas": resumo.recusadas},
    )


def _nao_publicadas(ids: set[int]) -> set[int]:
    """Das linhas da outbox pedidas, as que ainda não foram publicadas."""
    with _criar_sessao() as sessao:
        publicadas = sessao.scalars(
            select(Outbox.id).where(Outbox.id.in_(ids), Outbox.publicada_em.is_not(None))
        ).all()
    return ids - set(publicadas)


def _orcamento(contexto: Any) -> float:
    restante_s = float(contexto.get_remaining_time_in_millis()) / 1000
    return max(0.0, min(ORCAMENTO_LAMBDA_S, restante_s - RESERVA_S))


@com_telemetria("radar-coletora")
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
    # Mensagens confirmadas só se as continuações que gravaram forem publicadas.
    aguardando: list[tuple[dict[str, str], tuple[int, ...]]] = []
    for registro in evento.get("Records", []):
        message_id: str = registro["messageId"]
        item = {"itemIdentifier": message_id}
        dados = {"message_id": message_id}
        try:
            # O span `coleta.janela` da fatia liga ao contexto de quem enfileirou a janela.
            mensagem = dataclasses.replace(
                de_corpo(registro["body"]), traceparent=traceparent_do_registro(registro)
            )
            dados |= {
                "outbox_id": str(mensagem.outbox_id),
                **{k: str(v) for k, v in mensagem.carga.items()},
            }
            with _criar_sessao() as sessao:
                resultado = processar_janela(
                    sessao, mensagem, _criar_cliente(), token, _orcamento(contexto)
                )
            logger.info("mensagem_processada", extra={**dados, "desfecho": resultado.desfecho})
        except Exception:
            logger.exception("mensagem_erro", extra=dados)
            falhas.append(item)
            continue
        if resultado.desfecho == "falha":
            falhas.append(item)
        elif resultado.continuacoes:
            aguardando.append((item, resultado.continuacoes))
    try:
        _publicar("fim")  # as páginas desta fatia e as continuações
    except Exception:  # a conferência abaixo decide quem volta
        logger.exception("relay_erro", extra={"momento": "fim"})
    if not aguardando:
        return {"batchItemFailures": falhas}
    pedidas = {outbox_id for _, ids in aguardando for outbox_id in ids}
    try:
        pendentes = _nao_publicadas(pedidas)
    except Exception:  # sem saber, volta quem tem continuação; a reentrega sai obsoleta
        logger.exception("conferencia_erro")
        pendentes = pedidas
    for item, ids in aguardando:
        if pendentes & set(ids):
            logger.warning(
                "continuacao_nao_publicada",
                extra={"message_id": item["itemIdentifier"], "continuacoes": list(ids)},
            )
            falhas.append(item)
    return {"batchItemFailures": falhas}
