"""multifonte: chave (fonte, id_na_fonte) e situação normalizada

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-26 15:00:00.000000

Tudo numa transação só (o Alembic abre uma por migração no Postgres). No banco local, com
~38 mil editais, leva poucos segundos; em produção grande, o caminho seria outro (colunas
novas, backfill em lotes, índice CONCURRENTLY e troca da chave numa janela curta).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("edital", sa.Column("fonte", sa.Text(), nullable=True))
    op.add_column("edital", sa.Column("id_na_fonte", sa.Text(), nullable=True))
    op.add_column("edital", sa.Column("modalidade_nome", sa.Text(), nullable=True))
    op.add_column("edital", sa.Column("situacao_normalizada", sa.Text(), nullable=True))
    op.add_column("edital", sa.Column("visto_em", sa.DateTime(timezone=True), nullable=True))
    op.add_column("edital", sa.Column("hash_conteudo", sa.Text(), nullable=True))
    # O nome da situação do PNCP é a situação original da fonte.
    op.alter_column("edital", "situacao_nome", new_column_name="situacao_original")

    # Mapa do PNCP: 1 Divulgada -> aberta; 2 Revogada, 3 Anulada -> encerrada; 4 Suspensa e
    # qualquer outro código -> outra.
    op.execute(
        """
        UPDATE edital SET
            fonte = 'pncp',
            id_na_fonte = numero_controle_pncp,
            modalidade_nome = COALESCE(bruto->>'modalidadeNome', 'modalidade ' || modalidade_id),
            situacao_normalizada = CASE situacao_id
                WHEN 1 THEN 'aberta'
                WHEN 2 THEN 'encerrada'
                WHEN 3 THEN 'encerrada'
                ELSE 'outra'
            END
        """
    )

    for coluna in ("fonte", "id_na_fonte", "modalidade_nome", "situacao_normalizada"):
        op.alter_column("edital", coluna, nullable=False)
    # Só o PNCP tem esses dados; as outras fontes podem não trazer.
    for coluna in ("modalidade_id", "situacao_id", "orgao_cnpj", "data_publicacao"):
        op.alter_column("edital", coluna, nullable=True)

    op.drop_constraint("edital_pkey", "edital", type_="primary")
    op.create_primary_key("edital_pkey", "edital", ["fonte", "id_na_fonte"])
    op.drop_column("edital", "numero_controle_pncp")

    op.create_check_constraint(
        "ck_edital_fonte", "edital", "fonte IN ('pncp', 'sanepar', 'compesa')"
    )
    op.create_check_constraint(
        "ck_edital_situacao",
        "edital",
        "situacao_normalizada IN ('aberta', 'em_julgamento', 'encerrada', 'outra')",
    )
    op.create_index(
        "ix_edital_situacao_encerramento",
        "edital",
        ["situacao_normalizada", "data_encerramento_proposta"],
    )


def downgrade() -> None:
    # O esquema 0002 só conhece o PNCP: editais de outras fontes são apagados.
    op.execute("DELETE FROM edital WHERE fonte <> 'pncp'")
    op.drop_index("ix_edital_situacao_encerramento", table_name="edital")
    op.drop_constraint("ck_edital_situacao", "edital", type_="check")
    op.drop_constraint("ck_edital_fonte", "edital", type_="check")

    op.add_column("edital", sa.Column("numero_controle_pncp", sa.Text(), nullable=True))
    op.execute("UPDATE edital SET numero_controle_pncp = id_na_fonte")
    op.alter_column("edital", "numero_controle_pncp", nullable=False)
    op.drop_constraint("edital_pkey", "edital", type_="primary")
    op.create_primary_key("edital_pkey", "edital", ["numero_controle_pncp"])

    for coluna in ("modalidade_id", "situacao_id", "orgao_cnpj", "data_publicacao"):
        op.alter_column("edital", coluna, nullable=False)
    op.alter_column("edital", "situacao_original", new_column_name="situacao_nome")
    for coluna in (
        "hash_conteudo",
        "visto_em",
        "situacao_normalizada",
        "modalidade_nome",
        "id_na_fonte",
        "fonte",
    ):
        op.drop_column("edital", coluna)
