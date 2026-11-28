"""Filtro dos editais por linha de serviço, UF, modalidade (só PNCP) e prazo."""

import tomllib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Select, and_, false, func, or_, select
from sqlalchemy.orm import Session

from radar.editais.modelos import Edital, Fonte, Situacao

FUSO = ZoneInfo("America/Sao_Paulo")


@dataclass(frozen=True)
class Criterios:
    linhas: dict[str, list[str]]
    ufs: list[str]
    modalidades: list[int]


SEPARADOR_ID = "~"


def id_publico(fonte: str, id_na_fonte: str) -> str:
    """Id único entre as fontes: `<fonte>~<id_na_fonte>` (o id da fonte pode ter `/`)."""
    return f"{fonte}{SEPARADOR_ID}{id_na_fonte}"


def separar_id_publico(valor: str) -> tuple[Fonte, str] | None:
    """O inverso de `id_publico`; None se a fonte for desconhecida ou o id vier vazio."""
    fonte, separador, id_na_fonte = valor.partition(SEPARADOR_ID)
    if not separador or not id_na_fonte or fonte not in Fonte.__members__:
        return None
    return Fonte(fonte), id_na_fonte


@dataclass(frozen=True)
class EditalResumo:
    fonte: str
    id_na_fonte: str
    objeto: str
    orgao_nome: str
    uf: str | None
    municipio: str | None
    modalidade_id: int | None
    modalidade_nome: str
    valor_estimado: Decimal | None
    data_encerramento_proposta: datetime | None
    link_origem: str | None
    linhas: list[str]

    @property
    def id_publico(self) -> str:
        return id_publico(self.fonte, self.id_na_fonte)


@dataclass(frozen=True)
class EditalDetalhe(EditalResumo):
    orgao_cnpj: str | None
    situacao_nome: str
    situacao_normalizada: Situacao
    data_publicacao: datetime | None
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
    Edital.fonte,
    Edital.id_na_fonte,
    Edital.objeto,
    Edital.orgao_nome,
    Edital.uf,
    Edital.municipio,
    Edital.modalidade_id,
    Edital.modalidade_nome,
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


def _modalidade_aceita(criterios: Criterios) -> ColumnElement[bool]:
    """As modalidades do filtro.toml são códigos do PNCP; as outras fontes passam direto."""
    return or_(Edital.fonte != Fonte.pncp.value, Edital.modalidade_id.in_(criterios.modalidades))


def _aberto(agora: datetime) -> ColumnElement[bool]:
    """Aberto = situação normalizada `aberta` e prazo no futuro (Brasília). Prazo nulo não é."""
    agora_brasilia = agora.astimezone(FUSO).replace(tzinfo=None)
    return and_(
        Edital.situacao_normalizada == "aberta", Edital.data_encerramento_proposta > agora_brasilia
    )


def _montar_consulta(
    criterios: Criterios, agora: datetime, somente_abertos: bool, limite: int
) -> Select[tuple[Any, ...]]:
    casamentos = _casamentos(criterios)
    # Só as colunas do EditalResumo: `bruto` e `busca` são pesados e não são lidos aqui.
    consulta = (
        select(*_COLUNAS_RESUMO, *_rotulos(casamentos))
        .where(or_(*casamentos) if casamentos else false())
        .where(Edital.uf.in_(criterios.ufs))
        .where(_modalidade_aceita(criterios))
        .order_by(Edital.data_encerramento_proposta.asc().nulls_last())
        .limit(limite)
    )
    if somente_abertos:
        consulta = consulta.where(_aberto(agora))
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
    Edital.situacao_original.label("situacao_nome"),
    Edital.situacao_normalizada,
    Edital.data_publicacao,
    Edital.data_abertura_proposta,
]


def _detalhe(linha: Any, criterios: Criterios) -> EditalDetalhe:
    return EditalDetalhe(
        **_campos_resumo(linha, criterios),
        **{coluna.key: linha._mapping[coluna.key] for coluna in _COLUNAS_DETALHE},
    )


def obter_edital(
    sessao: Session, fonte: str, id_na_fonte: str, criterios: Criterios
) -> EditalDetalhe | None:
    """Um edital pela chave, com as linhas que casam (mesma regra de `filtrar`).

    Não aplica UF, modalidade nem prazo: quem pede um edital pela chave o recebe.
    """
    consulta = select(*_COLUNAS_RESUMO, *_COLUNAS_DETALHE, *_rotulos(_casamentos(criterios))).where(
        Edital.fonte == fonte, Edital.id_na_fonte == id_na_fonte
    )
    linha = sessao.execute(consulta).first()
    return None if linha is None else _detalhe(linha, criterios)


def bruto_do_edital(sessao: Session, fonte: str, id_na_fonte: str) -> dict[str, Any] | None:
    """O registro bruto da fonte (anexos da Sanepar, por exemplo); None se o edital não existe."""
    return sessao.scalar(
        select(Edital.bruto).where(Edital.fonte == fonte, Edital.id_na_fonte == id_na_fonte)
    )


def datas_sessao_dae_jundiai(sessao: Session) -> dict[tuple[str, str], dict[str, Any]]:
    """Cache da data da sessão lida do PDF do edital da DAE Jundiaí, dos editais abertos.

    `{(cdcompra, arquivo do edital): bruto.detalhe.data_sessao_edital}` (sem migração: o valor
    mora no bruto, `{"arquivo", "data": iso | None, "conferido_em": iso}`). Quem decide se a
    entrada ainda vale é o coletor.
    """
    brutos = sessao.scalars(
        select(Edital.bruto).where(
            Edital.fonte == Fonte.dae_jundiai.value, Edital.situacao_normalizada == "aberta"
        )
    )
    datas: dict[tuple[str, str], dict[str, Any]] = {}
    for bruto in brutos:
        lista = bruto.get("lista") if isinstance(bruto, dict) else None
        detalhe = bruto.get("detalhe") if isinstance(bruto, dict) else None
        lido = detalhe.get("data_sessao_edital") if isinstance(detalhe, dict) else None
        if not isinstance(lista, dict) or not isinstance(lido, dict):
            continue
        cdcompra, arquivo = lista.get("cdcompra"), lido.get("arquivo")
        if cdcompra and arquivo:
            datas[(str(cdcompra), str(arquivo))] = lido
    return datas


def listar_abertos(
    sessao: Session, criterios: Criterios, agora: datetime, limite: int
) -> list[EditalDetalhe]:
    """Editais abertos em qualquer linha (só UF e modalidade dos critérios), por prazo.

    `linhas` vem preenchida quando o edital casa com alguma linha; fica vazia se não.
    """
    consulta = (
        select(*_COLUNAS_RESUMO, *_COLUNAS_DETALHE, *_rotulos(_casamentos(criterios)))
        .where(Edital.uf.in_(criterios.ufs))
        .where(_modalidade_aceita(criterios))
        .where(_aberto(agora))
        .order_by(Edital.data_encerramento_proposta.asc())
        .limit(limite)
    )
    return [_detalhe(linha, criterios) for linha in sessao.execute(consulta)]
