"""Fila no próprio processo: consome cada mensagem na hora (notebook e testes, sem AWS)."""

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from radar.coleta import FonteRetrato
from radar.comum import Config, Fila, FilaSQS, Mensagem, ResultadoEnvio
from radar.processador.consumidor import processar_mensagem

logger = logging.getLogger(__name__)


class FilaLocal:
    """Implementa `Fila` chamando o consumidor, uma sessão própria por mensagem.

    `ok` e `falha_definitiva` são aceitas (a linha da outbox ganha `publicada_em`); exceção,
    inclusive `MensagemAdiada`, é recusada com o `repr` do erro e fica pendente na outbox.
    `falhas` (definitivas + recusadas) decide o código de saída de quem chamou.
    """

    def __init__(
        self,
        fabrica_sessao: Callable[[], Session],
        fontes: Mapping[str, FonteRetrato],
        agora: Callable[[], datetime],
    ) -> None:
        self._fabrica = fabrica_sessao
        self._fontes = fontes
        self._agora = agora
        self.falhas = 0

    def enviar_lote(self, mensagens: list[Mensagem]) -> ResultadoEnvio:
        resultado = ResultadoEnvio(aceitas=set(), recusadas={})
        for mensagem in mensagens:
            dados = {"outbox_id": mensagem.outbox_id, "tipo": mensagem.tipo}
            try:
                # Sessão própria: o consumidor faz rollback, e a do relay segura as linhas.
                with self._fabrica() as sessao:
                    desfecho = processar_mensagem(sessao, mensagem, self._fontes, self._agora())
            except Exception as erro:  # Ctrl+C (BaseException) continua parando o processo
                logger.exception("mensagem_erro", extra=dados)
                resultado.recusadas[mensagem.outbox_id] = repr(erro)
                self.falhas += 1
                continue
            if desfecho == "falha_definitiva":
                self.falhas += 1
            resultado.aceitas.add(mensagem.outbox_id)
        return resultado


def criar_fila(
    config: Config,
    fabrica_sessao: Callable[[], Session],
    fontes: Mapping[str, FonteRetrato],
    agora: Callable[[], datetime],
    cliente_sqs: Any | None = None,
) -> Fila:
    """`FilaLocal` com `RADAR_FILA=local`; `FilaSQS` com `sqs` (exige `RADAR_FILA_URL`)."""
    if config.fila == "local":
        return FilaLocal(fabrica_sessao, fontes, agora)
    if not config.fila_url:
        raise ValueError("RADAR_FILA=sqs exige RADAR_FILA_URL")
    return FilaSQS(config.fila_url, cliente_sqs)
