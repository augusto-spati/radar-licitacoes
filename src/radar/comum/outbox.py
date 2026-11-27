from datetime import datetime
from typing import Any, Literal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from radar.comum.db import Base
from radar.comum.telemetria import traceparent_atual

TipoMensagem = Literal["pagina_coletada", "retrato_coletado", "coletar_janela"]


class Outbox(Base):
    """Mensagens a publicar na fila; nascem na mesma transação da coleta."""

    __tablename__ = "outbox"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('pagina_coletada', 'retrato_coletado', 'coletar_janela')",
            name="ck_outbox_tipo",
        ),
        Index("ix_outbox_pendente", "id", postgresql_where=text("publicada_em IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tipo: Mapped[str] = mapped_column(Text)
    carga: Mapped[dict[str, Any]] = mapped_column(JSONB)
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    publicada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tentativas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    erro: Mapped[str | None] = mapped_column(Text)
    # W3C do span que gravou a linha; viaja no atributo SQS e vira link no consumidor.
    traceparent: Mapped[str | None] = mapped_column(Text)


def enfileirar(sessao: Session, tipo: TipoMensagem, carga: dict[str, int]) -> Outbox:
    """Adiciona a mensagem à sessão e a devolve. Não faz commit: quem coleta decide quando.

    A linha guarda o `traceparent` do span corrente (se houver): o contexto nasce com a
    mensagem, na mesma transação, e sobrevive a um processo morto antes do relay.
    """
    linha = Outbox(tipo=tipo, carga=carga, traceparent=traceparent_atual())
    sessao.add(linha)
    return linha


def pendencias_outbox(sessao: Session) -> tuple[int, datetime | None]:
    """Quantas mensagens ainda não foram publicadas e quando nasceu a mais velha."""
    quantas, mais_velha = sessao.execute(
        select(func.count(), func.min(Outbox.criada_em)).where(Outbox.publicada_em.is_(None))
    ).one()
    return int(quantas), mais_velha
