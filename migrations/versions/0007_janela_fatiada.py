"""janela fatiada: checkpoint, lote (fencing token) e lease em coleta_janela

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-27 09:00:00.000000

A coleta do PNCP passa a andar em fatias (uma por invocação da Lambda coletora). A janela
guarda a próxima página, o total de páginas e de registros da página 1 (para a checagem de
completude valer entre fatias), o número da fatia (`lote`) e o lease de quem coleta agora.
A outbox aceita `coletar_janela`. As janelas `em_andamento` existentes eram sobra de execução
interrompida (havia um único coletor por vez): voltam a `pendente`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "coleta_janela",
        sa.Column("proxima_pagina", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("coleta_janela", sa.Column("total_paginas", sa.Integer(), nullable=True))
    op.add_column("coleta_janela", sa.Column("esperados", sa.Integer(), nullable=True))
    op.add_column(
        "coleta_janela", sa.Column("lote", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("coleta_janela", sa.Column("travada_por", sa.Text(), nullable=True))
    op.add_column(
        "coleta_janela", sa.Column("travada_ate", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE coleta_janela SET status = 'pendente' WHERE status = 'em_andamento'")
    op.drop_constraint("ck_outbox_tipo", "outbox", type_="check")
    op.create_check_constraint(
        "ck_outbox_tipo",
        "outbox",
        "tipo IN ('pagina_coletada', 'retrato_coletado', 'coletar_janela')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM outbox WHERE tipo = 'coletar_janela'")
    op.drop_constraint("ck_outbox_tipo", "outbox", type_="check")
    op.create_check_constraint(
        "ck_outbox_tipo", "outbox", "tipo IN ('pagina_coletada', 'retrato_coletado')"
    )
    for coluna in (
        "travada_ate",
        "travada_por",
        "lote",
        "esperados",
        "total_paginas",
        "proxima_pagina",
    ):
        op.drop_column("coleta_janela", coluna)
