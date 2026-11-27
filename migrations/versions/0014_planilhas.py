"""planilha_edital e origem pncp_planilha em preco_item

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-27 23:30:00.000000

Etapa 7b.2: preço estimado das planilhas orçamentárias dos editais do PNCP. A origem reservada
`planilha_edital` (nunca gravada) vira `pncp_planilha`; `preco_item.observacao` marca a rede de
água de adutora ("adutora"); `planilha_edital` guarda o estado de cada edital (para não
reprocessar), o sha256 da planilha (dedupe de republicação) e o custo da IA.

Numeração: 0013 é de outro ramo (resultado_externo). Este ramo parte de 0012; no rebase depois
do 0013, o down_revision passa a "0013".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_preco_item_origem", "preco_item", type_="check")
    op.create_check_constraint(
        "ck_preco_item_origem", "preco_item", "origem IN ('tce_pe', 'pncp_planilha')"
    )
    op.add_column("preco_item", sa.Column("observacao", sa.Text(), nullable=True))
    op.create_index("ix_preco_item_origem_processo", "preco_item", ["origem", "processo"])
    op.create_table(
        "planilha_edital",
        sa.Column("id_pncp", sa.Text(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("metodo", sa.Text(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("documento", sa.Text(), nullable=True),
        sa.Column("orgao_cnpj", sa.Text(), nullable=True),
        sa.Column("linhas_lidas", sa.Integer(), server_default="0", nullable=False),
        sa.Column("linhas_gravadas", sa.Integer(), server_default="0", nullable=False),
        sa.Column("custo_usd", sa.Numeric(), server_default="0", nullable=False),
        sa.Column("republicada_em", sa.Text(), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column(
            "processada_em",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('extraida', 'sem_planilha', 'pulado_teto', 'falhou', 'republicada')",
            name="ck_planilha_edital_status",
        ),
        sa.CheckConstraint("metodo IN ('deterministico', 'ia')", name="ck_planilha_edital_metodo"),
    )
    op.create_index("ix_planilha_edital_sha256", "planilha_edital", ["sha256"])


def downgrade() -> None:
    op.drop_index("ix_planilha_edital_sha256", table_name="planilha_edital")
    op.drop_table("planilha_edital")
    op.drop_index("ix_preco_item_origem_processo", table_name="preco_item")
    op.drop_column("preco_item", "observacao")
    # O CHECK antigo não aceita a origem nova: as linhas dela são apagadas.
    op.execute("DELETE FROM preco_item WHERE origem = 'pncp_planilha'")
    op.drop_constraint("ck_preco_item_origem", "preco_item", type_="check")
    op.create_check_constraint(
        "ck_preco_item_origem", "preco_item", "origem IN ('tce_pe', 'planilha_edital')"
    )
