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
# PE fica fora: o TCE-PE (7b.1) já traz o estimado de lá item a item; contar os dois seria o
# mesmo processo duas vezes na mediana.
UFS_FORA = ("PE",)
# Só obra ou serviço de engenharia tem planilha de serviços. Na 1ª rodada real, 9 de 10 alvos
# eram compra (hidrômetros, tubos), calibração ou projeto: sem planilha, só download e IA.
TERMOS_OBRA = [
    "obra",
    "execução",
    "implantação",
    "ampliação",
    "substituição",
    "construção",
    "reforma",
    "manutenção",
    "assentamento",
    "remanejamento",
    "prolongamento",
    "recuperação",
]
TERMOS_FORA = [
    "aquisição",
    "compra",
    "elaboração de projeto",
    "projeto executivo",
    "projeto básico",
    "calibração",
    "locação",
]


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
    """Editais de obra (`TERMOS_OBRA`, sem `TERMOS_FORA`) do PNCP das `linhas` (redes e perdas),
    UFs e modalidades do filtro, publicados
    nos últimos `janela_dias`, do mais antigo ao mais novo, fora os de `ignorar` e os de PE
    (`UFS_FORA`).

    Na publicação, e não só depois de homologados: a planilha sai com o edital.
    """
    casamentos = [_casa_termos(criterios.linhas[n]) for n in linhas if n in criterios.linhas]
    desde = agora.astimezone(FUSO).replace(tzinfo=None) - timedelta(days=janela_dias)
    consulta = (
        select(*_COLUNAS)
        .where(Edital.fonte == Fonte.pncp.value)
        .where(or_(*casamentos) if casamentos else false())
        .where(_casa_termos(TERMOS_OBRA))
        .where(~_casa_termos(TERMOS_FORA))
        .where(Edital.uf.in_(criterios.ufs))
        .where(Edital.uf.not_in(UFS_FORA))
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
