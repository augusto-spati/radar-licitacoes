from datetime import datetime
from typing import Any, Literal

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from radar.comum.db import Base

TipoMensagem = Literal["pagina_coletada", "retrato_coletado"]


class Outbox(Base):
    """Mensagens a publicar na fila; nascem na mesma transação da coleta."""

    __tablename__ = "outbox"
    __table_args__ = (
        CheckConstraint("tipo IN ('pagina_coletada', 'retrato_coletado')", name="ck_outbox_tipo"),
        Index("ix_outbox_pendente", "id", postgresql_where=text("publicada_em IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tipo: Mapped[str] = mapped_column(Text)
    carga: Mapped[dict[str, Any]] = mapped_column(JSONB)
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    publicada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tentativas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    erro: Mapped[str | None] = mapped_column(Text)


def enfileirar(sessao: Session, tipo: TipoMensagem, carga: dict[str, int]) -> None:
    """Adiciona a mensagem à sessão. Não faz commit: quem coleta decide quando."""
    sessao.add(Outbox(tipo=tipo, carga=carga))
