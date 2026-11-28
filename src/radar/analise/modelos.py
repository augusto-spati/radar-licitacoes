"""Tabelas da análise. Sem chave estrangeira para `edital`: `analise` não importa `editais`."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, get_args

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import false as sa_false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from radar.comum import Base

StatusDocumento = Literal["extraido", "sem_texto", "nao_pdf", "grande_demais", "falha"]
STATUS_DOCUMENTO: tuple[str, ...] = get_args(StatusDocumento)
Veredito = Literal["apto", "inapto", "verificar"]
VEREDITOS: tuple[str, ...] = get_args(Veredito)


def _lista_sql(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


class DocumentoEdital(Base):
    """Metadados de um documento baixado; o PDF e o texto ficam no armazém (`sha256`)."""

    __tablename__ = "documento_edital"
    __table_args__ = (
        UniqueConstraint("fonte", "id_na_fonte", "sequencial", name="uq_documento_edital"),
        CheckConstraint(
            f"status IN ({_lista_sql(STATUS_DOCUMENTO)})", name="ck_documento_edital_status"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fonte: Mapped[str] = mapped_column(Text)
    id_na_fonte: Mapped[str] = mapped_column(Text)
    sequencial: Mapped[int] = mapped_column(Integer)
    titulo: Mapped[str] = mapped_column(Text)
    tipo_nome: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(Text)
    tamanho: Mapped[int | None] = mapped_column(BigInteger)
    paginas: Mapped[int | None] = mapped_column(Integer)
    paginas_sem_texto: Mapped[int | None] = mapped_column(Integer)
    # Onde o documento começa no texto juntado dos documentos do edital (as páginas das
    # análises são desse texto); nulo se o documento não entrou no texto (sem texto, zip...).
    pagina_inicial: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    erro: Mapped[str | None] = mapped_column(Text)
    baixado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AnaliseEdital(Base):
    __tablename__ = "analise_edital"
    __table_args__ = (
        UniqueConstraint(
            "fonte", "id_na_fonte", "modelo", "versao_prompt", name="uq_analise_edital"
        ),
        # Ascendente: o btree é varrido de trás para frente e serve o ORDER BY ... DESC.
        Index("ix_analise_edital_ultima", "fonte", "id_na_fonte", "modelo", "criada_em", "id"),
        CheckConstraint(f"veredito IN ({_lista_sql(VEREDITOS)})", name="ck_analise_veredito"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fonte: Mapped[str] = mapped_column(Text)
    id_na_fonte: Mapped[str] = mapped_column(Text)
    modelo: Mapped[str] = mapped_column(Text)
    versao_prompt: Mapped[str] = mapped_column(Text)
    hash_documentos: Mapped[str] = mapped_column(Text)
    veredito: Mapped[str] = mapped_column(Text)
    incompleta: Mapped[bool] = mapped_column(Boolean)
    exigencias: Mapped[dict[str, Any]] = mapped_column(JSONB)
    comparacao: Mapped[dict[str, Any]] = mapped_column(JSONB)
    trechos: Mapped[int] = mapped_column(Integer)
    trechos_invalidos: Mapped[int] = mapped_column(Integer)
    achados_descartados: Mapped[int] = mapped_column(Integer)
    truncado: Mapped[bool] = mapped_column(Boolean)
    tokens_entrada: Mapped[int] = mapped_column(Integer)
    tokens_saida: Mapped[int] = mapped_column(Integer)
    custo_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PerfilResumo(Base):
    """Resumo não sensível do perfil, numa linha só (o painel lê; a análise grava)."""

    __tablename__ = "perfil_resumo"
    __table_args__ = (CheckConstraint("id = 1", name="ck_perfil_resumo_uma_linha"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1, autoincrement=False)
    exemplo: Mapped[bool] = mapped_column(Boolean)
    uf_sede: Mapped[str | None] = mapped_column(CHAR(2))
    valor_maximo_obra: Mapped[Decimal | None] = mapped_column(Numeric)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RespostaLLM(Base):
    """Cache das respostas do provedor (e base do gasto do mês)."""

    __tablename__ = "resposta_llm"
    __table_args__ = (Index("ix_resposta_llm_criada_em", "criada_em"),)

    chave: Mapped[str] = mapped_column(Text, primary_key=True)
    modelo: Mapped[str] = mapped_column(Text)
    resposta: Mapped[str] = mapped_column(Text)
    tokens_entrada: Mapped[int] = mapped_column(Integer)
    tokens_saida: Mapped[int] = mapped_column(Integer)
    custo_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    latencia_ms: Mapped[int] = mapped_column(Integer)
    # Resposta cobrada mas inutilizável (ErroProvedor): só conta no gasto, nunca é reusada.
    invalida: Mapped[bool] = mapped_column(Boolean, server_default=sa_false())
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
