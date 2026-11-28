"""Separação do painel (home) em Decidir e Mais adiante: função pura sobre a base dos abertos."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from radar.editais.filtro import DIAS_NOVO, FUSO, EditalResumo, limite_encerra_em

DIAS_DECIDIR = 20
LIMITE_SECAO = 30

ChaveSecao = Literal["decidir", "mais_adiante"]


@dataclass(frozen=True)
class Secao:
    chave: ChaveSecao
    total: int
    editais: list[EditalResumo]


@dataclass(frozen=True)
class Painel:
    secoes: list[Secao]
    decidir: int
    novos: int
    abertos: int


def dias_ate_encerrar(encerramento: datetime, agora: datetime) -> int:
    """Dias de calendário de Brasília até o encerramento (sem fuso); 0 = hoje."""
    return (encerramento.date() - agora.astimezone(FUSO).date()).days


def eh_novo(primeiro_visto_em: datetime | None, agora: datetime) -> bool:
    return primeiro_visto_em is not None and primeiro_visto_em >= agora - timedelta(days=DIAS_NOVO)


def separar_painel(
    editais: list[EditalResumo],
    agora: datetime,
    considerar_veredito: bool,
    limite_secao: int = LIMITE_SECAO,
) -> Painel:
    """Divide a base dos abertos (em ordem de prazo) em Decidir e Mais adiante.

    A janela é a mesma do recorte `encerra_em_dias` da Lista (`limite_encerra_em`), para o
    contador e a Lista filtrada darem o mesmo número.
    """
    corte = limite_encerra_em(agora, DIAS_DECIDIR)
    decidir: list[EditalResumo] = []
    adiante: list[EditalResumo] = []
    for edital in editais:
        if considerar_veredito and edital.veredito == "inapto":
            continue
        prazo = edital.data_encerramento_proposta
        (decidir if prazo is not None and prazo < corte else adiante).append(edital)
    secoes = [
        Secao("decidir", len(decidir), decidir[:limite_secao]),
        Secao("mais_adiante", len(adiante), adiante[:limite_secao]),
    ]
    return Painel(
        secoes=secoes,
        decidir=len(decidir),
        novos=sum(eh_novo(e.primeiro_visto_em, agora) for e in editais),
        abertos=len(editais),
    )
