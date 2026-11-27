"""Ponto de entrada da Lambda `radar-processador` (event source mapping do SQS).

Lotes de até 10 mensagens com `ReportBatchItemFailures`: devolve só os `messageId` que devem
voltar à fila (trava ocupada, corpo inválido, erro inesperado). `falha_definitiva` é
confirmada: o consumidor já rebaixou o retrato e gravou `processamento_falhou`.

Uma sessão por mensagem. O engine (NullPool via `RADAR_DB_NULLPOOL=true`) é criado uma vez
por container e só no primeiro uso; os logs em JSON, na primeira chamada. Importar este
módulo não abre conexão nem lê o SSM.

Telemetria (`com_telemetria`): um span `processar mensagem` por registro, com link para o
`traceparent` do atributo SQS; spans e métricas são descarregados no `finally` de cada
invocação (a Lambda congela o container logo depois de responder).
"""

import dataclasses
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from radar.comum import (
    configurar_logs,
    criar_engine,
    de_corpo,
    fabrica_sessao,
    garantir_url_do_banco,
    obter_config,
)
from radar.comum.telemetria import com_telemetria, traceparent_do_registro
from radar.processador.consumidor import MensagemAdiada, processar_com_span
from radar.processador.fontes import fontes_retrato

logger = logging.getLogger(__name__)


_fabrica: sessionmaker[Session] | None = None
_logs_configurados = False
_fontes = fontes_retrato()


def _criar_sessao() -> Session:
    global _fabrica
    if _fabrica is None:
        garantir_url_do_banco()
        _fabrica = fabrica_sessao(criar_engine(obter_config()))
    return _fabrica()


@com_telemetria("radar-processador")
def handler(evento: dict[str, Any], contexto: Any) -> dict[str, list[dict[str, str]]]:
    global _logs_configurados
    if not _logs_configurados:
        configurar_logs()  # JSON: o filtro de métrica procura `processamento_falhou`
        _logs_configurados = True
    falhas: list[dict[str, str]] = []
    for registro in evento.get("Records", []):
        message_id: str = registro["messageId"]
        dados = {"message_id": message_id}
        try:
            # O contexto do produtor vem no atributo da mensagem (o corpo não o leva).
            mensagem = dataclasses.replace(
                de_corpo(registro["body"]), traceparent=traceparent_do_registro(registro)
            )
            dados |= {"outbox_id": str(mensagem.outbox_id), "tipo": mensagem.tipo}
            with _criar_sessao() as sessao:
                desfecho = processar_com_span(
                    sessao, mensagem, _fontes, datetime.now(UTC), message_id
                )
            logger.info("mensagem_processada", extra={**dados, "desfecho": desfecho})
        except MensagemAdiada:
            logger.info("mensagem_adiada", extra=dados)
            falhas.append({"itemIdentifier": message_id})
        except Exception:
            logger.exception("mensagem_erro", extra=dados)
            falhas.append({"itemIdentifier": message_id})
    return {"batchItemFailures": falhas}
