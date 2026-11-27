"""Fila no próprio processo: consome cada mensagem na hora (notebook e testes, sem AWS)."""

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, get_args

from sqlalchemy.orm import Session

from radar.coleta import FonteRetrato
from radar.comum import (
    Config,
    Fila,
    FilaRoteada,
    FilaSQS,
    Mensagem,
    ResultadoEnvio,
    TipoMensagem,
)
from radar.processador.consumidor import processar_mensagem

logger = logging.getLogger(__name__)

# Coleta uma fatia da janela da mensagem `coletar_janela`; devolve o desfecho.
Coletora = Callable[[Session, Mensagem], str]


class FilaLocal:
    """Implementa `Fila` chamando o consumidor, uma sessão própria por mensagem.

    `ok` e `falha_definitiva` são aceitas (a linha da outbox ganha `publicada_em`); exceção,
    inclusive `MensagemAdiada`, é recusada com o `repr` do erro e fica pendente na outbox.
    `falhas` (definitivas + recusadas) decide o código de saída de quem chamou.

    `coletar_janela` vai para a `coletora`, quando há uma: a fatia commita a continuação na
    outbox, e o mesmo relay a publica em seguida, até a cadeia acabar. O desfecho `falha`
    conta como falha e confirma (o planejador enfileira a janela de novo). Sem coletora,
    `tipos` não inclui `coletar_janela` (o relay nem a seleciona: ela é da Lambda); se chegar
    mesmo assim, é recusada sem contar falha e fica pendente na outbox.

    `consome_no_envio`: o relay não segura as linhas da outbox enquanto a mensagem é
    consumida aqui (uma cadeia de fatias leva horas).
    """

    consome_no_envio = True

    def __init__(
        self,
        fabrica_sessao: Callable[[], Session],
        fontes: Mapping[str, FonteRetrato],
        agora: Callable[[], datetime],
        coletora: Coletora | None = None,
    ) -> None:
        self._fabrica = fabrica_sessao
        self._fontes = fontes
        self._agora = agora
        self._coletora = coletora
        self.falhas = 0

    @property
    def tipos(self) -> tuple[TipoMensagem, ...]:
        """Os tipos que esta fila consome; o relay local publica só estes."""
        if self._coletora is None:
            return ("pagina_coletada", "retrato_coletado")
        return get_args(TipoMensagem)

    def enviar_lote(self, mensagens: list[Mensagem]) -> ResultadoEnvio:
        resultado = ResultadoEnvio(aceitas=set(), recusadas={})
        for mensagem in mensagens:
            dados = {"outbox_id": mensagem.outbox_id, "tipo": mensagem.tipo}
            if mensagem.tipo == "coletar_janela" and self._coletora is None:
                resultado.recusadas[mensagem.outbox_id] = "coletar_janela sem coletora local"
                continue
            try:
                # Sessão própria: o consumidor faz rollback sem mexer na do relay.
                with self._fabrica() as sessao:
                    desfecho = self._despachar(sessao, mensagem)
            except Exception as erro:  # Ctrl+C (BaseException) continua parando o processo
                logger.exception("mensagem_erro", extra=dados)
                resultado.recusadas[mensagem.outbox_id] = repr(erro)
                self.falhas += 1
                continue
            if desfecho in ("falha_definitiva", "falha"):
                self.falhas += 1
            resultado.aceitas.add(mensagem.outbox_id)
        return resultado

    def _despachar(self, sessao: Session, mensagem: Mensagem) -> str:
        if mensagem.tipo == "coletar_janela":
            assert self._coletora is not None
            return self._coletora(sessao, mensagem)
        return processar_mensagem(sessao, mensagem, self._fontes, self._agora())


def criar_fila(
    config: Config,
    fabrica_sessao: Callable[[], Session],
    fontes: Mapping[str, FonteRetrato],
    agora: Callable[[], datetime],
    cliente_sqs: Any | None = None,
    coletora: Coletora | None = None,
) -> Fila:
    """`FilaLocal` com `RADAR_FILA=local`; com `sqs`, a fila roteada por tipo.

    No SQS, `coletar_janela` vai para `RADAR_FILA_JANELAS_URL` e o resto para
    `RADAR_FILA_URL`; as duas são obrigatórias (todo relay pode encontrar uma continuação).
    """
    if config.fila == "local":
        return FilaLocal(fabrica_sessao, fontes, agora, coletora)
    if not config.fila_url:
        raise ValueError("RADAR_FILA=sqs exige RADAR_FILA_URL")
    if not config.fila_janelas_url:
        raise ValueError("RADAR_FILA=sqs exige RADAR_FILA_JANELAS_URL")
    return FilaRoteada(
        {"coletar_janela": FilaSQS(config.fila_janelas_url, cliente_sqs)},
        FilaSQS(config.fila_url, cliente_sqs),
    )
