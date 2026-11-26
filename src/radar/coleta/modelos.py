from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from radar.comum import Base


class ColetaJanela(Base):
    __tablename__ = "coleta_janela"
    __table_args__ = (
        UniqueConstraint(
            "endpoint", "data_ini", "data_fim", "modalidade", name="uq_coleta_janela_chave"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text)  # publicacao | atualizacao
    data_ini: Mapped[date] = mapped_column(Date)
    data_fim: Mapped[date] = mapped_column(Date)
    modalidade: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)  # pendente | em_andamento | concluida | falha
    tentativas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    erro: Mapped[str | None] = mapped_column(Text)
    paginas: Mapped[int] = mapped_column(Integer)
    registros: Mapped[int] = mapped_column(Integer)
    iniciada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    concluida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaginaBruta(Base):
    __tablename__ = "pagina_bruta"
    __table_args__ = (
        UniqueConstraint("janela_id", "numero_pagina", name="uq_pagina_bruta_janela_numero"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    janela_id: Mapped[int] = mapped_column(ForeignKey("coleta_janela.id"))
    numero_pagina: Mapped[int] = mapped_column(Integer)
    conteudo: Mapped[dict[str, Any]] = mapped_column(JSONB)
    coletada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
