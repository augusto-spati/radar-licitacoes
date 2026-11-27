"""Linhas das planilhas → `preco_item` (sem banco) e a guarda das linhas que a IA devolve."""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from radar.precos import (
    EditalPlanilha,
    LinhaPlanilha,
    itens_da_planilha,
    ler_planilha,
    linhas_da_ia,
)

DADOS = Path(__file__).resolve().parents[1] / "dados" / "planilhas"

SANTA_BARBARA = EditalPlanilha(
    id_pncp="54010863000179-1-000533/2026",
    uf="SP",
    municipio="Santa Bárbara d'Oeste",
    orgao_nome="DAE Santa Bárbara d'Oeste",
    orgao_cnpj="54010863000179",
    objeto="Contratação de empresa para substituição da adutora de água tratada.",
    data=date(2026, 11, 23),
)
RIBEIRAO = EditalPlanilha(
    id_pncp="56024581000156-1-000529/2026",
    uf="SP",
    municipio="Ribeirão Preto",
    orgao_nome="SAERP",
    orgao_cnpj="56024581000156",
    objeto="Contratação de empresa para substituição de coletor de esgoto",
    data=date(2026, 11, 20),
)
SHA = "ab" * 32


def paginas(nome: str) -> list[str]:
    texto = (DADOS / f"{nome}.txt").read_text(encoding="utf-8")
    return [p for p in re.split(r"=====PAGINA \d+\n", texto) if p.strip()]


def test_santa_barbara_adutora_com_contexto_e_bdi_da_linha() -> None:
    itens = itens_da_planilha(SANTA_BARBARA, SHA, ler_planilha(paginas("santa_barbara")))
    por_chave = {i.chave_origem.rsplit("#", 1)[-1]: i for i in itens}
    tubo = por_chave["4.1"]
    assert tubo.chave_origem == f"{SANTA_BARBARA.id_pncp}#{SHA[:12]}#4.1"
    assert (tubo.origem, tubo.processo, tubo.uf) == ("pncp_planilha", SANTA_BARBARA.id_pncp, "SP")
    assert (tubo.servico, tubo.material, tubo.dn, tubo.escopo) == (
        "rede_agua",
        "pvc",
        250,
        "material",
    )
    assert tubo.observacao == "adutora"
    assert (tubo.preco_sem_bdi, tubo.preco_com_bdi, tubo.bdi) == (
        Decimal("284.07"),
        Decimal("327.48"),
        Decimal("0.1528"),
    )
    assert tubo.bdi_desconhecido is False
    assert (tubo.fonte_referencia, tubo.codigo_referencia) == ("SABESP", "HM07752")
    assert tubo.data_base == date(2026, 7, 1)
    assert tubo.preco_contratado_com_bdi is None
    assert por_chave["8.1"].faixa == "ate_1_5m"
    assert {i.servico for i in itens} == {"rede_agua", "escavacao_vala"}


def test_ribeirao_coletor_bdi_calculado() -> None:
    itens = itens_da_planilha(RIBEIRAO, SHA, ler_planilha(paginas("ribeirao")))
    assert [(i.servico, i.material, i.dn, i.escopo) for i in itens] == [
        ("rede_esgoto", "pvc", 150, "completo"),
        ("rede_esgoto", "pvc", 200, "completo"),
        ("rede_esgoto", "pvc", 250, "completo"),
    ]
    assert itens[1].bdi == Decimal("0.2352")  # 184,95 ÷ 149,73 − 1
    assert all(i.observacao is None for i in itens)


def _linha(item: str, com: str, sem: str | None = None) -> LinhaPlanilha:
    return LinhaPlanilha(
        item=item,
        codigo=None,
        fonte=None,
        descricao="Tubo PVC DN 150 fornecimento e assentamento para rede de esgoto",
        unidade="m",
        quantidade=Decimal(10),
        preco_sem_bdi=None if sem is None else Decimal(sem),
        preco_com_bdi=Decimal(com),
        total=Decimal(com) * 10,
        bdi=None,
        data_base=None,
        secao=None,
        pagina=1,
    )


def test_um_preco_so_e_bdi_desconhecido_e_item_repetido() -> None:
    itens = itens_da_planilha(RIBEIRAO, SHA, [_linha("1.1", "100"), _linha("1.1", "120", "100")])
    assert [i.chave_origem.rsplit("#", 1)[-1] for i in itens] == ["1.1", "1.1~2"]
    assert (itens[0].bdi, itens[0].bdi_desconhecido) == (None, True)
    assert (itens[1].bdi, itens[1].bdi_desconhecido) == (Decimal("0.2000"), False)


# --- IA ---

IBATE = (DADOS / "ibate.txt").read_text(encoding="utf-8")


def _ia(**campos: object) -> dict[str, object]:
    base: dict[str, object] = {
        "item": "2.2",
        "codigo": "103091",
        "fonte": "SINAPI",
        "descricao": "ASSENTAMENTO DE TUBO DE FERRO FUNDIDO PARA REDE DE ÁGUA, DN 150",
        "unidade": "m",
        "quantidade": "24,00",
        "preco_sem_bdi": "24,10",
        "preco_com_bdi": "30,13",
        "total": "723,12",
    }
    base.update(campos)
    return base


def test_linhas_da_ia_ancoradas_no_texto_picotado() -> None:
    # "R$\n870,\n95" no texto: sem espaços o número aparece inteiro.
    placa = _ia(
        item="1.1",
        descricao="Placa de identificação de obra",
        unidade="m²",
        quantidade=6,
        preco_sem_bdi="870,95",
        preco_com_bdi="1.088,69",
        total="6.532,13",
    )
    linhas = linhas_da_ia([placa], IBATE)
    assert [(linha.item, linha.preco_com_bdi, linha.preco_sem_bdi) for linha in linhas] == [
        ("1.1", Decimal("1088.69"), Decimal("870.95"))
    ]


def test_linha_da_ia_sem_conta_ou_fora_do_texto_cai() -> None:
    inventada = _ia(preco_com_bdi="31,00", total="744,00")  # fecha a conta, não está no texto
    sem_conta = _ia(preco_com_bdi="1.088,69", total="6.532,13", quantidade=7)
    assert linhas_da_ia([inventada, sem_conta], IBATE) == []


def test_soma_longe_do_total_da_planilha_derruba_tudo() -> None:
    placa = _ia(
        item="1.1",
        descricao="Placa de identificação de obra",
        unidade="m²",
        quantidade=6,
        preco_com_bdi="1.088,69",
        total="6.532,13",
    )
    # "R$ 16.122,94" é o subtotal no texto: uma linha só fica a mais de 2% dele.
    assert linhas_da_ia([placa], IBATE, total_planilha="16.122,94") == []
    # Total que não está no texto não vale como conferência.
    assert len(linhas_da_ia([placa], IBATE, total_planilha="99.999,99")) == 1
