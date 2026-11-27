"""Porta de entrada das mensagens da outbox: o consumidor, a fila local e o handler da Lambda."""

from radar.processador.consumidor import (
    FRACAO_INVALIDOS,
    Desfecho,
    MensagemAdiada,
    processar_mensagem,
)
from radar.processador.fila_local import FilaLocal, criar_fila
from radar.processador.fontes import fontes_retrato

__all__ = [
    "FRACAO_INVALIDOS",
    "Desfecho",
    "FilaLocal",
    "MensagemAdiada",
    "criar_fila",
    "fontes_retrato",
    "processar_mensagem",
]
