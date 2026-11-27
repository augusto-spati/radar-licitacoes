"""Resultados das contratações: interpretar itens do PNCP, resumir, gravar e escolher alvos.

Situação agregada em casos mistos: fracassado + deserto (sem homologado) → fracassada;
homologado + fracassado/deserto/anulado → homologada (ou parcial, se houver item em andamento).
"""

import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, false, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from radar.editais.filtro import Criterios, _casamentos, _modalidade_aceita
from radar.editais.modelos import Edital, Fonte, ResultadoBusca, ResultadoItem

logger = logging.getLogger(__name__)

FUSO = ZoneInfo("America/Sao_Paulo")
JANELA_DIAS = 180
REBUSCA_DIAS = 7
PESSOA_FISICA = "Pessoa física"
_ID_PNCP = re.compile(r"^(\d{14})-\d+-(\d+)/(\d{4})$")
_REBUSCAVEIS = ("parcial", "em_andamento", "sem_itens")

# Situações do item (nome do PNCP sem acento e em minúsculas → valor gravado).
_SITUACAO_ITEM = {
    "homologado": "homologado",
    "em andamento": "em_andamento",
    "fracassado": "fracassado",
    "deserto": "deserto",
    "anulado/revogado/cancelado": "anulado",
}


@dataclass(frozen=True)
class AlvoResultado:
    fonte: str
    id_na_fonte: str
    cnpj: str
    ano: int
    sequencial: int


@dataclass(frozen=True)
class ItemResultado:
    numero_item: int
    descricao: str
    unidade: str | None
    quantidade: Decimal | None
    valor_unitario_estimado: Decimal | None
    valor_total_estimado: Decimal | None
    orcamento_sigiloso: bool
    situacao: str
    fornecedor_ni: str | None = None
    fornecedor_nome: str | None = None
    fornecedor_porte: str | None = None
    quantidade_homologada: Decimal | None = None
    valor_unitario_homologado: Decimal | None = None
    valor_total_homologado: Decimal | None = None
    data_resultado: datetime | None = None


@dataclass(frozen=True)
class ResumoContratacao:
    situacao: str
    desconto: Decimal | None
    estimado_considerado: Decimal | None
    homologado_considerado: Decimal | None
    vencedor_ni: str | None
    vencedor_nome: str | None
    vencedor_porte: str | None


def _chave(id_na_fonte: str) -> tuple[str, int, int] | None:
    casamento = _ID_PNCP.match(id_na_fonte)
    if casamento is None:
        return None
    cnpj, sequencial, ano = casamento.groups()
    return cnpj, int(ano), int(sequencial)


def alvos_resultado(
    sessao: Session, criterios: Criterios, agora: datetime, limite: int
) -> list[AlvoResultado]:
    """Editais do PNCP que casam o filtro, já encerrados, para buscar ou rebuscar resultados.

    Nunca buscado entra qualquer que seja a idade do encerramento. Rebusca (buscado há mais de
    7 dias, em `parcial`/`em_andamento`/`sem_itens` ou `homologada` sem vencedor) só para
    encerrado nos últimos 180 dias. Nunca buscados vêm primeiro.
    """
    agora_brasilia = agora.astimezone(FUSO).replace(tzinfo=None)
    casamentos = _casamentos(criterios)
    sete_dias = agora - timedelta(days=REBUSCA_DIAS)
    consulta = (
        select(Edital.fonte, Edital.id_na_fonte)
        .outerjoin(
            ResultadoBusca,
            and_(
                ResultadoBusca.fonte == Edital.fonte,
                ResultadoBusca.id_na_fonte == Edital.id_na_fonte,
            ),
        )
        .where(Edital.fonte == Fonte.pncp.value)
        .where(or_(*casamentos) if casamentos else false())
        .where(Edital.uf.in_(criterios.ufs))
        .where(_modalidade_aceita(criterios))
        .where(Edital.data_encerramento_proposta < agora_brasilia)
        .where(
            or_(
                # Nunca buscado entra sem limite de idade (a carga histórica precisa disso).
                ResultadoBusca.id_na_fonte.is_(None),
                # Rebusca: só dentro da janela de 180 dias, uma vez por semana.
                and_(
                    Edital.data_encerramento_proposta
                    > agora_brasilia - timedelta(days=JANELA_DIAS),
                    ResultadoBusca.buscado_em < sete_dias,
                    or_(
                        ResultadoBusca.situacao.in_(_REBUSCAVEIS),
                        and_(
                            ResultadoBusca.situacao == "homologada",
                            ResultadoBusca.vencedor_nome.is_(None),
                        ),
                    ),
                ),
            )
        )
        .order_by(
            ResultadoBusca.buscado_em.asc().nulls_first(),
            Edital.data_encerramento_proposta.desc(),
        )
        .limit(limite)
    )
    alvos = []
    for fonte, id_na_fonte in sessao.execute(consulta):
        chave = _chave(id_na_fonte)
        if chave is None:
            logger.warning("id do PNCP fora do formato esperado: %s", id_na_fonte)
            continue
        alvos.append(AlvoResultado(fonte, id_na_fonte, *chave))
    return alvos


def _sem_acento(texto: str) -> str:
    base = unicodedata.normalize("NFD", texto)
    return "".join(c for c in base if not unicodedata.combining(c)).strip().lower()


def _situacao_item(nome: Any) -> str:
    situacao = _SITUACAO_ITEM.get(_sem_acento(str(nome or "")))
    if situacao is None:
        logger.warning(
            "situação de item desconhecida no PNCP: %r (tratada como em andamento)", nome
        )
        return "em_andamento"
    return situacao


def _decimal(valor: Any) -> Decimal | None:
    if valor is None or valor == "":
        return None
    try:
        numero = Decimal(str(valor))
    except ArithmeticError:
        return None
    return numero if numero.is_finite() else None


def _soma(valores: list[Decimal | None]) -> Decimal | None:
    presentes = [v for v in valores if v is not None]
    return sum(presentes, Decimal(0)) if presentes else None


def _data(valor: Any) -> datetime | None:
    if not valor:
        return None
    try:
        data = datetime.fromisoformat(str(valor))
    except ValueError:
        return None
    return data if data.tzinfo else data.replace(tzinfo=FUSO)


def _campos_vencedor(resultados: list[dict[str, Any]]) -> dict[str, Any]:
    """Um item pode ter mais de um resultado: o fornecedor é o de maior valor; os totais somam."""
    if not resultados:
        return {}
    melhor = max(resultados, key=lambda r: _decimal(r.get("valorTotalHomologado")) or Decimal(0))
    ni = "".join(c for c in str(melhor.get("niFornecedor") or "") if c.isdigit())
    pessoa_fisica = len(ni) == 11
    return {
        "fornecedor_ni": None if pessoa_fisica else (ni or None),
        "fornecedor_nome": PESSOA_FISICA
        if pessoa_fisica
        else melhor.get("nomeRazaoSocialFornecedor"),
        "fornecedor_porte": melhor.get("porteFornecedorNome"),
        "quantidade_homologada": _soma(
            [_decimal(r.get("quantidadeHomologada")) for r in resultados]
        ),
        "valor_unitario_homologado": _decimal(melhor.get("valorUnitarioHomologado")),
        "valor_total_homologado": _soma(
            [_decimal(r.get("valorTotalHomologado")) for r in resultados]
        ),
        "data_resultado": _data(melhor.get("dataResultado")),
    }


def interpretar_itens(itens: list[dict[str, Any]]) -> list[ItemResultado]:
    """Itens crus do PNCP (com `resultados`) → `ItemResultado`; CPF vira "Pessoa física"."""
    return [
        ItemResultado(
            numero_item=int(bruto["numeroItem"]),
            descricao=str(bruto.get("descricao") or ""),
            unidade=bruto.get("unidadeMedida"),
            quantidade=_decimal(bruto.get("quantidade")),
            valor_unitario_estimado=_decimal(bruto.get("valorUnitarioEstimado")),
            valor_total_estimado=_decimal(bruto.get("valorTotal")),
            orcamento_sigiloso=bool(bruto.get("orcamentoSigiloso")),
            situacao=_situacao_item(bruto.get("situacaoCompraItemNome")),
            **_campos_vencedor([r for r in bruto.get("resultados") or [] if isinstance(r, dict)]),
        )
        for bruto in itens
    ]


def _situacao_agregada(itens: list[ItemResultado]) -> str:
    if not itens:
        return "sem_itens"
    situacoes = {i.situacao for i in itens}
    if "homologado" in situacoes:
        return "parcial" if "em_andamento" in situacoes else "homologada"
    if "em_andamento" in situacoes:
        return "em_andamento"
    if "fracassado" in situacoes:
        return "fracassada"
    if "deserto" in situacoes:
        return "deserta"
    return "anulada"


def _estimado_comparavel(item: ItemResultado) -> Decimal | None:
    """Estimado da quantidade homologada: com homologação parcial da quantidade (0 < q_hom < q),
    vale o unitário estimado × q_hom; senão, o total estimado do item."""
    q, q_hom, unitario = item.quantidade, item.quantidade_homologada, item.valor_unitario_estimado
    if q is not None and q_hom is not None and unitario is not None and 0 < q_hom < q:
        return unitario * q_hom
    return item.valor_total_estimado


def resumir(itens: list[ItemResultado]) -> ResumoContratacao:
    """Situação, desconto (Σ homologado ÷ Σ estimado dos itens com estimado conhecido), vencedor.

    O estimado de cada item é o da quantidade homologada (ver `_estimado_comparavel`).
    """
    homologados = [i for i in itens if i.situacao == "homologado"]
    pares: list[tuple[Decimal, Decimal]] = []
    for i in homologados:
        estimado_item = _estimado_comparavel(i)
        if (
            not i.orcamento_sigiloso
            and estimado_item is not None
            and estimado_item > 0
            and i.valor_total_homologado is not None
        ):
            pares.append((estimado_item, i.valor_total_homologado))
    desconto = estimado = homologado = None
    if pares:
        estimado = sum((e for e, _ in pares), Decimal(0))
        homologado = sum((h for _, h in pares), Decimal(0))
        desconto = (1 - homologado / estimado).quantize(Decimal("0.0001"))
    totais: dict[tuple[str, str | int], Decimal] = {}
    quem: dict[tuple[str, str | int], ItemResultado] = {}
    for i in homologados:
        if i.fornecedor_ni:
            chave: tuple[str, str | int] = ("ni", i.fornecedor_ni)
        elif i.fornecedor_nome == PESSOA_FISICA:
            chave = ("pf", i.numero_item)  # cada item de pessoa física conta à parte
        elif i.fornecedor_nome:
            chave = ("nome", i.fornecedor_nome)
        else:
            continue
        quem.setdefault(chave, i)
        totais[chave] = totais.get(chave, Decimal(0)) + (i.valor_total_homologado or Decimal(0))
    ni = nome = porte = None
    if totais:
        vencedor = quem[max(totais, key=lambda c: totais[c])]
        ni, nome, porte = (
            vencedor.fornecedor_ni,
            vencedor.fornecedor_nome,
            vencedor.fornecedor_porte,
        )
    return ResumoContratacao(
        _situacao_agregada(itens), desconto, estimado, homologado, ni, nome, porte
    )


def gravar_resultados(
    sessao: Session,
    fonte: str,
    id_na_fonte: str,
    itens: list[ItemResultado],
    agora: datetime,
) -> ResumoContratacao:
    """Upsert dos itens e do resumo da busca (sem commit).

    Lista vazia com itens já gravados não apaga nada (resposta transitória): só renova
    `buscado_em`. Resultado que vinha preenchido nunca é trocado por nulo. O resumo sai das
    linhas já mescladas.
    """
    chaves = {"fonte": fonte, "id_na_fonte": id_na_fonte}
    unicos = {i.numero_item: i for i in itens}  # número repetido no lote: o último vence
    if unicos:
        linhas = [{**chaves, **asdict(i)} for i in unicos.values()]
        insercao = insert(ResultadoItem).values(linhas)
        preservados = {
            "fornecedor_ni",
            "fornecedor_nome",
            "fornecedor_porte",
            "quantidade_homologada",
            "valor_unitario_homologado",
            "valor_total_homologado",
            "data_resultado",
        }
        sessao.execute(
            insercao.on_conflict_do_update(
                constraint="uq_resultado_item",
                set_={
                    c: func.coalesce(insercao.excluded[c], ResultadoItem.__table__.c[c])
                    if c in preservados
                    else insercao.excluded[c]
                    for c in linhas[0]
                    if c not in ("fonte", "id_na_fonte", "numero_item")
                },
            )
        )
        sessao.execute(
            delete(ResultadoItem).where(
                ResultadoItem.fonte == fonte,
                ResultadoItem.id_na_fonte == id_na_fonte,
                ResultadoItem.numero_item.not_in(list(unicos)),
            )
        )
    campos = [f.name for f in fields(ItemResultado)]
    gravados = sessao.execute(
        select(*(ResultadoItem.__table__.c[c] for c in campos)).where(
            ResultadoItem.fonte == fonte, ResultadoItem.id_na_fonte == id_na_fonte
        )
    )
    itens_gravados = [ItemResultado(**dict(zip(campos, linha, strict=True))) for linha in gravados]
    resumo = resumir(itens_gravados)
    itens = itens_gravados
    busca = {
        **chaves,
        "buscado_em": agora,
        "itens": len(itens),
        "situacao": resumo.situacao,
        "desconto": resumo.desconto,
        "valor_estimado_considerado": resumo.estimado_considerado,
        "valor_homologado_considerado": resumo.homologado_considerado,
        "vencedor_ni": resumo.vencedor_ni,
        "vencedor_nome": resumo.vencedor_nome,
        "vencedor_porte": resumo.vencedor_porte,
    }
    inserir = insert(ResultadoBusca).values(busca)
    sessao.execute(
        inserir.on_conflict_do_update(
            index_elements=["fonte", "id_na_fonte"],
            set_={c: inserir.excluded[c] for c in busca if c not in chaves},
        )
    )
    return resumo
