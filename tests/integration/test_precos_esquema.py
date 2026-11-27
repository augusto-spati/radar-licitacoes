"""Tabela `preco_item`: chave por origem e origem restrita."""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from radar.precos import PrecoItem


def _item(origem: str = "tce_pe", chave: str = "compesa~1/2026~7") -> PrecoItem:
    return PrecoItem(
        origem=origem,
        chave_origem=chave,
        uf="PE",
        orgao_nome="Compesa",
        processo="1/2026",
        descricao="TUBO PVC P/ REDE DE �GUA DN 150",
        unidade="M",
        quantidade=Decimal("120.5"),
        preco_com_bdi=Decimal("98.76"),
        servico="rede_agua",
        material="pvc",
        dn=150,
        com_fornecimento=True,
        unidade_canonica="m",
        fator_unidade=Decimal("1"),
    )


def test_grava_com_padroes(sessao: Session) -> None:
    sessao.add(_item())
    sessao.flush()

    linha = sessao.query(PrecoItem).one()
    sessao.refresh(linha)
    assert linha.bdi_desconhecido is False
    assert linha.suspeito is False
    assert linha.gravado_em is not None
    assert linha.quantidade == Decimal("120.5")
    assert linha.descricao.endswith("�GUA DN 150")


def test_chave_unica_por_origem(sessao: Session) -> None:
    sessao.add(_item())
    sessao.add(_item(origem="planilha_edital"))
    sessao.flush()

    sessao.add(_item())
    with pytest.raises(IntegrityError):
        sessao.flush()


def test_origem_desconhecida_viola_check(sessao: Session) -> None:
    sessao.add(_item(origem="pncp"))
    with pytest.raises(IntegrityError):
        sessao.flush()
