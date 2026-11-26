from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import CHAR, CheckConstraint, Computed, DateTime, Index, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from radar.comum import Base


class Fonte(StrEnum):
    """Fonte do edital. Vira o enum `Fonte` do OpenAPI (uma união no TypeScript)."""

    pncp = "pncp"
    sanepar = "sanepar"
    compesa = "compesa"


SITUACOES = ("aberta", "em_julgamento", "encerrada", "outra")


def _lista_sql(valores: list[str] | tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


class Edital(Base):
    __tablename__ = "edital"
    __table_args__ = (
        Index("ix_edital_uf", "uf"),
        Index("ix_edital_data_encerramento_proposta", "data_encerramento_proposta"),
        Index("ix_edital_busca", "busca", postgresql_using="gin"),
        Index(
            "ix_edital_situacao_encerramento", "situacao_normalizada", "data_encerramento_proposta"
        ),
        CheckConstraint(
            f"fonte IN ({_lista_sql([f.value for f in Fonte])})", name="ck_edital_fonte"
        ),
        CheckConstraint(
            f"situacao_normalizada IN ({_lista_sql(SITUACOES)})", name="ck_edital_situacao"
        ),
    )

    fonte: Mapped[str] = mapped_column(Text, primary_key=True)
    id_na_fonte: Mapped[str] = mapped_column(Text, primary_key=True)
    objeto: Mapped[str] = mapped_column(Text)
    orgao_cnpj: Mapped[str | None] = mapped_column(Text)
    orgao_nome: Mapped[str] = mapped_column(Text)
    uf: Mapped[str | None] = mapped_column(CHAR(2))
    municipio: Mapped[str | None] = mapped_column(Text)
    # Só o PNCP tem os códigos; as outras fontes trazem só o nome.
    modalidade_id: Mapped[int | None] = mapped_column(Integer)
    modalidade_nome: Mapped[str] = mapped_column(Text)
    situacao_id: Mapped[int | None] = mapped_column(Integer)
    situacao_original: Mapped[str] = mapped_column(Text)
    situacao_normalizada: Mapped[str] = mapped_column(Text)
    valor_estimado: Mapped[Decimal | None] = mapped_column(Numeric)
    data_publicacao: Mapped[datetime | None] = mapped_column(DateTime())
    data_abertura_proposta: Mapped[datetime | None] = mapped_column(DateTime())
    data_encerramento_proposta: Mapped[datetime | None] = mapped_column(DateTime())
    # PNCP: dataAtualizacaoGlobal. Outras fontes: quando o conteúdo mudou no nosso banco.
    data_atualizacao: Mapped[datetime] = mapped_column(DateTime())
    link_origem: Mapped[str | None] = mapped_column(Text)
    visto_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hash_conteudo: Mapped[str | None] = mapped_column(Text)
    bruto: Mapped[dict[str, Any]] = mapped_column(JSONB)
    busca: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('portuguese', radar_unaccent(objeto))", persisted=True)
    )
