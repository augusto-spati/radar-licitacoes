"""Histórico do órgão de um edital: contagens, últimas contratações, vencedores e régua.

Recorte: editais do PNCP que casam alguma linha do filtro, com resumo em `resultado_busca` e
encerramento nos últimos 24 meses, mais os casos de fora do PNCP em `resultado_externo`
(processos da Compesa no TCE-PE, com sessão de abertura nos últimos 24 meses e alguma linha).
Os dois viram uma subconsulta só (`_casos`), com as mesmas colunas. Contagens, últimas e
vencedores ficam no mesmo CNPJ do órgão; a régua usa todos os órgãos. Um edital de retrato
(Sanepar, Compesa) usa o próprio `orgao_cnpj`: enxerga os pregões que o órgão publica no PNCP e
os processos dele no TCE-PE.
"""

import statistics
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import (
    ColumnElement,
    Subquery,
    Text,
    and_,
    any_,
    case,
    false,
    func,
    literal,
    null,
    or_,
    select,
    union_all,
)
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy.orm import Session

from radar.editais.externos import (
    FONTE_TCEPE,
    ROTULOS_FONTE,
    OrigemHistorico,
    numero_licitacao,
)
from radar.editais.filtro import Criterios, _casamentos, bruto_do_edital, id_publico, obter_edital
from radar.editais.modelos import Edital, Fonte, ResultadoBusca, ResultadoExterno, ResultadoItem
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
    origem: OrigemHistorico = "PNCP"  # de onde veio o resultado (ROTULOS_FONTE)


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


def _faixa_sql(valor: Any) -> ColumnElement[Any]:
    return case(
        (valor <= 0, None),
        (valor <= UM_MILHAO, "ate_1m"),
        (valor <= CINCO_MILHOES, "1m_a_5m"),
        (valor > CINCO_MILHOES, "acima_5m"),
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


def _juncao() -> ColumnElement[bool]:
    return and_(
        ResultadoBusca.fonte == Edital.fonte, ResultadoBusca.id_na_fonte == Edital.id_na_fonte
    )


def _referencia_pncp() -> ColumnElement[Any]:
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


def _casos(criterios: Criterios, agora: datetime) -> Subquery:
    """Casos do PNCP (edital + resumo) e de fora dele, dos últimos 24 meses, com alguma linha.

    Colunas: fonte, id_na_fonte, orgao_cnpj, uf, objeto, data, valor_edital (o estimado do
    edital, que dá a faixa), referencia (ordem das últimas), numero (só os externos), o resumo
    (situacao, desconto, estimado e homologado considerados, vencedor) e um `linha_<i>`
    booleano por linha do filtro, na ordem de `criterios.linhas`.
    """
    agora_brasilia = agora.astimezone(FUSO).replace(tzinfo=None)
    inicio = _meses_atras(agora_brasilia, MESES_RECORTE)
    casamentos = _casamentos(criterios)
    nomes = list(criterios.linhas)
    pncp = (
        select(
            Edital.fonte.label("fonte"),
            Edital.id_na_fonte.label("id_na_fonte"),
            Edital.orgao_cnpj.label("orgao_cnpj"),
            Edital.uf.label("uf"),
            Edital.objeto.label("objeto"),
            Edital.data_encerramento_proposta.label("data"),
            Edital.valor_estimado.label("valor_edital"),
            _referencia_pncp().label("referencia"),
            null().cast(Text).label("numero"),
            ResultadoBusca.situacao.label("situacao"),
            ResultadoBusca.desconto.label("desconto"),
            ResultadoBusca.valor_estimado_considerado.label("estimado_considerado"),
            ResultadoBusca.valor_homologado_considerado.label("homologado_considerado"),
            ResultadoBusca.vencedor_ni.label("vencedor_ni"),
            ResultadoBusca.vencedor_nome.label("vencedor_nome"),
            ResultadoBusca.vencedor_porte.label("vencedor_porte"),
            *(c.label(f"linha_{i}") for i, c in enumerate(casamentos)),
        )
        .join(ResultadoBusca, _juncao())
        .where(
            Edital.fonte == Fonte.pncp.value,
            or_(*casamentos) if casamentos else false(),
            Edital.data_encerramento_proposta > inicio,
        )
    )
    externo = ResultadoExterno
    # As linhas do caso externo foram decididas na gravação; vale só o que ainda é linha.
    tem_linha = (
        externo.linhas.op("&&")(array(nomes, type_=Text).cast(ARRAY(Text))) if nomes else false()
    )
    externos = select(
        externo.fonte.label("fonte"),
        externo.id_na_fonte.label("id_na_fonte"),
        externo.orgao_cnpj.label("orgao_cnpj"),
        externo.uf.label("uf"),
        externo.objeto.label("objeto"),
        externo.data.label("data"),
        externo.valor_estimado.label("valor_edital"),
        func.coalesce(externo.data_resultado, func.timezone(FUSO.key, externo.data)).label(
            "referencia"
        ),
        externo.numero.label("numero"),
        externo.situacao.label("situacao"),
        externo.desconto.label("desconto"),
        externo.valor_estimado.label("estimado_considerado"),
        externo.valor_homologado.label("homologado_considerado"),
        externo.vencedor_ni.label("vencedor_ni"),
        externo.vencedor_nome.label("vencedor_nome"),
        externo.vencedor_porte.label("vencedor_porte"),
        *(
            (literal(nome, Text) == any_(externo.linhas)).label(f"linha_{i}")
            for i, nome in enumerate(nomes)
        ),
    ).where(externo.data > inicio, tem_linha)
    return union_all(pncp, externos).subquery("caso")


def _ordem(casos: Subquery) -> list[Any]:
    return [casos.c.referencia.desc().nulls_last(), casos.c.id_na_fonte.desc()]


def _contagens(
    sessao: Session, casos: Subquery, filtro: list[ColumnElement[bool]]
) -> dict[str, int]:
    consulta = (
        select(casos.c.situacao, func.count())
        .where(*filtro)
        .group_by(casos.c.situacao)
        .order_by(casos.c.situacao)
    )
    return {situacao: int(n) for situacao, n in sessao.execute(consulta)}


def _positivo(valor: Decimal | None) -> Decimal | None:
    """Estimado ≤ 0 é "sem estimado" (a tela trata nulo)."""
    return valor if valor is not None and valor > 0 else None


def _ultimas(
    sessao: Session, casos: Subquery, filtro: list[ColumnElement[bool]]
) -> list[ContratacaoPassada]:
    consulta = select(casos).where(*filtro).order_by(*_ordem(casos)).limit(ULTIMAS)
    return [
        ContratacaoPassada(
            id_publico=id_publico(linha.fonte, linha.id_na_fonte),
            objeto=linha.objeto,
            data=linha.data,
            situacao=linha.situacao,
            # O par considerado é o que dá o desconto; sem ele, o estimado do edital.
            valor_estimado=_positivo(
                linha.estimado_considerado
                if linha.estimado_considerado is not None
                else linha.valor_edital
            ),
            valor_homologado=linha.homologado_considerado,
            desconto=linha.desconto,
            parcial=linha.situacao == "parcial",
            vencedor_nome=linha.vencedor_nome,
            vencedor_porte=linha.vencedor_porte,
            origem=ROTULOS_FONTE[linha.fonte],
        )
        for linha in sessao.execute(consulta)
    ]


def _mediana(valores: list[Decimal]) -> Decimal | None:
    if not valores:
        return None
    return Decimal(statistics.median(valores)).quantize(_QUATRO_CASAS)


def _vencedores(
    sessao: Session, casos: Subquery, filtro: list[ColumnElement[bool]]
) -> list[Vencedor]:
    """Uma vitória por contratação (o vencedor do resumo). Pessoa física conta à parte.

    `parcial` conta vitória, mas o desconto provisório não entra na mediana (regra da régua).
    O mesmo CNPJ no PNCP e no TCE-PE soma as vitórias.
    """
    consulta = (
        select(
            casos.c.fonte,
            casos.c.id_na_fonte,
            casos.c.vencedor_ni,
            casos.c.vencedor_nome,
            casos.c.vencedor_porte,
            casos.c.homologado_considerado,
            casos.c.desconto,
            casos.c.situacao,
        )
        .where(*filtro)
        .where(casos.c.vencedor_nome.is_not(None))
        .order_by(*_ordem(casos))
    )
    grupos: dict[tuple[str, str], dict[str, Any]] = {}
    for linha in sessao.execute(consulta):
        if linha.vencedor_ni:
            chave = ("ni", linha.vencedor_ni)
        elif linha.vencedor_nome == PESSOA_FISICA:
            chave = ("pf", id_publico(linha.fonte, linha.id_na_fonte))
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
        grupo["soma"] += linha.homologado_considerado or Decimal(0)
        if grupo["porte"] is None:
            grupo["porte"] = linha.vencedor_porte  # o TCE-PE não tem porte; o PNCP pode ter
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


def _proprios(
    sessao: Session, casos: Subquery, fonte: str, id_na_fonte: str, orgao_cnpj: str | None
) -> list[ColumnElement[bool]]:
    """Casos que são o próprio edital: ele mesmo e, para um edital do portal da Compesa, o
    processo do TCE-PE do mesmo órgão com o mesmo número de licitação ("LC 213/2026 CEL-1" ↔
    `numeroModalidade` 213 de 2026)."""
    proprios = [and_(casos.c.fonte == fonte, casos.c.id_na_fonte == id_na_fonte)]
    if fonte == Fonte.compesa.value and orgao_cnpj:
        bruto = bruto_do_edital(sessao, fonte, id_na_fonte) or {}
        numero = numero_licitacao(bruto.get("numero"))
        if numero is not None:
            proprios.append(
                and_(
                    casos.c.fonte == FONTE_TCEPE,
                    casos.c.orgao_cnpj == orgao_cnpj,
                    casos.c.numero == numero,
                )
            )
    return proprios


def _regua(
    sessao: Session,
    casos: Subquery,
    criterios: Criterios,
    proprios: list[ColumnElement[bool]],
    linha: str | None,
    uf: str | None,
    faixa: str | None,
) -> Regua:
    condicoes = [
        casos.c.situacao == "homologada",
        casos.c.desconto.is_not(None),
        # O próprio edital não mede a si mesmo.
        # coalesce: com `numero` nulo a comparação é NULL, e NOT NULL tiraria o caso.
        *(~func.coalesce(proprio, false()) for proprio in proprios),
    ]
    if linha is not None:
        condicoes.append(casos.c[f"linha_{list(criterios.linhas).index(linha)}"])
    if uf is not None:
        condicoes.append(casos.c.uf == uf)
    if faixa is not None:
        condicoes.append(_faixa_sql(casos.c.valor_edital) == faixa)
    consulta = select(casos.c.desconto).where(*condicoes)
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

    O próprio edital fica fora das réguas, mas entra nas contagens, últimas e vencedores (o
    edital do portal da Compesa também deixa de fora o processo do TCE-PE de mesmo número).
    `agora` sem fuso → ValueError.
    Órgão sem nada no recorte (ou edital sem CNPJ) → contagens, últimas e vencedores vazios.
    Régua: uma por linha do edital (sem linha → uma de todas as linhas), na UF e faixa de valor
    do edital (sem faixa se o valor é nulo ou ≤ 0), só contratações `homologada` com desconto,
    de todos os órgãos e das duas origens (PNCP e TCE-PE); com menos de 5 casos, vem também a
    régua da mesma linha sem UF e sem faixa. Edital sem CNPJ tem histórico vazio, mas régua.
    """
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("agora precisa ter fuso horário")
    edital = obter_edital(sessao, fonte, id_na_fonte, criterios)
    if edital is None:
        return None
    casos = _casos(criterios, agora)
    filtro = [casos.c.orgao_cnpj == edital.orgao_cnpj if edital.orgao_cnpj else false()]
    contagens = _contagens(sessao, casos, filtro)
    faixa = faixa_de_valor(edital.valor_estimado)
    proprios = _proprios(sessao, casos, fonte, id_na_fonte, edital.orgao_cnpj)
    reguas: list[Regua] = []
    linhas: list[str | None] = [*edital.linhas] or [None]
    for linha in linhas:
        regua = _regua(sessao, casos, criterios, proprios, linha, edital.uf, faixa)
        reguas.append(regua)
        if regua.poucos_casos and (regua.uf is not None or regua.faixa is not None):
            reguas.append(_regua(sessao, casos, criterios, proprios, linha, None, None))
    return HistoricoOrgao(
        orgao_cnpj=edital.orgao_cnpj,
        orgao_nome=edital.orgao_nome,
        contagens=contagens,
        total=sum(contagens.values()),
        ultimas=_ultimas(sessao, casos, filtro),
        vencedores=_vencedores(sessao, casos, filtro),
        reguas=reguas,
        limite_inexequivel=limite_inexequivel(edital.orgao_cnpj),
    )
