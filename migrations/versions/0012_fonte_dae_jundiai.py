"""fonte dae_jundiai no CHECK de edital.fonte

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-27 23:00:00.000000

Coletor da DAE S/A Jundiaí (Compra Eletrônica do CIJUN): a fonte nova entra no `ck_edital_fonte`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_edital_fonte", "edital", type_="check")
    op.create_check_constraint(
        "ck_edital_fonte", "edital", "fonte IN ('pncp', 'sanepar', 'compesa', 'dae_jundiai')"
    )


def downgrade() -> None:
    # O CHECK antigo não aceita a fonte: os editais dela são apagados.
    op.execute("DELETE FROM edital WHERE fonte = 'dae_jundiai'")
    op.drop_constraint("ck_edital_fonte", "edital", type_="check")
    op.create_check_constraint(
        "ck_edital_fonte", "edital", "fonte IN ('pncp', 'sanepar', 'compesa')"
    )
