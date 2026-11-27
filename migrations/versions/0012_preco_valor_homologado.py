"""preco_item.valor_homologado

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-27 23:00:00.000000

Etapa 7b.1 (revisão final): guarda o `valorHomologado` do processo do TCE-PE em cada linha, para
a rodada semanal não buscar de novo os contratos de processo já casado cujo homologado não mudou.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("preco_item", sa.Column("valor_homologado", sa.Numeric(), nullable=True))


def downgrade() -> None:
    op.drop_column("preco_item", "valor_homologado")
