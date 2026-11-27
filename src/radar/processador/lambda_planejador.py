"""Ponto de entrada da Lambda `radar-planejador` (EventBridge Scheduler, 06:00 em Brasília).

Planeja as janelas de atualização do recuo, enfileira `coletar_janela` das elegíveis e roda o
relay. Erro propaga (vira `Errors` da Lambda); recusa da fila também, para não passar em
silêncio. Importar este módulo não abre conexão nem lê o SSM.
"""

import logging
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from radar.comum import (
    Fila,
    configurar_logs,
    criar_engine,
    fabrica_sessao,
    garantir_url_do_banco,
    obter_config,
    publicar_outbox,
)
from radar.editais import carregar_criterios
from radar.processador.coletora import planejar_e_enfileirar
from radar.processador.fila_local import criar_fila

logger = logging.getLogger(__name__)

FUSO = ZoneInfo("America/Sao_Paulo")

_fabrica: sessionmaker[Session] | None = None
_logs_configurados = False


def _criar_sessao() -> Session:
    global _fabrica
    if _fabrica is None:
        garantir_url_do_banco()
        _fabrica = fabrica_sessao(criar_engine(obter_config()))
    return _fabrica()


def _criar_fila() -> Fila:
    return criar_fila(obter_config(), _criar_sessao, {}, lambda: datetime.now(UTC))


def _hoje() -> date:
    return datetime.now(FUSO).date()


def handler(evento: dict[str, Any], contexto: Any) -> dict[str, int]:
    global _logs_configurados
    if not _logs_configurados:
        configurar_logs()
        _logs_configurados = True
    criterios = carregar_criterios(obter_config().config_filtro)
    hoje = _hoje()
    with _criar_sessao() as sessao:
        novas, enfileiradas = planejar_e_enfileirar(sessao, criterios, hoje)
        resumo = publicar_outbox(sessao, _criar_fila())
    dados = {
        "hoje": hoje.isoformat(),
        "janelas_novas": novas,
        "enfileiradas": enfileiradas,
        "publicadas": resumo.publicadas,
        "recusadas": resumo.recusadas,
    }
    logger.info("planejamento_concluido", extra=dados)
    if resumo.recusadas:
        raise RuntimeError(f"relay: {resumo.recusadas} mensagens recusadas pela fila")
    return {"janelas_novas": novas, "enfileiradas": enfileiradas, "publicadas": resumo.publicadas}
