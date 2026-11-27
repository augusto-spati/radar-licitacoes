"""documento_edital, analise_edital e resposta_llm

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-27 09:00:00.000000

Tabelas da análise dos editais com IA (etapa 6). Sem chave estrangeira para `edital`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documento_edital",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("id_na_fonte", sa.Text(), nullable=False),
        sa.Column("sequencial", sa.Integer(), nullable=False),
        sa.Column("titulo", sa.Text(), nullable=False),
        sa.Column("tipo_nome", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("tamanho", sa.BigInteger(), nullable=True),
        sa.Column("paginas", sa.Integer(), nullable=True),
        sa.Column("paginas_sem_texto", sa.Integer(), nullable=True),
        sa.Column("pagina_inicial", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("baixado_em", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fonte", "id_na_fonte", "sequencial", name="uq_documento_edital"),
        sa.CheckConstraint(
            "status IN ('extraido', 'sem_texto', 'nao_pdf', 'grande_demais', 'falha')",
            name="ck_documento_edital_status",
        ),
    )
    op.create_table(
        "analise_edital",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("id_na_fonte", sa.Text(), nullable=False),
        sa.Column("modelo", sa.Text(), nullable=False),
        sa.Column("versao_prompt", sa.Text(), nullable=False),
        sa.Column("hash_documentos", sa.Text(), nullable=False),
        sa.Column("veredito", sa.Text(), nullable=False),
        sa.Column("incompleta", sa.Boolean(), nullable=False),
        sa.Column("exigencias", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("comparacao", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trechos", sa.Integer(), nullable=False),
        sa.Column("trechos_invalidos", sa.Integer(), nullable=False),
        sa.Column("achados_descartados", sa.Integer(), nullable=False),
        sa.Column("truncado", sa.Boolean(), nullable=False),
        sa.Column("tokens_entrada", sa.Integer(), nullable=False),
        sa.Column("tokens_saida", sa.Integer(), nullable=False),
        sa.Column("custo_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column(
            "criada_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "fonte", "id_na_fonte", "modelo", "versao_prompt", name="uq_analise_edital"
        ),
        sa.CheckConstraint(
            "veredito IN ('apto', 'inapto', 'verificar')", name="ck_analise_veredito"
        ),
    )
    op.create_table(
        "resposta_llm",
        sa.Column("chave", sa.Text(), nullable=False),
        sa.Column("modelo", sa.Text(), nullable=False),
        sa.Column("resposta", sa.Text(), nullable=False),
        sa.Column("tokens_entrada", sa.Integer(), nullable=False),
        sa.Column("tokens_saida", sa.Integer(), nullable=False),
        sa.Column("custo_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("latencia_ms", sa.Integer(), nullable=False),
        sa.Column(
            "criada_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("chave"),
    )
    op.create_index("ix_resposta_llm_criada_em", "resposta_llm", ["criada_em"])


def downgrade() -> None:
    op.drop_index("ix_resposta_llm_criada_em", table_name="resposta_llm")
    op.drop_table("resposta_llm")
    op.drop_table("analise_edital")
    op.drop_table("documento_edital")
