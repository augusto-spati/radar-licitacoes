"""Tabela `preco_item`: preço unitário por item, já normalizado num serviço-padrão."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, get_args

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from radar.comum import Base
from radar.precos.servicos import ESCOPOS, FAIXAS

# `pncp_planilha`: preço estimado da planilha orçamentária de um edital do PNCP (7b.2).
Origem = Literal["tce_pe", "pncp_planilha"]
ORIGENS: tuple[str, ...] = get_args(Origem)


class PrecoItem(Base):
    """Um item de orçamento (estimado) e, quando o contrato casa, o preço contratado.

    Preços são sempre por unidade original (`unidade`); para comparar, divide-se por
    `fator_unidade` (quantidade na canônica = quantidade × fator).
    """

    __tablename__ = "preco_item"
    __table_args__ = (
        UniqueConstraint("origem", "chave_origem", name="uq_preco_item_origem"),
        CheckConstraint(
            "origem IN (" + ", ".join(f"'{o}'" for o in ORIGENS) + ")",
            name="ck_preco_item_origem",
        ),
        CheckConstraint(
            "escopo IN (" + ", ".join(f"'{e}'" for e in ESCOPOS) + ")",
            name="ck_preco_item_escopo",
        ),
        CheckConstraint(
            "faixa IN (" + ", ".join(f"'{f}'" for f in FAIXAS) + ")",
            name="ck_preco_item_faixa",
        ),
        Index("ix_preco_item_servico_uf", "servico", "uf"),
        Index("ix_preco_item_origem_processo", "origem", "processo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    origem: Mapped[str] = mapped_column(Text)
    chave_origem: Mapped[str] = mapped_column(Text)
    uf: Mapped[str] = mapped_column(Text)
    municipio: Mapped[str | None] = mapped_column(Text)
    orgao_nome: Mapped[str] = mapped_column(Text)
    orgao_cnpj: Mapped[str | None] = mapped_column(Text)
    processo: Mapped[str] = mapped_column(Text)
    # Data do processo (homologação); `data_base` é a data-base do preço (no TCE-PE, o
    # `DataReferencia` do item), que pode ser bem anterior.
    data: Mapped[date | None] = mapped_column(Date)
    data_base: Mapped[date | None] = mapped_column(Date)
    descricao: Mapped[str] = mapped_column(Text)
    unidade: Mapped[str] = mapped_column(Text)
    quantidade: Mapped[Decimal | None] = mapped_column(Numeric)
    preco_sem_bdi: Mapped[Decimal | None] = mapped_column(Numeric)
    # BDI como fração (0.2467 = 24,67%). A fonte manda em escalas diferentes ("0.2467" na
    # Compesa, "20.9700" em Pedra): valor < 1 já é fração, valor >= 1 é dividido por 100. A
    # normalização é feita na gravação (Task 3), não aqui.
    bdi: Mapped[Decimal | None] = mapped_column(Numeric)
    # No TCE-PE o `ValorUnitario` do orçamento já inclui o BDI (amostra de Pedra:
    # Σ ValorTotal = valorGlobal do processo), então preco_com_bdi = ValorUnitario.
    preco_com_bdi: Mapped[Decimal | None] = mapped_column(Numeric)
    bdi_desconhecido: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    preco_contratado_com_bdi: Mapped[Decimal | None] = mapped_column(Numeric)
    fonte_referencia: Mapped[str | None] = mapped_column(Text)
    codigo_referencia: Mapped[str | None] = mapped_column(Text)
    servico: Mapped[str] = mapped_column(Text)
    material: Mapped[str | None] = mapped_column(Text)
    # Em PEAD pode ser o diâmetro externo (DE); só se compara dentro do mesmo material.
    dn: Mapped[int | None] = mapped_column(Integer)
    # Só em rede: material, assentamento ou completo (ver servicos.escopo_de_rede).
    escopo: Mapped[str | None] = mapped_column(Text)
    # Só em escavação: faixa de profundidade (ate_1_5m, 1_5_a_3m, 3_a_6m).
    faixa: Mapped[str | None] = mapped_column(Text)
    # Marca que não muda a chave de comparação: "adutora" na rede de água de adutora (7b.2).
    observacao: Mapped[str | None] = mapped_column(Text)
    unidade_canonica: Mapped[str] = mapped_column(Text)
    fator_unidade: Mapped[Decimal] = mapped_column(Numeric)
    suspeito: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Valor homologado do processo na gravação (TCE-PE): se não mudou e o contratado já está
    # gravado, a rodada semanal não busca os contratos de novo.
    valor_homologado: Mapped[Decimal | None] = mapped_column(Numeric)
    gravado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


@dataclass(frozen=True)
class PrecoItemNovo:
    """Linha a gravar em `preco_item` (sem `id` e `gravado_em`), vinda de qualquer origem."""

    origem: Origem
    chave_origem: str
    uf: str
    municipio: str | None
    orgao_nome: str
    orgao_cnpj: str | None
    processo: str
    data: date | None
    data_base: date | None
    descricao: str
    unidade: str
    quantidade: Decimal | None
    preco_sem_bdi: Decimal | None
    bdi: Decimal | None
    preco_com_bdi: Decimal | None
    bdi_desconhecido: bool
    preco_contratado_com_bdi: Decimal | None
    fonte_referencia: str | None
    codigo_referencia: str | None
    servico: str
    material: str | None
    dn: int | None
    escopo: str | None
    faixa: str | None
    unidade_canonica: str
    fator_unidade: Decimal
    suspeito: bool
    valor_homologado: Decimal | None = None
    observacao: str | None = None


StatusPlanilha = Literal[
    "extraida", "sem_planilha", "pulado_teto", "falhou", "republicada", "ia_invalida", "aguarda_ia"
]
STATUS_PLANILHA: tuple[str, ...] = get_args(StatusPlanilha)
MetodoPlanilha = Literal["deterministico", "ia"]
METODOS_PLANILHA: tuple[str, ...] = get_args(MetodoPlanilha)


class PlanilhaEdital(Base):
    """Estado da planilha orçamentária de um edital do PNCP (7b.2), um por edital.

    `extraida`: linhas lidas (determinístico ou IA) e as com serviço gravadas em `preco_item`;
    `sem_planilha`: nada achado (não reprocessa); `pulado_teto`: a IA passaria do teto (tenta
    de novo no mês seguinte); `falhou`: erro (tenta de novo com espera que dobra a cada
    tentativa: 7, 14, 28, 56 dias); `republicada`: a mesma planilha saiu num edital mais novo
    (`republicada_em`), onde as linhas ficaram; `ia_invalida`: a IA foi paga e a resposta não
    serviu (inválida ou cortada em `max_tokens`): não compra de novo (só com `--refazer` ou
    `--edital`); `aguarda_ia`: pede IA e ela está desligada (volta quando a IA for ligada).
    """

    __tablename__ = "planilha_edital"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in STATUS_PLANILHA) + ")",
            name="ck_planilha_edital_status",
        ),
        CheckConstraint(
            "metodo IN (" + ", ".join(f"'{m}'" for m in METODOS_PLANILHA) + ")",
            name="ck_planilha_edital_metodo",
        ),
        Index("ix_planilha_edital_sha256", "sha256"),
    )

    # `id_na_fonte` do edital do PNCP ("<cnpj>-1-<sequencial>/<ano>").
    id_pncp: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text)
    metodo: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(Text)
    # Nome do arquivo (ou "edital: <nome>, p. 39-42") de onde as linhas vieram.
    documento: Mapped[str | None] = mapped_column(Text)
    orgao_cnpj: Mapped[str | None] = mapped_column(Text)
    linhas_lidas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    linhas_gravadas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    custo_usd: Mapped[Decimal] = mapped_column(Numeric, default=Decimal(0), server_default="0")
    republicada_em: Mapped[str | None] = mapped_column(Text)
    motivo: Mapped[str | None] = mapped_column(Text)
    # Falhas seguidas (zera em qualquer outro estado): dobra a espera de `falhou`.
    tentativas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    processada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class GastoPlanilha(Base):
    """Cada chamada paga da IA da 7b.2 (válida, inválida ou de um edital que falhou depois).

    O teto mensal da 7b.2 soma esta tabela, não `planilha_edital.custo_usd` (que é
    sobrescrito quando o edital é refeito). Acerto de cache não paga e não entra.
    """

    __tablename__ = "gasto_planilha"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_pncp: Mapped[str] = mapped_column(Text)
    custo_usd: Mapped[Decimal] = mapped_column(Numeric)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
