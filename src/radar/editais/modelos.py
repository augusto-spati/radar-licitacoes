from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, get_args

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from radar.comum import Base


class Fonte(StrEnum):
    """Fonte do edital. Vira o enum `Fonte` do OpenAPI (uma união no TypeScript)."""

    pncp = "pncp"
    sanepar = "sanepar"
    compesa = "compesa"
    dae_jundiai = "dae_jundiai"


Situacao = Literal["aberta", "em_julgamento", "encerrada", "outra"]
SITUACOES = get_args(Situacao)


def _lista_sql(valores: list[str] | tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


class Edital(Base):
    __tablename__ = "edital"
    __table_args__ = (
        Index("ix_edital_uf", "uf"),
        Index("ix_edital_orgao_cnpj", "orgao_cnpj"),
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


SituacaoResultado = Literal[
    "homologada", "fracassada", "deserta", "parcial", "em_andamento", "sem_itens", "anulada"
]
SITUACOES_RESULTADO = get_args(SituacaoResultado)


class ResultadoItem(Base):
    """Um item da contratação com o resultado (vencedor e valor homologado), se houver."""

    __tablename__ = "resultado_item"
    __table_args__ = (
        UniqueConstraint("fonte", "id_na_fonte", "numero_item", name="uq_resultado_item"),
        ForeignKeyConstraint(
            ["fonte", "id_na_fonte"],
            ["edital.fonte", "edital.id_na_fonte"],
            name="fk_resultado_item_edital",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fonte: Mapped[str] = mapped_column(Text)
    id_na_fonte: Mapped[str] = mapped_column(Text)
    numero_item: Mapped[int] = mapped_column(Integer)
    descricao: Mapped[str] = mapped_column(Text)
    unidade: Mapped[str | None] = mapped_column(Text)
    quantidade: Mapped[Decimal | None] = mapped_column(Numeric)
    valor_unitario_estimado: Mapped[Decimal | None] = mapped_column(Numeric)
    valor_total_estimado: Mapped[Decimal | None] = mapped_column(Numeric)
    orcamento_sigiloso: Mapped[bool] = mapped_column(Boolean)
    situacao: Mapped[str] = mapped_column(Text)
    fornecedor_ni: Mapped[str | None] = mapped_column(Text)
    fornecedor_nome: Mapped[str | None] = mapped_column(Text)
    fornecedor_porte: Mapped[str | None] = mapped_column(Text)
    quantidade_homologada: Mapped[Decimal | None] = mapped_column(Numeric)
    valor_unitario_homologado: Mapped[Decimal | None] = mapped_column(Numeric)
    valor_total_homologado: Mapped[Decimal | None] = mapped_column(Numeric)
    data_resultado: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResultadoBusca(Base):
    """Resumo por contratação da última busca de resultados."""

    __tablename__ = "resultado_busca"
    __table_args__ = (
        ForeignKeyConstraint(
            ["fonte", "id_na_fonte"],
            ["edital.fonte", "edital.id_na_fonte"],
            name="fk_resultado_busca_edital",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"situacao IN ({_lista_sql(SITUACOES_RESULTADO)})",
            name="ck_resultado_busca_situacao",
        ),
        Index("ix_resultado_busca_situacao", "situacao"),
    )

    fonte: Mapped[str] = mapped_column(Text, primary_key=True)
    id_na_fonte: Mapped[str] = mapped_column(Text, primary_key=True)
    buscado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    itens: Mapped[int] = mapped_column(Integer)
    situacao: Mapped[str] = mapped_column(Text)
    desconto: Mapped[Decimal | None] = mapped_column(Numeric)
    valor_estimado_considerado: Mapped[Decimal | None] = mapped_column(Numeric)
    valor_homologado_considerado: Mapped[Decimal | None] = mapped_column(Numeric)
    vencedor_ni: Mapped[str | None] = mapped_column(Text)
    vencedor_nome: Mapped[str | None] = mapped_column(Text)
    vencedor_porte: Mapped[str | None] = mapped_column(Text)


FontesExternas = Literal["tce_pe"]
FONTES_EXTERNAS = get_args(FontesExternas)


class ResultadoExterno(Base):
    """Contratação de fora do PNCP que entra no histórico do órgão (etapa 7a.3: TCE-PE).

    Não é edital do radar (não tem prazo nem aparece na lista): é um caso já resolvido, com o
    resumo no formato de `resultado_busca`. `linhas` vem do `config/filtro.toml` aplicado ao
    objeto na gravação (o objeto do TCE-PE tem U+FFFD no lugar do acento, que o tsvector não
    casaria). `numero` ("213/2026") liga o caso ao edital do portal do órgão, para a régua do
    próprio edital não se medir.
    """

    __tablename__ = "resultado_externo"
    __table_args__ = (
        CheckConstraint(
            f"fonte IN ({_lista_sql(FONTES_EXTERNAS)})", name="ck_resultado_externo_fonte"
        ),
        CheckConstraint(
            f"situacao IN ({_lista_sql(SITUACOES_RESULTADO)})",
            name="ck_resultado_externo_situacao",
        ),
        Index("ix_resultado_externo_orgao_cnpj", "orgao_cnpj"),
    )

    fonte: Mapped[str] = mapped_column(Text, primary_key=True)
    id_na_fonte: Mapped[str] = mapped_column(Text, primary_key=True)
    orgao_cnpj: Mapped[str] = mapped_column(Text)
    orgao_nome: Mapped[str] = mapped_column(Text)
    uf: Mapped[str] = mapped_column(CHAR(2))
    objeto: Mapped[str] = mapped_column(Text)
    modalidade_nome: Mapped[str | None] = mapped_column(Text)
    numero: Mapped[str | None] = mapped_column(Text)
    linhas: Mapped[list[str]] = mapped_column(ARRAY(Text))
    # Sessão de abertura (hora de Brasília sem fuso, como `edital.data_encerramento_proposta`).
    data: Mapped[datetime] = mapped_column(DateTime())
    data_resultado: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    situacao: Mapped[str] = mapped_column(Text)
    valor_estimado: Mapped[Decimal | None] = mapped_column(Numeric)
    valor_homologado: Mapped[Decimal | None] = mapped_column(Numeric)
    desconto: Mapped[Decimal | None] = mapped_column(Numeric)
    vencedor_ni: Mapped[str | None] = mapped_column(Text)
    vencedor_nome: Mapped[str | None] = mapped_column(Text)
    vencedor_porte: Mapped[str | None] = mapped_column(Text)
    gravado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
