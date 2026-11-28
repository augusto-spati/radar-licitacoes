"""primeiro_visto_em, índice da última análise e perfil_resumo

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-28 10:00:00.000000

Etapa 8a: o painel. `edital.primeiro_visto_em` marca quando o radar viu o edital pela primeira
vez (só na inserção; o upsert não o toca). O backfill nunca deixa um edital antigo parecer novo:
é o menor entre `visto_em`, a publicação (horário de Brasília) e 30 dias atrás. O índice serve
`ORDER BY criada_em DESC, id DESC` da última análise de cada edital. `perfil_resumo` guarda uma
linha com o resumo não sensível do perfil (para o painel saber se é o perfil de exemplo).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "edital", sa.Column("primeiro_visto_em", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE edital SET primeiro_visto_em = least(visto_em, "
        "data_publicacao AT TIME ZONE 'America/Sao_Paulo', now() - interval '30 days')"
    )
    op.alter_column(
        "edital",
        "primeiro_visto_em",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    op.create_index(
        "ix_analise_edital_ultima",
        "analise_edital",
        ["fonte", "id_na_fonte", "modelo", "criada_em", "id"],
    )
    op.create_table(
        "perfil_resumo",
        sa.Column("id", sa.SmallInteger(), server_default="1", primary_key=True),
        sa.Column("exemplo", sa.Boolean(), nullable=False),
        sa.Column("uf_sede", sa.CHAR(2), nullable=True),
        sa.Column("valor_maximo_obra", sa.Numeric(), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_perfil_resumo_uma_linha"),
    )


def downgrade() -> None:
    op.drop_table("perfil_resumo")
    op.drop_index("ix_analise_edital_ultima", table_name="analise_edital")
    op.drop_column("edital", "primeiro_visto_em")
