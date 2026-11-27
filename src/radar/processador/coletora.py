"""Coleta do PNCP por mensagem: a fatia de uma `coletar_janela` e o planejamento das janelas.

Quem chama é a Lambda coletora (com orçamento), a planejadora e, localmente, a cli com a
`FilaLocal` (sem orçamento). Os logs de desfecho (`janela_concluida`, `janela_ocupada`, ...)
saem da própria fatia.
"""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from radar.coleta import (
    ClientePNCP,
    ResultadoFatia,
    coletar_fatia,
    enfileirar_elegiveis,
    planejar_janelas,
)
from radar.comum import Mensagem
from radar.editais import Criterios

ENDPOINT = "atualizacao"
RECUO_PADRAO = 10


def processar_janela(
    sessao: Session,
    mensagem: Mensagem,
    cliente: ClientePNCP,
    token: str,
    orcamento_s: float | None,
) -> ResultadoFatia:
    """Uma fatia da janela da mensagem; `ValueError` se a mensagem não é `coletar_janela`.

    O resultado traz o `outbox_id` das continuações que a fatia gravou (`continuacoes`).
    """
    if mensagem.tipo != "coletar_janela":
        raise ValueError(f"a coletora só recebe coletar_janela, não {mensagem.tipo!r}")
    return coletar_fatia(
        sessao,
        cliente,
        mensagem.carga["janela_id"],
        lote=mensagem.carga["lote"],
        token=token,
        orcamento_s=orcamento_s,
        traceparent=mensagem.traceparent,
    )


def planejar_e_enfileirar(
    sessao: Session, criterios: Criterios, hoje: date, recuo: int = RECUO_PADRAO
) -> tuple[int, int]:
    """Janelas de atualização do recuo até hoje e `coletar_janela` das elegíveis.

    Devolve (janelas novas, mensagens enfileiradas). Quem chama roda o relay depois.
    """
    novas = planejar_janelas(
        sessao, ENDPOINT, hoje - timedelta(days=recuo), hoje, criterios.modalidades
    )
    enfileiradas = enfileirar_elegiveis(sessao, ENDPOINT)
    return novas, enfileiradas
