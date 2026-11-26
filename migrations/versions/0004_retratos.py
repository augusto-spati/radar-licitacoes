"""retratos: tabela coleta_retrato e pagina_bruta generalizada (janela ou retrato)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-26 18:00:00.000000

As páginas existentes são todas do PNCP: ficam com formato 'json' (padrão da coluna nova) e
continuam presas à janela. Nada é reescrito.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coleta_retrato",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("iniciado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("concluido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paginas", sa.Integer(), server_default="0", nullable=False),
        sa.Column("registros", sa.Integer(), server_default="0", nullable=False),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('em_andamento', 'concluido', 'falha')", name="ck_coleta_retrato_status"
        ),
    )
    op.create_index("ix_coleta_retrato_fonte_iniciado", "coleta_retrato", ["fonte", "iniciado_em"])

    op.alter_column("pagina_bruta", "janela_id", nullable=True)
    op.alter_column("pagina_bruta", "conteudo", nullable=True)
    op.add_column("pagina_bruta", sa.Column("retrato_id", sa.Integer(), nullable=True))
    op.add_column(
        "pagina_bruta",
        sa.Column("formato", sa.Text(), server_default="json", nullable=False),
    )
    op.add_column("pagina_bruta", sa.Column("conteudo_texto", sa.Text(), nullable=True))
    op.add_column("pagina_bruta", sa.Column("url", sa.Text(), nullable=True))
    op.create_foreign_key(
        "pagina_bruta_retrato_id_fkey", "pagina_bruta", "coleta_retrato", ["retrato_id"], ["id"]
    )
    op.create_unique_constraint(
        "uq_pagina_bruta_retrato_numero", "pagina_bruta", ["retrato_id", "numero_pagina"]
    )
    op.create_check_constraint(
        "ck_pagina_bruta_origem", "pagina_bruta", "(janela_id IS NULL) <> (retrato_id IS NULL)"
    )
    op.create_check_constraint(
        "ck_pagina_bruta_formato", "pagina_bruta", "formato IN ('json', 'html', 'js')"
    )
    # Página de janela é sempre JSON do PNCP.
    op.create_check_constraint(
        "ck_pagina_bruta_janela_json", "pagina_bruta", "janela_id IS NULL OR formato = 'json'"
    )
    op.create_check_constraint(
        "ck_pagina_bruta_conteudo",
        "pagina_bruta",
        "(formato = 'json' AND conteudo IS NOT NULL)"
        " OR (formato IN ('html', 'js') AND conteudo_texto IS NOT NULL)",
    )


def downgrade() -> None:
    # O esquema 0003 só conhece páginas de janela: as de retrato são apagadas.
    op.execute("DELETE FROM pagina_bruta WHERE retrato_id IS NOT NULL")
    op.drop_constraint("ck_pagina_bruta_conteudo", "pagina_bruta", type_="check")
    op.drop_constraint("ck_pagina_bruta_janela_json", "pagina_bruta", type_="check")
    op.drop_constraint("ck_pagina_bruta_formato", "pagina_bruta", type_="check")
    op.drop_constraint("ck_pagina_bruta_origem", "pagina_bruta", type_="check")
    op.drop_constraint("uq_pagina_bruta_retrato_numero", "pagina_bruta", type_="unique")
    op.drop_constraint("pagina_bruta_retrato_id_fkey", "pagina_bruta", type_="foreignkey")
    for coluna in ("url", "conteudo_texto", "formato", "retrato_id"):
        op.drop_column("pagina_bruta", coluna)
    op.alter_column("pagina_bruta", "conteudo", nullable=False)
    op.alter_column("pagina_bruta", "janela_id", nullable=False)
    op.drop_index("ix_coleta_retrato_fonte_iniciado", table_name="coleta_retrato")
    op.drop_table("coleta_retrato")
