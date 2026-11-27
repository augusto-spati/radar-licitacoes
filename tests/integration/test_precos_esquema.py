"""Tabela `preco_item`: chave por origem, origem e escopo restritos."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from radar.precos import PlanilhaEdital, PrecoItem


def _item(
    origem: str = "tce_pe", chave: str = "compesa~1/2026~7", escopo: str | None = "material"
) -> PrecoItem:
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
        escopo=escopo,
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
    sessao.add(_item(origem="pncp_planilha"))
    sessao.flush()

    sessao.add(_item())
    with pytest.raises(IntegrityError):
        sessao.flush()


def test_origem_desconhecida_viola_check(sessao: Session) -> None:
    sessao.add(_item(origem="pncp"))
    with pytest.raises(IntegrityError):
        sessao.flush()


def test_escopo_desconhecido_viola_check(sessao: Session) -> None:
    sessao.add(_item(escopo="com_fornecimento"))
    with pytest.raises(IntegrityError):
        sessao.flush()


def test_escopo_nulo_fora_de_rede(sessao: Session) -> None:
    sessao.add(_item(escopo=None))
    sessao.flush()

    assert sessao.query(PrecoItem).one().escopo is None


def test_faixa_e_data_base_de_escavacao(sessao: Session) -> None:
    item = _item(escopo=None)
    item.servico, item.faixa, item.data_base = "escavacao_vala", "1_5_a_3m", date(2025, 5, 1)
    sessao.add(item)
    sessao.flush()

    linha = sessao.query(PrecoItem).one()
    assert (linha.faixa, linha.data_base) == ("1_5_a_3m", date(2025, 5, 1))


def test_faixa_desconhecida_viola_check(sessao: Session) -> None:
    item = _item(escopo=None)
    item.faixa = "ate_2m"
    sessao.add(item)
    with pytest.raises(IntegrityError):
        sessao.flush()


def test_origem_reservada_antiga_viola_check(sessao: Session) -> None:
    """`planilha_edital` era o nome reservado na 7b.1; a 7b.2 grava `pncp_planilha`."""
    sessao.add(_item(origem="planilha_edital"))
    with pytest.raises(IntegrityError):
        sessao.flush()


def test_observacao_adutora(sessao: Session) -> None:
    item = _item(origem="pncp_planilha")
    item.observacao = "adutora"
    sessao.add(item)
    sessao.flush()

    assert sessao.query(PrecoItem).one().observacao == "adutora"


def test_planilha_edital_padroes_e_status(sessao: Session) -> None:
    sessao.add(PlanilhaEdital(id_pncp="1-1-000001/2026", status="sem_planilha"))
    sessao.flush()
    linha = sessao.query(PlanilhaEdital).one()
    sessao.refresh(linha)
    assert (linha.linhas_lidas, linha.linhas_gravadas, linha.custo_usd) == (0, 0, Decimal(0))
    assert linha.processada_em is not None

    sessao.add(PlanilhaEdital(id_pncp="1-1-000002/2026", status="lida"))
    with pytest.raises(IntegrityError):
        sessao.flush()


def test_planilha_edital_metodo_desconhecido_viola_check(sessao: Session) -> None:
    sessao.add(PlanilhaEdital(id_pncp="1-1-000003/2026", status="extraida", metodo="ocr"))
    with pytest.raises(IntegrityError):
        sessao.flush()
