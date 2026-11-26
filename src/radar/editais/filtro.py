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


@dataclass(frozen=True)
class EditalDetalhe(EditalResumo):
    orgao_cnpj: str
    situacao_nome: str
    data_publicacao: datetime
    data_abertura_proposta: datetime | None


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


_COLUNAS_RESUMO: list[Any] = [
    Edital.numero_controle_pncp,
    Edital.objeto,
    Edital.orgao_nome,
    Edital.uf,
    Edital.municipio,
    Edital.modalidade_id,
    Edital.valor_estimado,
    Edital.data_encerramento_proposta,
    Edital.link_origem,
]


def _casamentos(criterios: Criterios) -> list[ColumnElement[bool]]:
    return [_casa_termos(termos) for termos in criterios.linhas.values()]


def _rotulos(casamentos: list[ColumnElement[bool]]) -> list[Any]:
    """Uma coluna booleana `linha_<i>` por linha de serviço: o edital casa com ela?"""
    return [c.label(f"linha_{i}") for i, c in enumerate(casamentos)]


def _campos_resumo(linha: Any, criterios: Criterios) -> dict[str, Any]:
    campos = {coluna.key: linha._mapping[coluna.key] for coluna in _COLUNAS_RESUMO}
    campos["linhas"] = [
        nome for i, nome in enumerate(criterios.linhas) if linha._mapping[f"linha_{i}"]
    ]
    return campos


def _montar_consulta(
    criterios: Criterios, agora: datetime, somente_abertos: bool, limite: int
) -> Select[tuple[Any, ...]]:
    casamentos = _casamentos(criterios)
    # Só as colunas do EditalResumo: `bruto` e `busca` são pesados e não são lidos aqui.
    consulta = (
        select(*_COLUNAS_RESUMO, *_rotulos(casamentos))
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
    linhas = sessao.execute(_montar_consulta(criterios, agora, somente_abertos, limite))
    return [EditalResumo(**_campos_resumo(linha, criterios)) for linha in linhas]


_COLUNAS_DETALHE: list[Any] = [
    Edital.orgao_cnpj,
    Edital.situacao_nome,
    Edital.data_publicacao,
    Edital.data_abertura_proposta,
]


def _detalhe(linha: Any, criterios: Criterios) -> EditalDetalhe:
    return EditalDetalhe(
        **_campos_resumo(linha, criterios),
        **{coluna.key: linha._mapping[coluna.key] for coluna in _COLUNAS_DETALHE},
    )


def obter_edital(
    sessao: Session, numero_controle_pncp: str, criterios: Criterios
) -> EditalDetalhe | None:
    """Um edital pelo número, com as linhas que casam (mesma regra de `filtrar`).

    Não aplica UF, modalidade nem prazo: quem pede um edital pelo número o recebe.
    """
    consulta = select(*_COLUNAS_RESUMO, *_COLUNAS_DETALHE, *_rotulos(_casamentos(criterios))).where(
        Edital.numero_controle_pncp == numero_controle_pncp
    )
    linha = sessao.execute(consulta).first()
    return None if linha is None else _detalhe(linha, criterios)


def listar_abertos(
    sessao: Session, criterios: Criterios, agora: datetime, limite: int
) -> list[EditalDetalhe]:
    """Editais abertos em qualquer linha (só UF e modalidade dos critérios), por prazo.

    `linhas` vem preenchida quando o edital casa com alguma linha; fica vazia se não.
    """
    agora_brasilia = agora.astimezone(FUSO).replace(tzinfo=None)
    consulta = (
        select(*_COLUNAS_RESUMO, *_COLUNAS_DETALHE, *_rotulos(_casamentos(criterios)))
        .where(Edital.uf.in_(criterios.ufs))
        .where(Edital.modalidade_id.in_(criterios.modalidades))
        .where(Edital.data_encerramento_proposta > agora_brasilia, Edital.situacao_id == 1)
        .order_by(Edital.data_encerramento_proposta.asc())
        .limit(limite)
    )
    return [_detalhe(linha, criterios) for linha in sessao.execute(consulta)]
