"""outbox.traceparent: o contexto do trace nasce com a mensagem

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-27 12:00:00.000000

O `traceparent` W3C do span que gravou a linha (a coleta da página, a conclusão do retrato, a
fatia que enfileirou a continuação), na mesma transação. O relay o leva no atributo
`traceparent` da mensagem SQS e o consumidor cria o span dele com link para esse contexto.
Nulo nas linhas antigas e nas gravadas fora de span: a mensagem segue sem link.

Vem depois da 0008 (análise por IA, etapa 6).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("outbox", sa.Column("traceparent", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("outbox", "traceparent")
