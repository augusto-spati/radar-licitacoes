from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CHAR, Computed, DateTime, Index, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from radar.comum import Base


class Edital(Base):
    __tablename__ = "edital"
    __table_args__ = (
        Index("ix_edital_uf", "uf"),
        Index("ix_edital_data_encerramento_proposta", "data_encerramento_proposta"),
        Index("ix_edital_busca", "busca", postgresql_using="gin"),
    )

    numero_controle_pncp: Mapped[str] = mapped_column(Text, primary_key=True)
    objeto: Mapped[str] = mapped_column(Text)
    orgao_cnpj: Mapped[str] = mapped_column(Text)
    orgao_nome: Mapped[str] = mapped_column(Text)
    uf: Mapped[str | None] = mapped_column(CHAR(2))
    municipio: Mapped[str | None] = mapped_column(Text)
    modalidade_id: Mapped[int] = mapped_column(Integer)
    situacao_id: Mapped[int] = mapped_column(Integer)
    situacao_nome: Mapped[str] = mapped_column(Text)
    valor_estimado: Mapped[Decimal | None] = mapped_column(Numeric)
    data_publicacao: Mapped[datetime] = mapped_column(DateTime())
    data_abertura_proposta: Mapped[datetime | None] = mapped_column(DateTime())
    data_encerramento_proposta: Mapped[datetime | None] = mapped_column(DateTime())
    data_atualizacao: Mapped[datetime] = mapped_column(DateTime())
    link_origem: Mapped[str | None] = mapped_column(Text)
    bruto: Mapped[dict[str, Any]] = mapped_column(JSONB)
    busca: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('portuguese', radar_unaccent(objeto))", persisted=True)
    )
