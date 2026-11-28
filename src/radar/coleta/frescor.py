"""Frescor por fonte: há quanto tempo está o dado mais novo que já chegou a edital.

Medido de ponta a ponta, por quem não coletou: só conta o que o consumidor já processou.

- Retratos: `iniciado_em` do retrato `concluido` mais novo **com `processado_em`** (a lista
  vale do momento em que começou a ser lida).
- PNCP: `concluida_em` da janela de atualização concluída mais nova **sem nenhuma página
  pendente** (`processada_em` nulo).
- Fonte sem dado nenhum: `SEM_DADO_H` (9999 h), que estoura qualquer alvo.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import desc, exists, func, select
from sqlalchemy.orm import Session

from radar.coleta.modelos import ColetaJanela, ColetaRetrato, PaginaBruta

SEM_DADO_H = 9999.0
FONTE_PNCP = "pncp"
LIMITE_ATRASO_H = 36.0  # o mesmo do AvisoDesatualizado do front


@dataclass(frozen=True)
class FalhaColeta:
    """Fonte com problema: parada há mais de 36 h ou com a última leitura em falha."""

    fonte: str
    motivo: Literal["atrasado", "falha"]
    dados_de: datetime | None


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


def _mais_novo(sessao: Session, fonte: str) -> datetime | None:
    return _mais_novo_pncp(sessao) if fonte == FONTE_PNCP else _mais_novo_retrato(sessao, fonte)


def _ultima_leitura_falhou(sessao: Session, fonte: str) -> bool:
    if fonte == FONTE_PNCP:
        status = sessao.scalar(
            select(ColetaJanela.status)
            .where(
                ColetaJanela.endpoint == "atualizacao",
                ColetaJanela.status.in_(("concluida", "falha")),
            )
            .order_by(
                desc(func.coalesce(ColetaJanela.concluida_em, ColetaJanela.iniciada_em)),
                desc(ColetaJanela.id),
            )
            .limit(1)
        )
    else:
        status = sessao.scalar(
            select(ColetaRetrato.status)
            .where(ColetaRetrato.fonte == fonte, ColetaRetrato.status != "em_andamento")
            .order_by(desc(ColetaRetrato.iniciado_em), desc(ColetaRetrato.id))
            .limit(1)
        )
    return status == "falha"


def falhas_de_coleta(sessao: Session, agora: datetime, fontes: Iterable[str]) -> list[FalhaColeta]:
    """Fontes atrasadas (frescor acima de 36 h, inclusive sem dado) ou com a última leitura em
    falha, na ordem de `fontes`. Sem o texto de erro."""
    falhas: list[FalhaColeta] = []
    for fonte in fontes:
        mais_novo = _mais_novo(sessao, fonte)
        horas = (
            SEM_DADO_H
            if mais_novo is None
            else round(max(0.0, (agora - mais_novo).total_seconds() / 3600), 3)
        )
        if horas > LIMITE_ATRASO_H:
            falhas.append(FalhaColeta(fonte, "atrasado", mais_novo))
        elif _ultima_leitura_falhou(sessao, fonte):
            falhas.append(FalhaColeta(fonte, "falha", mais_novo))
    return falhas


def frescor_por_fonte(sessao: Session, agora: datetime, fontes: Iterable[str]) -> dict[str, float]:
    """Horas de frescor de cada fonte (`pncp` ou o nome de uma fonte de retrato)."""
    resultado: dict[str, float] = {}
    for fonte in fontes:
        mais_novo = _mais_novo(sessao, fonte)
        if mais_novo is None:
            resultado[fonte] = SEM_DADO_H
        else:
            resultado[fonte] = round(max(0.0, (agora - mais_novo).total_seconds() / 3600), 3)
    return resultado
