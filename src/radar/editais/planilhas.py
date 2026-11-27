"""Editais do PNCP cuja planilha orçamentária a 7b.2 lê (`radar planilhas atualizar`)."""

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from radar.editais.filtro import Criterios, _casa_termos, separar_id_publico
from radar.editais.modelos import Edital, Fonte
from radar.editais.resultados import FUSO, _chave

logger = logging.getLogger(__name__)

LINHAS_PLANILHA = ("redes", "perdas")


@dataclass(frozen=True)
class AlvoPlanilha:
    id_na_fonte: str
    cnpj: str
    ano: int
    sequencial: int
    uf: str | None
    municipio: str | None
    orgao_nome: str
    orgao_cnpj: str | None
    objeto: str
    data_publicacao: datetime | None


_COLUNAS = (
    Edital.id_na_fonte,
    Edital.uf,
    Edital.municipio,
    Edital.orgao_nome,
    Edital.orgao_cnpj,
    Edital.objeto,
    Edital.data_publicacao,
)


def _alvo(linha: Any) -> AlvoPlanilha | None:
    id_na_fonte = linha.id_na_fonte
    chave = _chave(id_na_fonte)
    if chave is None:
        logger.warning("id do PNCP fora do formato esperado: %s", id_na_fonte)
        return None
    return AlvoPlanilha(
        id_na_fonte,
        *chave,
        uf=linha.uf,
        municipio=linha.municipio,
        orgao_nome=linha.orgao_nome,
        orgao_cnpj=linha.orgao_cnpj,
        objeto=linha.objeto,
        data_publicacao=linha.data_publicacao,
    )


def alvos_planilha(
    sessao: Session,
    criterios: Criterios,
    agora: datetime,
    janela_dias: int,
    limite: int,
    ignorar: Collection[str] = (),
    linhas: Sequence[str] = LINHAS_PLANILHA,
) -> list[AlvoPlanilha]:
    """Editais do PNCP das `linhas` (redes e perdas), UFs e modalidades do filtro, publicados
    nos últimos `janela_dias`, do mais antigo ao mais novo, fora os de `ignorar`.

    Na publicação, e não só depois de homologados: a planilha sai com o edital.
    """
    casamentos = [_casa_termos(criterios.linhas[n]) for n in linhas if n in criterios.linhas]
    desde = agora.astimezone(FUSO).replace(tzinfo=None) - timedelta(days=janela_dias)
    consulta = (
        select(*_COLUNAS)
        .where(Edital.fonte == Fonte.pncp.value)
        .where(or_(*casamentos) if casamentos else false())
        .where(Edital.uf.in_(criterios.ufs))
        .where(Edital.modalidade_id.in_(criterios.modalidades))
        .where(Edital.data_publicacao >= desde)
        .order_by(Edital.data_publicacao.asc(), Edital.id_na_fonte.asc())
        .limit(limite)
    )
    if ignorar:
        consulta = consulta.where(Edital.id_na_fonte.not_in(list(ignorar)))
    return [a for linha in sessao.execute(consulta) if (a := _alvo(linha)) is not None]


def alvo_planilha(sessao: Session, id_publico: str) -> AlvoPlanilha | None:
    """Um edital do PNCP pelo id público (`pncp~...`), sem filtro de linha, UF nem data."""
    chave = separar_id_publico(id_publico)
    if chave is None or chave[0] != Fonte.pncp.value:
        return None
    linha = sessao.execute(
        select(*_COLUNAS).where(Edital.fonte == chave[0], Edital.id_na_fonte == chave[1])
    ).first()
    return None if linha is None else _alvo(linha)
