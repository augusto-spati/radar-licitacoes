"""preco_item

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-27 21:00:00.000000

Etapa 7b.1: preço unitário por item (estimado e, quando o contrato casa, contratado), já
normalizado num serviço-padrão de `config/servicos.toml`. Origem `tce_pe` agora; `planilha_edital`
na 7b.2. Só itens que casam com um serviço são gravados.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "preco_item",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("origem", sa.Text(), nullable=False),
        sa.Column("chave_origem", sa.Text(), nullable=False),
        sa.Column("uf", sa.Text(), nullable=False),
        sa.Column("municipio", sa.Text(), nullable=True),
        sa.Column("orgao_nome", sa.Text(), nullable=False),
        sa.Column("orgao_cnpj", sa.Text(), nullable=True),
        sa.Column("processo", sa.Text(), nullable=False),
        sa.Column("data", sa.Date(), nullable=True),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column("unidade", sa.Text(), nullable=False),
        sa.Column("quantidade", sa.Numeric(), nullable=True),
        sa.Column("preco_sem_bdi", sa.Numeric(), nullable=True),
        sa.Column("bdi", sa.Numeric(), nullable=True),
        sa.Column("preco_com_bdi", sa.Numeric(), nullable=True),
        sa.Column("bdi_desconhecido", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("preco_contratado_com_bdi", sa.Numeric(), nullable=True),
        sa.Column("fonte_referencia", sa.Text(), nullable=True),
        sa.Column("codigo_referencia", sa.Text(), nullable=True),
        sa.Column("servico", sa.Text(), nullable=False),
        sa.Column("material", sa.Text(), nullable=True),
        sa.Column("dn", sa.Integer(), nullable=True),
        sa.Column("escopo", sa.Text(), nullable=True),
        sa.Column("unidade_canonica", sa.Text(), nullable=False),
        sa.Column("fator_unidade", sa.Numeric(), nullable=False),
        sa.Column("suspeito", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "gravado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("origem", "chave_origem", name="uq_preco_item_origem"),
        sa.CheckConstraint("origem IN ('tce_pe', 'planilha_edital')", name="ck_preco_item_origem"),
        sa.CheckConstraint(
            "escopo IN ('material', 'assentamento', 'completo')", name="ck_preco_item_escopo"
        ),
    )
    op.create_index("ix_preco_item_servico_uf", "preco_item", ["servico", "uf"])


def downgrade() -> None:
    op.drop_index("ix_preco_item_servico_uf", table_name="preco_item")
    op.drop_table("preco_item")
