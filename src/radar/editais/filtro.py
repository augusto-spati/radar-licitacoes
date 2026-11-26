"""Filtro dos editais por linha de serviço, UF, modalidade e prazo."""

import tomllib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Select, false, func, or_, select
from sqlalchemy.orm import Session

from radar.editais.modelos import Edital

FUSO = ZoneInfo("America/Sao_Paulo")


@dataclass(frozen=True)
class Criterios:
    linhas: dict[str, list[str]]
    ufs: list[str]
    modalidades: list[int]


@dataclass(frozen=True)
class EditalResumo:
    numero_controle_pncp: str
    objeto: str
    orgao_nome: str
    uf: str | None
    municipio: str | None
    modalidade_id: int
    valor_estimado: Decimal | None
    data_encerramento_proposta: datetime | None
    link_origem: str | None
    linhas: list[str]


def carregar_criterios(caminho: Path) -> Criterios:
    with caminho.open("rb") as arquivo:
        dados = tomllib.load(arquivo)
    return Criterios(
        linhas={nome: list(termos) for nome, termos in dados["linhas"].items()},
        ufs=list(dados["ufs"]),
        modalidades=list(dados["modalidades"]),
    )


def _casa_termos(termos: list[str]) -> ColumnElement[bool]:
    """Expressão `busca @@ (frase1 || frase2 ...)`; cada termo é uma busca de frase."""
    if not termos:
        return false()
    frases = [func.phraseto_tsquery("portuguese", func.radar_unaccent(t)) for t in termos]
    consulta: Any = frases[0]
    for frase in frases[1:]:
        consulta = consulta.op("||")(frase)
    return Edital.busca.op("@@")(consulta)


def _montar_consulta(
    criterios: Criterios, agora: datetime, somente_abertos: bool, limite: int
) -> Select[tuple[Any, ...]]:
    casamentos = [_casa_termos(termos) for termos in criterios.linhas.values()]
    # Só as colunas do EditalResumo: `bruto` e `busca` são pesados e não são lidos aqui.
    colunas: list[Any] = [
        Edital.numero_controle_pncp,
        Edital.objeto,
        Edital.orgao_nome,
        Edital.uf,
        Edital.municipio,
        Edital.modalidade_id,
        Edital.valor_estimado,
        Edital.data_encerramento_proposta,
        Edital.link_origem,
        *(c.label(f"linha_{i}") for i, c in enumerate(casamentos)),
    ]
    consulta = (
        select(*colunas)
        .where(or_(*casamentos) if casamentos else false())
        .where(Edital.uf.in_(criterios.ufs))
        .where(Edital.modalidade_id.in_(criterios.modalidades))
        .order_by(Edital.data_encerramento_proposta.asc().nulls_last())
        .limit(limite)
    )
    if somente_abertos:
        agora_brasilia = agora.astimezone(FUSO).replace(tzinfo=None)
        consulta = consulta.where(
            Edital.data_encerramento_proposta > agora_brasilia, Edital.situacao_id == 1
        )
    return consulta


def filtrar(
    sessao: Session,
    criterios: Criterios,
    agora: datetime,
    somente_abertos: bool = True,
    limite: int = 200,
) -> list[EditalResumo]:
    nomes = list(criterios.linhas)
    resultado: list[EditalResumo] = []
    for linha in sessao.execute(_montar_consulta(criterios, agora, somente_abertos, limite)):
        bateram: tuple[bool, ...] = tuple(linha[9:])
        resultado.append(
            EditalResumo(
                numero_controle_pncp=linha.numero_controle_pncp,
                objeto=linha.objeto,
                orgao_nome=linha.orgao_nome,
                uf=linha.uf,
                municipio=linha.municipio,
                modalidade_id=linha.modalidade_id,
                valor_estimado=linha.valor_estimado,
                data_encerramento_proposta=linha.data_encerramento_proposta,
                link_origem=linha.link_origem,
                linhas=[nome for nome, bateu in zip(nomes, bateram, strict=True) if bateu],
            )
        )
    return resultado
