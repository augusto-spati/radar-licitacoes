import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    # Checkpoint: a próxima página a pedir, avançada no mesmo commit de cada página.
    proxima_pagina: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # totalPaginas e totalRegistros da página 1, para a completude valer entre fatias.
    total_paginas: Mapped[int | None] = mapped_column(Integer)
    esperados: Mapped[int | None] = mapped_column(Integer)
    # Número da fatia: o fencing token da mensagem coletar_janela.
    lote: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Lease de quem coleta agora (o status em_andamento só diz "começada e não terminada").
    travada_por: Mapped[str | None] = mapped_column(Text)
    travada_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ColetaRetrato(Base):
    """Uma leitura completa da lista de uma fonte de retrato (Sanepar, Compesa)."""

    __tablename__ = "coleta_retrato"
    __table_args__ = (
        CheckConstraint(
            "status IN ('em_andamento', 'concluido', 'falha')", name="ck_coleta_retrato_status"
        ),
        Index("ix_coleta_retrato_fonte_iniciado", "fonte", "iniciado_em"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fonte: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)  # em_andamento | concluido | falha
    iniciado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paginas: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    registros: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    erro: Mapped[str | None] = mapped_column(Text)
    processado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaginaBruta(Base):
    """Página bruta de uma janela do PNCP (JSON) ou de um retrato (HTML, JS ou JSON)."""

    __tablename__ = "pagina_bruta"
    __table_args__ = (
        UniqueConstraint("janela_id", "numero_pagina", name="uq_pagina_bruta_janela_numero"),
        UniqueConstraint("retrato_id", "numero_pagina", name="uq_pagina_bruta_retrato_numero"),
        CheckConstraint(
            "(janela_id IS NULL) <> (retrato_id IS NULL)", name="ck_pagina_bruta_origem"
        ),
        CheckConstraint("formato IN ('json', 'html', 'js')", name="ck_pagina_bruta_formato"),
        CheckConstraint(
            "janela_id IS NULL OR formato = 'json'", name="ck_pagina_bruta_janela_json"
        ),
        CheckConstraint(
            "(formato = 'json' AND conteudo IS NOT NULL)"
            " OR (formato IN ('html', 'js') AND conteudo_texto IS NOT NULL)",
            name="ck_pagina_bruta_conteudo",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    janela_id: Mapped[int | None] = mapped_column(ForeignKey("coleta_janela.id"))
    retrato_id: Mapped[int | None] = mapped_column(ForeignKey("coleta_retrato.id"))
    numero_pagina: Mapped[int] = mapped_column(Integer)
    formato: Mapped[str] = mapped_column(Text, default="json", server_default="json")
    conteudo: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    conteudo_texto: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    coletada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TravaFonte(Base):
    """Lease que garante um único trabalho por fonte de cada vez (veja `coleta.trava`)."""

    __tablename__ = "trava_fonte"

    fonte: Mapped[str] = mapped_column(Text, primary_key=True)
    dono: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    obtida_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
