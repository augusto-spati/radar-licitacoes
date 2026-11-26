"""Porta de entrada das mensagens da outbox: o consumidor e o handler da Lambda."""

from radar.processador.consumidor import (
    FRACAO_INVALIDOS,
    Desfecho,
    MensagemAdiada,
    processar_mensagem,
)
from radar.processador.fontes import fontes_retrato

__all__ = [
    "FRACAO_INVALIDOS",
    "Desfecho",
    "MensagemAdiada",
    "fontes_retrato",
    "processar_mensagem",
]
