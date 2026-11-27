"""resultado_externo

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-28 01:00:00.000000

Etapa 7a.3: contratações de fora do PNCP no histórico do órgão. Agora só os processos da Compesa
no TCE-PE (ela não publica no PNCP), um caso por `processoContratacaoId`, com o resumo no
formato de `resultado_busca`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resultado_externo",
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("id_na_fonte", sa.Text(), nullable=False),
        sa.Column("orgao_cnpj", sa.Text(), nullable=False),
        sa.Column("orgao_nome", sa.Text(), nullable=False),
        sa.Column("uf", sa.CHAR(2), nullable=False),
        sa.Column("objeto", sa.Text(), nullable=False),
        sa.Column("modalidade_nome", sa.Text(), nullable=True),
        sa.Column("numero", sa.Text(), nullable=True),
        sa.Column("linhas", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("data", sa.DateTime(), nullable=False),
        sa.Column("data_resultado", sa.DateTime(timezone=True), nullable=True),
        sa.Column("situacao", sa.Text(), nullable=False),
        sa.Column("valor_estimado", sa.Numeric(), nullable=True),
        sa.Column("valor_homologado", sa.Numeric(), nullable=True),
        sa.Column("desconto", sa.Numeric(), nullable=True),
        sa.Column("vencedor_ni", sa.Text(), nullable=True),
        sa.Column("vencedor_nome", sa.Text(), nullable=True),
        sa.Column("vencedor_porte", sa.Text(), nullable=True),
        sa.Column(
            "gravado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("fonte", "id_na_fonte", name="pk_resultado_externo"),
        sa.CheckConstraint("fonte IN ('tce_pe')", name="ck_resultado_externo_fonte"),
        sa.CheckConstraint(
            "situacao IN ('homologada', 'fracassada', 'deserta', 'parcial', 'em_andamento',"
            " 'sem_itens', 'anulada')",
            name="ck_resultado_externo_situacao",
        ),
    )
    op.create_index("ix_resultado_externo_orgao_cnpj", "resultado_externo", ["orgao_cnpj"])


def downgrade() -> None:
    op.drop_index("ix_resultado_externo_orgao_cnpj", table_name="resultado_externo")
    op.drop_table("resultado_externo")
