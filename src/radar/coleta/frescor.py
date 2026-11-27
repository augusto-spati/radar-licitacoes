"""Frescor por fonte: há quanto tempo está o dado mais novo que já chegou a edital.

Medido de ponta a ponta, por quem não coletou: só conta o que o consumidor já processou.

- Retratos: `iniciado_em` do retrato `concluido` mais novo **com `processado_em`** (a lista
  vale do momento em que começou a ser lida).
- PNCP: `concluida_em` da janela de atualização concluída mais nova **sem nenhuma página
  pendente** (`processada_em` nulo).
- Fonte sem dado nenhum: `SEM_DADO_H` (9999 h), que estoura qualquer alvo.
"""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from radar.coleta.modelos import ColetaJanela, ColetaRetrato, PaginaBruta

SEM_DADO_H = 9999.0
FONTE_PNCP = "pncp"


def _mais_novo_pncp(sessao: Session) -> datetime | None:
    pendente = exists().where(
        PaginaBruta.janela_id == ColetaJanela.id, PaginaBruta.processada_em.is_(None)
    )
    return sessao.scalar(
        select(func.max(ColetaJanela.concluida_em)).where(
            ColetaJanela.status == "concluida",
            ColetaJanela.endpoint == "atualizacao",
            ~pendente,
        )
    )


def _mais_novo_retrato(sessao: Session, fonte: str) -> datetime | None:
    return sessao.scalar(
        select(func.max(ColetaRetrato.iniciado_em)).where(
            ColetaRetrato.fonte == fonte,
            ColetaRetrato.status == "concluido",
            ColetaRetrato.processado_em.is_not(None),
        )
    )


def frescor_por_fonte(sessao: Session, agora: datetime, fontes: Iterable[str]) -> dict[str, float]:
    """Horas de frescor de cada fonte (`pncp` ou o nome de uma fonte de retrato)."""
    resultado: dict[str, float] = {}
    for fonte in fontes:
        mais_novo = (
            _mais_novo_pncp(sessao) if fonte == FONTE_PNCP else _mais_novo_retrato(sessao, fonte)
        )
        if mais_novo is None:
            resultado[fonte] = SEM_DADO_H
        else:
            resultado[fonte] = round(max(0.0, (agora - mais_novo).total_seconds() / 3600), 3)
    return resultado
