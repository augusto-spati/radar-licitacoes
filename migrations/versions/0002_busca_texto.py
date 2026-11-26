"""busca_texto

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-26 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    # IMMUTABLE porque coluna gerada e índice de expressão exigem função imutável,
    # e unaccent() é só STABLE (depende do dicionário no search_path).
    op.execute(
        """
        CREATE FUNCTION radar_unaccent(text) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        AS $$ SELECT public.unaccent('public.unaccent', $1) $$
        """
    )
    op.execute(
        """
        ALTER TABLE edital ADD COLUMN busca tsvector
        GENERATED ALWAYS AS (to_tsvector('portuguese', radar_unaccent(objeto))) STORED
        """
    )
    op.execute("CREATE INDEX ix_edital_busca ON edital USING GIN (busca)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_edital_busca")
    op.execute("ALTER TABLE edital DROP COLUMN busca")
    op.execute("DROP FUNCTION radar_unaccent(text)")
    # A extensão unaccent fica instalada: é inofensiva e pode ter outros usos.
