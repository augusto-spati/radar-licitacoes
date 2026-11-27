"""outbox e coleta_retrato.processado_em

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-26 20:00:00.000000

Os retratos 'concluido' que já existem foram processados pelo fluxo síncrono antigo: recebem
processado_em = concluido_em. As páginas do PNCP ainda não processadas ganham uma mensagem
`pagina_coletada` na outbox (o próximo relay as publica).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("carga", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "criada_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("publicada_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tentativas", sa.Integer(), server_default="0", nullable=False),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "tipo IN ('pagina_coletada', 'retrato_coletado')", name="ck_outbox_tipo"
        ),
    )
    op.create_index(
        "ix_outbox_pendente",
        "outbox",
        ["id"],
        postgresql_where=sa.text("publicada_em IS NULL"),
    )
    op.add_column(
        "coleta_retrato", sa.Column("processado_em", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE coleta_retrato SET processado_em = concluido_em"
        " WHERE status = 'concluido' AND concluido_em IS NOT NULL"
    )
    # Páginas do PNCP que o fluxo síncrono deixou pendentes: sem mensagem, ficariam órfãs.
    op.execute(
        "INSERT INTO outbox (tipo, carga, criada_em)"
        " SELECT 'pagina_coletada', jsonb_build_object('pagina_id', id), now()"
        " FROM pagina_bruta WHERE processada_em IS NULL AND janela_id IS NOT NULL ORDER BY id"
    )


def downgrade() -> None:
    op.drop_column("coleta_retrato", "processado_em")
    op.drop_index(
        "ix_outbox_pendente", table_name="outbox", postgresql_where=sa.text("publicada_em IS NULL")
    )
    op.drop_table("outbox")
