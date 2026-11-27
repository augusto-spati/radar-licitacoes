"""Planilhas orçamentárias do PNCP (7b.2): classificar as linhas e gravar em `preco_item`.

Não baixa nem extrai nada: recebe as linhas já lidas (`planilha_leitura`) e o edital; quem
compõe com os documentos e a IA é a CLI.

- Só linha com serviço-padrão é gravada, origem `pncp_planilha`, chave
  `<id_pncp>#<sha256[:12]>#<item>`, `processo` = id do edital no PNCP (é o caso das medianas).
- Classificação com o contexto da obra: título da seção da planilha e, sem ele, o objeto;
  em seção de ramal ("4.0 Ramal") o tubo não é metro de rede.
- BDI: com e sem BDI na linha → `bdi = com ÷ sem − 1` (ou o `%` da linha); um preço só e
  nenhum BDI declarado → `bdi_desconhecido` (regra da 7b.1). Compara-se sempre o com BDI.
- Republicação: a mesma planilha (sha256) ou, do mesmo órgão, 80%+ das linhas (das duas
  planilhas: um lote só de uma planilha de vários não conta) com o mesmo (código,
  quantidade, preço) de outro edital já gravado. As linhas ficam uma vez só, no edital
  processado por último (a rodada vai do mais antigo ao mais novo); o anterior vira
  `republicada`.
"""

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from radar.precos.modelos import (
    GastoPlanilha,
    MetodoPlanilha,
    Origem,
    PlanilhaEdital,
    PrecoItem,
    PrecoItemNovo,
    StatusPlanilha,
)
from radar.precos.planilha_leitura import LinhaPlanilha, bdi_plausivel
from radar.precos.servicos import Servico, classificar, contexto_da_obra, secao_de_ramal
from radar.precos.tcepe import gravar_precos

logger = logging.getLogger(__name__)

ORIGEM: Origem = "pncp_planilha"
REPUBLICACAO_MINIMA = Decimal("0.8")
LINHAS_MINIMAS_REPUBLICACAO = 3
# `falhou` tenta de novo depois disso; `pulado_teto`, no mês seguinte.
ESPERA_FALHA = timedelta(days=7)
_FINAIS = ("extraida", "sem_planilha", "republicada", "ia_invalida")

type _Assinatura = tuple[str | None, Decimal | None, Decimal | None]


@dataclass(frozen=True)
class EditalPlanilha:
    """O que a gravação precisa do edital (quem monta lê do banco: precos não importa editais)."""

    id_pncp: str
    uf: str
    municipio: str | None
    orgao_nome: str
    orgao_cnpj: str | None
    objeto: str
    data: date | None


@dataclass(frozen=True)
class Gravacao:
    gravadas: int
    # Editais anteriores com a mesma planilha (as linhas deles foram apagadas).
    republicadas: tuple[str, ...]


def _bdi(linha: LinhaPlanilha) -> tuple[Decimal | None, bool]:
    """BDI da linha (já conferido na leitura) ou com ÷ sem − 1, só se plausível (5% a 45%)."""
    if linha.bdi is not None and bdi_plausivel(linha.bdi):
        return linha.bdi, False
    if linha.preco_sem_bdi is not None and linha.preco_sem_bdi > 0:
        razao = (linha.preco_com_bdi / linha.preco_sem_bdi - 1).quantize(Decimal("0.0001"))
        if bdi_plausivel(razao):
            return razao, False
    return None, True


def itens_da_planilha(
    edital: EditalPlanilha,
    sha256: str,
    linhas: Sequence[LinhaPlanilha],
    servicos: tuple[Servico, ...] | None = None,
) -> list[PrecoItemNovo]:
    """Linhas da planilha que casam com um serviço-padrão, prontas para `preco_item`."""
    novos: list[PrecoItemNovo] = []
    chaves: set[str] = set()
    for posicao, linha in enumerate(linhas, start=1):
        contexto = replace(
            contexto_da_obra(linha.secao, edital.objeto), ramal=secao_de_ramal(linha.secao)
        )
        classificacao = classificar(linha.descricao, linha.unidade, servicos, contexto)
        if classificacao is None:
            continue
        base = f"{edital.id_pncp}#{sha256[:12]}#{linha.item or f'pos{posicao}'}"
        chave, n = base, 1
        while chave in chaves:  # item repetido com outra linha (lotes com a mesma numeração)
            n += 1
            chave = f"{base}~{n}"
        chaves.add(chave)
        bdi, desconhecido = _bdi(linha)
        novos.append(
            PrecoItemNovo(
                origem=ORIGEM,
                chave_origem=chave,
                uf=edital.uf,
                municipio=edital.municipio,
                orgao_nome=edital.orgao_nome,
                orgao_cnpj=edital.orgao_cnpj,
                processo=edital.id_pncp,
                data=edital.data,
                data_base=linha.data_base,
                descricao=linha.descricao,
                unidade=linha.unidade,
                quantidade=linha.quantidade,
                preco_sem_bdi=linha.preco_sem_bdi,
                bdi=bdi,
                preco_com_bdi=linha.preco_com_bdi,
                bdi_desconhecido=desconhecido,
                preco_contratado_com_bdi=None,
                fonte_referencia=linha.fonte.upper() if linha.fonte else None,
                codigo_referencia=linha.codigo,
                servico=classificacao.servico,
                material=classificacao.material,
                dn=classificacao.dn,
                escopo=classificacao.escopo,
                faixa=classificacao.faixa,
                unidade_canonica=classificacao.unidade_canonica,
                fator_unidade=classificacao.fator,
                suspeito=False,
                observacao=classificacao.observacao,
            )
        )
    return novos


# --- Estado por edital ---


def _inicio_do_mes(agora: datetime) -> datetime:
    return agora.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _espera_da_falha(tentativas: int) -> timedelta:
    """7, 14, 28, 56 dias: dobra a cada falha seguida (para em 56)."""
    fator: int = 2 ** (min(max(tentativas, 1), 4) - 1)
    return ESPERA_FALHA * fator


def planilhas_a_ignorar(sessao: Session, agora: datetime, ia_ligada: bool = True) -> set[str]:
    """Editais que a rodada não refaz: estado final (`extraida`, `sem_planilha`, `republicada`,
    `ia_invalida`), `falhou` dentro da espera (que dobra), `pulado_teto` neste mês e, com a IA
    desligada, `aguarda_ia`."""
    finais = [*_FINAIS, *(() if ia_ligada else ("aguarda_ia",))]
    ignorar = set(
        sessao.scalars(
            select(PlanilhaEdital.id_pncp).where(
                or_(
                    PlanilhaEdital.status.in_(finais),
                    and_(
                        PlanilhaEdital.status == "pulado_teto",
                        PlanilhaEdital.processada_em >= _inicio_do_mes(agora),
                    ),
                )
            )
        ).all()
    )
    falhas = sessao.execute(
        select(
            PlanilhaEdital.id_pncp, PlanilhaEdital.tentativas, PlanilhaEdital.processada_em
        ).where(PlanilhaEdital.status == "falhou")
    ).all()
    ignorar.update(
        f.id_pncp for f in falhas if f.processada_em > agora - _espera_da_falha(f.tentativas)
    )
    return ignorar


def gasto_planilhas_do_mes(sessao: Session, agora: datetime) -> Decimal:
    """Custo da IA da 7b.2 no mês UTC de `agora`: toda chamada paga, válida ou não."""
    total = sessao.scalar(
        select(func.coalesce(func.sum(GastoPlanilha.custo_usd), 0)).where(
            GastoPlanilha.criado_em >= _inicio_do_mes(agora)
        )
    )
    return Decimal(total or 0)


def registrar_gasto_planilha(sessao: Session, id_pncp: str, custo_usd: Decimal) -> None:
    """Anota uma chamada paga (sem commit). Custo zero (acerto de cache) não é anotado."""
    if custo_usd > 0:
        sessao.add(GastoPlanilha(id_pncp=id_pncp, custo_usd=custo_usd))
        sessao.flush()


def registrar_planilha(
    sessao: Session,
    id_pncp: str,
    status: StatusPlanilha,
    *,
    metodo: MetodoPlanilha | None = None,
    sha256: str | None = None,
    documento: str | None = None,
    orgao_cnpj: str | None = None,
    linhas_lidas: int = 0,
    linhas_gravadas: int = 0,
    custo_usd: Decimal = Decimal(0),
    motivo: str | None = None,
) -> None:
    """Upsert do estado do edital (sem commit). `motivo` só com tipo e contagem, nunca texto."""
    valores: dict[str, Any] = {
        "id_pncp": id_pncp,
        "status": status,
        "metodo": metodo,
        "sha256": sha256,
        "documento": documento,
        "orgao_cnpj": orgao_cnpj,
        "linhas_lidas": linhas_lidas,
        "linhas_gravadas": linhas_gravadas,
        "custo_usd": custo_usd,
        "republicada_em": None,
        "motivo": motivo,
        "tentativas": 1 if status == "falhou" else 0,
        "processada_em": func.now(),
    }
    comando = insert(PlanilhaEdital).values(**valores)
    atualizar: dict[str, Any] = {
        k: comando.excluded[k] for k in valores if k not in ("id_pncp", "tentativas")
    }
    atualizar["tentativas"] = PlanilhaEdital.__table__.c.tentativas + 1 if status == "falhou" else 0
    sessao.execute(
        comando.on_conflict_do_update(index_elements=[PlanilhaEdital.id_pncp], set_=atualizar)
    )


def _assinatura(itens: Collection[PrecoItemNovo]) -> set[_Assinatura]:
    return {(i.codigo_referencia, i.quantidade, i.preco_com_bdi) for i in itens}


def _republicadas(
    sessao: Session, edital: EditalPlanilha, sha256: str, itens: Sequence[PrecoItemNovo]
) -> list[str]:
    """Editais já extraídos que são a mesma planilha deste (ver docstring do módulo)."""
    mesma = set(
        sessao.scalars(
            select(PlanilhaEdital.id_pncp).where(
                PlanilhaEdital.sha256 == sha256,
                PlanilhaEdital.status == "extraida",
                PlanilhaEdital.id_pncp != edital.id_pncp,
            )
        ).all()
    )
    nova = _assinatura(itens)
    if edital.orgao_cnpj and len(nova) >= LINHAS_MINIMAS_REPUBLICACAO:
        do_orgao = sessao.scalars(
            select(PlanilhaEdital.id_pncp).where(
                PlanilhaEdital.orgao_cnpj == edital.orgao_cnpj,
                PlanilhaEdital.status == "extraida",
                PlanilhaEdital.id_pncp != edital.id_pncp,
            )
        ).all()
        for outro in do_orgao:
            if outro in mesma:
                continue
            linhas = sessao.execute(
                select(
                    PrecoItem.codigo_referencia, PrecoItem.quantidade, PrecoItem.preco_com_bdi
                ).where(PrecoItem.origem == ORIGEM, PrecoItem.processo == outro)
            ).all()
            antiga: set[_Assinatura] = {(c, q, p) for c, q, p in linhas}
            # Simétrico: a nova não pode ser só um pedaço da antiga (um lote de várias), nem a
            # antiga um pedaço da nova; senão as linhas do outro edital seriam apagadas.
            maior = max(len(nova), len(antiga))
            if antiga and Decimal(len(nova & antiga)) >= REPUBLICACAO_MINIMA * maior:
                mesma.add(outro)
    return sorted(mesma)


def apagar_planilha(sessao: Session, id_pncp: str) -> int:
    """Apaga as linhas de `preco_item` do edital (sem commit)."""
    resultado = sessao.execute(
        delete(PrecoItem).where(PrecoItem.origem == ORIGEM, PrecoItem.processo == id_pncp)
    )
    return int(getattr(resultado, "rowcount", 0) or 0)


def gravar_planilha(
    sessao: Session,
    edital: EditalPlanilha,
    sha256: str,
    itens: Sequence[PrecoItemNovo],
    *,
    metodo: MetodoPlanilha,
    documento: str,
    linhas_lidas: int,
    custo_usd: Decimal = Decimal(0),
) -> Gravacao:
    """Troca as linhas do edital pelas `itens`, resolve republicação e marca `extraida`.

    Sem commit: quem chama commita (uma transação por edital).
    """
    republicadas = _republicadas(sessao, edital, sha256, itens)
    for anterior in republicadas:
        apagar_planilha(sessao, anterior)
        sessao.execute(
            update(PlanilhaEdital)
            .where(PlanilhaEdital.id_pncp == anterior)
            .values(status="republicada", republicada_em=edital.id_pncp, linhas_gravadas=0)
        )
        logger.info("planilha_republicada", extra={"anterior": anterior, "id_pncp": edital.id_pncp})
    apagar_planilha(sessao, edital.id_pncp)
    gravadas = gravar_precos(sessao, itens)
    registrar_planilha(
        sessao,
        edital.id_pncp,
        "extraida",
        metodo=metodo,
        sha256=sha256,
        documento=documento,
        orgao_cnpj=edital.orgao_cnpj,
        linhas_lidas=linhas_lidas,
        linhas_gravadas=gravadas,
        custo_usd=custo_usd,
    )
    return Gravacao(gravadas, tuple(republicadas))
