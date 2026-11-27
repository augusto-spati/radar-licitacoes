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
from radar.precos.servicos import ESCOPOS

Origem = Literal["tce_pe", "planilha_edital"]
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
        Index("ix_preco_item_servico_uf", "servico", "uf"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    origem: Mapped[str] = mapped_column(Text)
    chave_origem: Mapped[str] = mapped_column(Text)
    uf: Mapped[str] = mapped_column(Text)
    municipio: Mapped[str | None] = mapped_column(Text)
    orgao_nome: Mapped[str] = mapped_column(Text)
    orgao_cnpj: Mapped[str | None] = mapped_column(Text)
    processo: Mapped[str] = mapped_column(Text)
    data: Mapped[date | None] = mapped_column(Date)
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
    unidade_canonica: Mapped[str] = mapped_column(Text)
    fator_unidade: Mapped[Decimal] = mapped_column(Numeric)
    suspeito: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
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
    unidade_canonica: str
    fator_unidade: Decimal
    suspeito: bool
