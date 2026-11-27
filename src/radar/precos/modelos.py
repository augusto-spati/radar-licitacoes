"""Tabela `preco_item`: preço unitário por item, já normalizado num serviço-padrão."""

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
    bdi: Mapped[Decimal | None] = mapped_column(Numeric)
    preco_com_bdi: Mapped[Decimal | None] = mapped_column(Numeric)
    bdi_desconhecido: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    preco_contratado_com_bdi: Mapped[Decimal | None] = mapped_column(Numeric)
    fonte_referencia: Mapped[str | None] = mapped_column(Text)
    codigo_referencia: Mapped[str | None] = mapped_column(Text)
    servico: Mapped[str] = mapped_column(Text)
    material: Mapped[str | None] = mapped_column(Text)
    dn: Mapped[int | None] = mapped_column(Integer)
    # Só em rede: material, assentamento ou completo (ver servicos.escopo_de_rede).
    escopo: Mapped[str | None] = mapped_column(Text)
    unidade_canonica: Mapped[str] = mapped_column(Text)
    fator_unidade: Mapped[Decimal] = mapped_column(Numeric)
    suspeito: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    gravado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
