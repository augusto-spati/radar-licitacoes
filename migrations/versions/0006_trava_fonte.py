"""trava_fonte: lease por fonte no lugar do advisory lock

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-26 23:00:00.000000

O advisory lock prendia uma conexão em transação ociosa durante o retrato inteiro: o Neon a
encerrava por idle_in_transaction_session_timeout, e pelo pooler (PgBouncer em modo
transação) lock de sessão não é confiável. O lease é uma linha com dono e prazo.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trava_fonte",
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("dono", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("obtida_em", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("fonte"),
    )


def downgrade() -> None:
    op.drop_table("trava_fonte")
