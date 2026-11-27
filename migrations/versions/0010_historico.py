"""historico_carga, resultado_item e resultado_busca

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-27 18:00:00.000000

Etapa 7a: carga histórica de contratações (checkpoint por UF, modalidade e mês) e os resultados
dos itens (vencedor, valor homologado) com o resumo por contratação. Índice em
`edital(orgao_cnpj)` para o histórico do órgão.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "historico_carga",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("uf", sa.Text(), nullable=False),
        sa.Column("modalidade", sa.Integer(), nullable=False),
        sa.Column("mes", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("proxima_pagina", sa.Integer(), server_default="1", nullable=False),
        sa.Column("total_paginas", sa.Integer(), nullable=True),
        sa.Column("registros_lidos", sa.Integer(), server_default="0", nullable=False),
        sa.Column("registros_gravados", sa.Integer(), server_default="0", nullable=False),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("atualizada_em", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("uf", "modalidade", "mes", name="uq_historico_carga_chave"),
        sa.CheckConstraint(
            "status IN ('pendente', 'em_andamento', 'concluida', 'falha')",
            name="ck_historico_carga_status",
        ),
    )
    op.create_table(
        "resultado_item",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("id_na_fonte", sa.Text(), nullable=False),
        sa.Column("numero_item", sa.Integer(), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column("unidade", sa.Text(), nullable=True),
        sa.Column("quantidade", sa.Numeric(), nullable=True),
        sa.Column("valor_unitario_estimado", sa.Numeric(), nullable=True),
        sa.Column("valor_total_estimado", sa.Numeric(), nullable=True),
        sa.Column("orcamento_sigiloso", sa.Boolean(), nullable=False),
        sa.Column("situacao", sa.Text(), nullable=False),
        sa.Column("fornecedor_ni", sa.Text(), nullable=True),
        sa.Column("fornecedor_nome", sa.Text(), nullable=True),
        sa.Column("fornecedor_porte", sa.Text(), nullable=True),
        sa.Column("quantidade_homologada", sa.Numeric(), nullable=True),
        sa.Column("valor_unitario_homologado", sa.Numeric(), nullable=True),
        sa.Column("valor_total_homologado", sa.Numeric(), nullable=True),
        sa.Column("data_resultado", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["fonte", "id_na_fonte"],
            ["edital.fonte", "edital.id_na_fonte"],
            name="fk_resultado_item_edital",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("fonte", "id_na_fonte", "numero_item", name="uq_resultado_item"),
    )
    op.create_table(
        "resultado_busca",
        sa.Column("fonte", sa.Text(), nullable=False),
        sa.Column("id_na_fonte", sa.Text(), nullable=False),
        sa.Column("buscado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("itens", sa.Integer(), nullable=False),
        sa.Column("situacao", sa.Text(), nullable=False),
        sa.Column("desconto", sa.Numeric(), nullable=True),
        sa.Column("valor_estimado_considerado", sa.Numeric(), nullable=True),
        sa.Column("valor_homologado_considerado", sa.Numeric(), nullable=True),
        sa.Column("vencedor_ni", sa.Text(), nullable=True),
        sa.Column("vencedor_nome", sa.Text(), nullable=True),
        sa.Column("vencedor_porte", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("fonte", "id_na_fonte", name="pk_resultado_busca"),
        sa.ForeignKeyConstraint(
            ["fonte", "id_na_fonte"],
            ["edital.fonte", "edital.id_na_fonte"],
            name="fk_resultado_busca_edital",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "situacao IN ('homologada', 'fracassada', 'deserta', 'parcial', 'em_andamento',"
            " 'sem_itens', 'anulada')",
            name="ck_resultado_busca_situacao",
        ),
    )
    op.create_index("ix_resultado_busca_situacao", "resultado_busca", ["situacao"])
    op.create_index("ix_edital_orgao_cnpj", "edital", ["orgao_cnpj"])


def downgrade() -> None:
    op.drop_index("ix_edital_orgao_cnpj", table_name="edital")
    op.drop_index("ix_resultado_busca_situacao", table_name="resultado_busca")
    op.drop_table("resultado_busca")
    op.drop_table("resultado_item")
    op.drop_table("historico_carga")
