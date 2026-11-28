"""Filtro dos editais por linha de serviço, UF, modalidade (só PNCP) e prazo."""

import tomllib
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    column,
    false,
    func,
    literal_column,
    null,
    or_,
    select,
    table,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import LateralFromClause

from radar.editais.modelos import Edital, Fonte, Situacao

FUSO = ZoneInfo("America/Sao_Paulo")

FiltroVeredito = Literal["apto", "verificar", "inapto", "sem_analise"]
DIAS_NOVO = 3

# `editais` não importa `analise`: lê a tabela só pelo nome e pelas colunas usadas.
_ANALISE = table(
    "analise_edital",
    column("id"),
    column("fonte"),
    column("id_na_fonte"),
    column("modelo"),
    column("criada_em"),
    column("veredito"),
    column("comparacao", JSONB),
)

_CAMINHO_VERIFICAR: Any = literal_column("""'$.itens[*] ? (@.situacao == "verificar").campo'""")


@dataclass(frozen=True)
class Recorte:
    """Recorte da Lista: prazo em dias de calendário, novos e vereditos (vazio = todos)."""

    encerra_em_dias: int | None = None
    novos: bool = False
    vereditos: frozenset[str] = frozenset()


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
    veredito: str | None
    motivo_verificar: str | None
    primeiro_visto_em: datetime | None

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
    Edital.primeiro_visto_em,
]


def _casamentos(criterios: Criterios) -> list[ColumnElement[bool]]:
    return [_casa_termos(termos) for termos in criterios.linhas.values()]


def _rotulos(casamentos: list[ColumnElement[bool]]) -> list[Any]:
    """Uma coluna booleana `linha_<i>` por linha de serviço: o edital casa com ela?"""
    return [c.label(f"linha_{i}") for i, c in enumerate(casamentos)]


def _colunas_analise(modelo: str | None) -> tuple[list[Any], LateralFromClause | None]:
    """Colunas `veredito` e `motivo_verificar` da análise mais nova do modelo (e o lateral)."""
    if modelo is None:
        return [null().label("veredito"), null().label("motivo_verificar")], None
    ultima = (
        select(_ANALISE.c.veredito, _ANALISE.c.comparacao)
        .where(
            _ANALISE.c.fonte == Edital.fonte,
            _ANALISE.c.id_na_fonte == Edital.id_na_fonte,
            _ANALISE.c.modelo == modelo,
        )
        .order_by(_ANALISE.c.criada_em.desc(), _ANALISE.c.id.desc())
        .limit(1)
        .lateral("ultima")
    )
    motivo = func.jsonb_path_query_first(ultima.c.comparacao, _CAMINHO_VERIFICAR).op("#>>")(
        literal_column("'{}'")
    )
    return [ultima.c.veredito.label("veredito"), motivo.label("motivo_verificar")], ultima


def _com_analise(consulta: Select[Any], lateral: LateralFromClause | None) -> Select[Any]:
    if lateral is None:
        return consulta
    return consulta.select_from(Edital.__table__.outerjoin(lateral, true()))


def limite_encerra_em(agora: datetime, dias: int) -> datetime:
    """Limite exclusivo (Brasília, sem fuso) de "encerra em até `dias` dias de calendário".

    O dia `hoje + dias` entra inteiro (até 23:59); 00:00 do dia seguinte já não.
    """
    hoje = agora.astimezone(FUSO).date()
    return datetime.combine(hoje, time()) + timedelta(days=dias + 1)


def _condicoes_recorte(
    recorte: Recorte, agora: datetime, veredito: ColumnElement[Any]
) -> list[ColumnElement[bool]]:
    condicoes: list[ColumnElement[bool]] = []
    if recorte.encerra_em_dias is not None:
        condicoes.append(
            Edital.data_encerramento_proposta < limite_encerra_em(agora, recorte.encerra_em_dias)
        )
    if recorte.novos:
        condicoes.append(Edital.primeiro_visto_em >= agora - timedelta(days=DIAS_NOVO))
    if recorte.vereditos:
        nomes = [v for v in recorte.vereditos if v != "sem_analise"]
        opcoes: list[ColumnElement[bool]] = [veredito.in_(nomes)] if nomes else []
        if "sem_analise" in recorte.vereditos:
            opcoes.append(veredito.is_(None))
        condicoes.append(or_(*opcoes))
    return condicoes


def _campos_resumo(linha: Any, criterios: Criterios) -> dict[str, Any]:
    campos = {coluna.key: linha._mapping[coluna.key] for coluna in _COLUNAS_RESUMO}
    campos["veredito"] = linha._mapping["veredito"]
    campos["motivo_verificar"] = linha._mapping["motivo_verificar"]
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
    criterios: Criterios,
    agora: datetime,
    somente_abertos: bool,
    limite: int | None,
    modelo_analise: str | None,
    recorte: Recorte,
) -> Select[tuple[Any, ...]]:
    casamentos = _casamentos(criterios)
    colunas, lateral = _colunas_analise(modelo_analise)
    veredito = null() if lateral is None else lateral.c.veredito
    # Só as colunas do EditalResumo: `bruto` e `busca` são pesados e não são lidos aqui.
    consulta = _com_analise(select(*_COLUNAS_RESUMO, *colunas, *_rotulos(casamentos)), lateral)
    consulta = (
        consulta.where(or_(*casamentos) if casamentos else false())
        .where(Edital.uf.in_(criterios.ufs))
        .where(_modalidade_aceita(criterios))
        .where(*_condicoes_recorte(recorte, agora, veredito))
        .order_by(Edital.data_encerramento_proposta.asc().nulls_last())
    )
    if limite is not None:
        consulta = consulta.limit(limite)
    if somente_abertos:
        consulta = consulta.where(_aberto(agora))
    return consulta


def filtrar(
    sessao: Session,
    criterios: Criterios,
    agora: datetime,
    somente_abertos: bool = True,
    limite: int | None = 200,
    modelo_analise: str | None = None,
    recorte: Recorte = Recorte(),
) -> list[EditalResumo]:
    consulta = _montar_consulta(criterios, agora, somente_abertos, limite, modelo_analise, recorte)
    linhas = sessao.execute(consulta)
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
    sessao: Session,
    fonte: str,
    id_na_fonte: str,
    criterios: Criterios,
    modelo_analise: str | None = None,
) -> EditalDetalhe | None:
    """Um edital pela chave, com as linhas que casam (mesma regra de `filtrar`).

    Não aplica UF, modalidade nem prazo: quem pede um edital pela chave o recebe.
    """
    colunas, lateral = _colunas_analise(modelo_analise)
    consulta = _com_analise(
        select(*_COLUNAS_RESUMO, *colunas, *_COLUNAS_DETALHE, *_rotulos(_casamentos(criterios))),
        lateral,
    ).where(Edital.fonte == fonte, Edital.id_na_fonte == id_na_fonte)
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
    sessao: Session,
    criterios: Criterios,
    agora: datetime,
    limite: int,
    modelo_analise: str | None = None,
) -> list[EditalDetalhe]:
    """Editais abertos em qualquer linha (só UF e modalidade dos critérios), por prazo.

    `linhas` vem preenchida quando o edital casa com alguma linha; fica vazia se não.
    """
    colunas, lateral = _colunas_analise(modelo_analise)
    consulta = (
        _com_analise(
            select(
                *_COLUNAS_RESUMO, *colunas, *_COLUNAS_DETALHE, *_rotulos(_casamentos(criterios))
            ),
            lateral,
        )
        .where(Edital.uf.in_(criterios.ufs))
        .where(_modalidade_aceita(criterios))
        .where(_aberto(agora))
        .order_by(Edital.data_encerramento_proposta.asc())
        .limit(limite)
    )
    return [_detalhe(linha, criterios) for linha in sessao.execute(consulta)]
