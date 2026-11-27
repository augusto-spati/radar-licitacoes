"""Histórico do órgão de um edital: contagens, últimas contratações, vencedores e régua.

Recorte: editais do PNCP que casam alguma linha do filtro, com resumo em `resultado_busca` e
encerramento nos últimos 24 meses. Contagens, últimas e vencedores ficam no mesmo CNPJ do
órgão; a régua usa todos os órgãos. Um edital de retrato (Sanepar, Compesa) usa o próprio
`orgao_cnpj` e enxerga os pregões que o órgão publica no PNCP.
"""

import statistics
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, case, false, func, or_, select
from sqlalchemy.orm import Session

from radar.editais.filtro import Criterios, _casa_termos, _casamentos, id_publico, obter_edital
from radar.editais.modelos import Edital, Fonte, ResultadoBusca, ResultadoItem
from radar.editais.resultados import PESSOA_FISICA

FUSO = ZoneInfo("America/Sao_Paulo")
MESES_RECORTE = 24
ULTIMAS = 10
TOP_VENCEDORES = 5
MINIMO_CASOS = 5
# Estatais da Lei 13.303 (limite 0,70); o resto segue a Lei 14.133 (0,75).
ESTATAIS_13303 = frozenset({"76484013000145", "09769035000164"})
LIMITE_14133 = Decimal("0.75")
LIMITE_13303 = Decimal("0.70")
UM_MILHAO = Decimal(1_000_000)
CINCO_MILHOES = Decimal(5_000_000)
_QUATRO_CASAS = Decimal("0.0001")


@dataclass(frozen=True)
class ContratacaoPassada:
    id_publico: str
    objeto: str
    data: datetime | None
    situacao: str
    valor_estimado: Decimal | None
    valor_homologado: Decimal | None
    desconto: Decimal | None
    parcial: bool
    vencedor_nome: str | None
    vencedor_porte: str | None


@dataclass(frozen=True)
class Vencedor:
    nome: str
    porte: str | None
    vitorias: int
    desconto_mediano: Decimal | None


@dataclass(frozen=True)
class Regua:
    linha: str | None
    uf: str | None
    faixa: str | None
    casos: int
    p25: Decimal | None
    mediana: Decimal | None
    p75: Decimal | None
    poucos_casos: bool


@dataclass(frozen=True)
class HistoricoOrgao:
    orgao_cnpj: str | None
    orgao_nome: str
    contagens: dict[str, int]
    total: int
    ultimas: list[ContratacaoPassada]
    vencedores: list[Vencedor]
    reguas: list[Regua]
    limite_inexequivel: Decimal


def limite_inexequivel(orgao_cnpj: str | None) -> Decimal:
    """0,70 do estimado para estatal da Lei 13.303 (Sanepar, Compesa); 0,75 para o resto."""
    return LIMITE_13303 if orgao_cnpj in ESTATAIS_13303 else LIMITE_14133


def faixa_de_valor(valor: Decimal | None) -> str | None:
    """`ate_1m` (≤ 1 mi), `1m_a_5m` (≤ 5 mi), `acima_5m`; None sem valor ou com valor ≤ 0."""
    if valor is None or valor <= 0:
        return None
    if valor <= UM_MILHAO:
        return "ate_1m"
    if valor <= CINCO_MILHOES:
        return "1m_a_5m"
    return "acima_5m"


def _faixa_sql() -> ColumnElement[Any]:
    return case(
        (Edital.valor_estimado <= 0, None),
        (Edital.valor_estimado <= UM_MILHAO, "ate_1m"),
        (Edital.valor_estimado <= CINCO_MILHOES, "1m_a_5m"),
        (Edital.valor_estimado > CINCO_MILHOES, "acima_5m"),
        else_=None,
    )


def _meses_atras(data: datetime, meses: int) -> datetime:
    total = data.year * 12 + data.month - 1 - meses
    ano, mes = divmod(total, 12)
    for dia in (data.day, 30, 29, 28):
        try:
            return data.replace(year=ano, month=mes + 1, day=dia)
        except ValueError:
            continue
    raise AssertionError("inalcançável")


def _recorte(criterios: Criterios, agora: datetime) -> list[ColumnElement[bool]]:
    """Contratações do PNCP que casam alguma linha, encerradas nos últimos 24 meses."""
    agora_brasilia = agora.astimezone(FUSO).replace(tzinfo=None)
    casamentos = _casamentos(criterios)
    return [
        Edital.fonte == Fonte.pncp.value,
        or_(*casamentos) if casamentos else false(),
        Edital.data_encerramento_proposta > _meses_atras(agora_brasilia, MESES_RECORTE),
    ]


def _juncao() -> ColumnElement[bool]:
    return and_(
        ResultadoBusca.fonte == Edital.fonte, ResultadoBusca.id_na_fonte == Edital.id_na_fonte
    )


def _contagens(sessao: Session, filtro: list[ColumnElement[bool]]) -> dict[str, int]:
    consulta = (
        select(ResultadoBusca.situacao, func.count())
        .join(Edital, _juncao())
        .where(*filtro)
        .group_by(ResultadoBusca.situacao)
        .order_by(ResultadoBusca.situacao)
    )
    return {situacao: int(n) for situacao, n in sessao.execute(consulta)}


def _referencia() -> ColumnElement[Any]:
    """Data do resultado mais recente da contratação; sem ela, o encerramento."""
    data_resultado = (
        select(func.max(ResultadoItem.data_resultado))
        .where(
            ResultadoItem.fonte == Edital.fonte,
            ResultadoItem.id_na_fonte == Edital.id_na_fonte,
        )
        .correlate(Edital)
        .scalar_subquery()
    )
    # O encerramento é hora de Brasília sem fuso; vira timestamptz para comparar.
    return func.coalesce(data_resultado, func.timezone(FUSO.key, Edital.data_encerramento_proposta))


def _positivo(valor: Decimal | None) -> Decimal | None:
    """Estimado ≤ 0 é "sem estimado" (a tela trata nulo)."""
    return valor if valor is not None and valor > 0 else None


def _ultimas(sessao: Session, filtro: list[ColumnElement[bool]]) -> list[ContratacaoPassada]:
    consulta = (
        select(
            Edital.fonte,
            Edital.id_na_fonte,
            Edital.objeto,
            Edital.data_encerramento_proposta,
            Edital.valor_estimado,
            ResultadoBusca.situacao,
            ResultadoBusca.valor_estimado_considerado,
            ResultadoBusca.valor_homologado_considerado,
            ResultadoBusca.desconto,
            ResultadoBusca.vencedor_nome,
            ResultadoBusca.vencedor_porte,
        )
        .join(ResultadoBusca, _juncao())
        .where(*filtro)
        .order_by(_referencia().desc().nulls_last(), Edital.id_na_fonte.desc())
        .limit(ULTIMAS)
    )
    return [
        ContratacaoPassada(
            id_publico=id_publico(linha.fonte, linha.id_na_fonte),
            objeto=linha.objeto,
            data=linha.data_encerramento_proposta,
            situacao=linha.situacao,
            # O par considerado é o que dá o desconto; sem ele, o estimado do edital.
            valor_estimado=_positivo(
                linha.valor_estimado_considerado
                if linha.valor_estimado_considerado is not None
                else linha.valor_estimado
            ),
            valor_homologado=linha.valor_homologado_considerado,
            desconto=linha.desconto,
            parcial=linha.situacao == "parcial",
            vencedor_nome=linha.vencedor_nome,
            vencedor_porte=linha.vencedor_porte,
        )
        for linha in sessao.execute(consulta)
    ]


def _mediana(valores: list[Decimal]) -> Decimal | None:
    if not valores:
        return None
    return Decimal(statistics.median(valores)).quantize(_QUATRO_CASAS)


def _vencedores(sessao: Session, filtro: list[ColumnElement[bool]]) -> list[Vencedor]:
    """Uma vitória por contratação (o vencedor do resumo). Pessoa física conta à parte.

    `parcial` conta vitória, mas o desconto provisório não entra na mediana (regra da régua).
    """
    consulta = (
        select(
            Edital.id_na_fonte,
            ResultadoBusca.vencedor_ni,
            ResultadoBusca.vencedor_nome,
            ResultadoBusca.vencedor_porte,
            ResultadoBusca.valor_homologado_considerado,
            ResultadoBusca.desconto,
            ResultadoBusca.situacao,
        )
        .join(Edital, _juncao())
        .where(*filtro)
        .where(ResultadoBusca.vencedor_nome.is_not(None))
        .order_by(_referencia().desc().nulls_last(), Edital.id_na_fonte.desc())
    )
    grupos: dict[tuple[str, str], dict[str, Any]] = {}
    for linha in sessao.execute(consulta):
        if linha.vencedor_ni:
            chave = ("ni", linha.vencedor_ni)
        elif linha.vencedor_nome == PESSOA_FISICA:
            chave = ("pf", linha.id_na_fonte)
        else:
            chave = ("nome", linha.vencedor_nome)
        # Mesma ordem de `_ultimas`: nome e porte vêm do resultado mais recente.
        grupo = grupos.setdefault(
            chave,
            {
                "nome": linha.vencedor_nome,
                "porte": linha.vencedor_porte,
                "vitorias": 0,
                "soma": Decimal(0),
                "descontos": [],
            },
        )
        grupo["vitorias"] += 1
        grupo["soma"] += linha.valor_homologado_considerado or Decimal(0)
        if linha.situacao == "homologada" and linha.desconto is not None:
            grupo["descontos"].append(linha.desconto)
    ordenados = sorted(grupos.values(), key=lambda g: (-g["vitorias"], -g["soma"]))
    return [
        Vencedor(g["nome"], g["porte"], g["vitorias"], _mediana(g["descontos"]))
        for g in ordenados[:TOP_VENCEDORES]
    ]


def _quartis(valores: list[Decimal]) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    if not valores:
        return None, None, None
    p25, mediana, p75 = (
        Decimal(v).quantize(_QUATRO_CASAS)
        for v in statistics.quantiles(valores, n=4, method="inclusive")
    )
    return p25, mediana, p75


def _regua(
    sessao: Session,
    filtro: list[ColumnElement[bool]],
    criterios: Criterios,
    proprio: tuple[str, str],
    linha: str | None,
    uf: str | None,
    faixa: str | None,
) -> Regua:
    condicoes = [
        *filtro,
        ResultadoBusca.situacao == "homologada",
        ResultadoBusca.desconto.is_not(None),
        # O próprio edital não mede a si mesmo.
        ~and_(Edital.fonte == proprio[0], Edital.id_na_fonte == proprio[1]),
    ]
    if linha is not None:
        condicoes.append(_casa_termos(criterios.linhas[linha]))
    if uf is not None:
        condicoes.append(Edital.uf == uf)
    if faixa is not None:
        condicoes.append(_faixa_sql() == faixa)
    consulta = select(ResultadoBusca.desconto).join(Edital, _juncao()).where(*condicoes)
    descontos = [d for d in sessao.scalars(consulta) if d is not None]
    p25, mediana, p75 = _quartis(descontos)
    return Regua(
        linha=linha,
        uf=uf,
        faixa=faixa,
        casos=len(descontos),
        p25=p25,
        mediana=mediana,
        p75=p75,
        poucos_casos=len(descontos) < MINIMO_CASOS,
    )


def historico_do_edital(
    sessao: Session, criterios: Criterios, fonte: str, id_na_fonte: str, agora: datetime
) -> HistoricoOrgao | None:
    """Histórico do órgão do edital; None só se o edital não existe.

    O próprio edital fica fora das réguas, mas entra nas contagens, últimas e vencedores.
    `agora` sem fuso → ValueError.
    Órgão sem nada no recorte (ou edital sem CNPJ) → contagens, últimas e vencedores vazios.
    Régua: uma por linha do edital (sem linha → uma de todas as linhas), na UF e faixa de valor
    do edital (sem faixa se o valor é nulo ou ≤ 0), só contratações `homologada` com desconto,
    de todos os órgãos; com menos de 5 casos, vem também a régua da mesma linha sem UF e sem
    faixa. Edital sem CNPJ tem histórico vazio, mas régua.
    """
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("agora precisa ter fuso horário")
    edital = obter_edital(sessao, fonte, id_na_fonte, criterios)
    if edital is None:
        return None
    recorte = _recorte(criterios, agora)
    do_orgao = Edital.orgao_cnpj == edital.orgao_cnpj if edital.orgao_cnpj else false()
    filtro = [*recorte, do_orgao]
    contagens = _contagens(sessao, filtro)
    faixa = faixa_de_valor(edital.valor_estimado)
    proprio = (fonte, id_na_fonte)
    reguas: list[Regua] = []
    linhas: list[str | None] = [*edital.linhas] or [None]
    for linha in linhas:
        regua = _regua(sessao, recorte, criterios, proprio, linha, edital.uf, faixa)
        reguas.append(regua)
        if regua.poucos_casos and (regua.uf is not None or regua.faixa is not None):
            reguas.append(_regua(sessao, recorte, criterios, proprio, linha, None, None))
    return HistoricoOrgao(
        orgao_cnpj=edital.orgao_cnpj,
        orgao_nome=edital.orgao_nome,
        contagens=contagens,
        total=sum(contagens.values()),
        ultimas=_ultimas(sessao, filtro),
        vencedores=_vencedores(sessao, filtro),
        reguas=reguas,
        limite_inexequivel=limite_inexequivel(edital.orgao_cnpj),
    )
