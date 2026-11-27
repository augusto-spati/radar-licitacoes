"""Linhas das planilhas → `preco_item` (sem banco) e a guarda das linhas que a IA devolve."""

import re
from dataclasses import replace
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


# --- IA: a guarda das linhas que a IA devolve (texto picotado de Ibaté) ---

IBATE = (DADOS / "ibate.txt").read_text(encoding="utf-8")
SUBTOTAL = "16.122,94"  # "SUB TOTAL 1 R$ 16.122,94": soma de 1.1, 1.2 e 1.3


def _ia(
    item: str, descricao: str, unidade: str, q: str, sem: str, com: str, total: str
) -> dict[str, object]:
    return {
        "item": item,
        "descricao": descricao,
        "unidade": unidade,
        "quantidade": q,
        "preco_sem_bdi": sem,
        "preco_com_bdi": com,
        "total": total,
    }


PLACA = _ia("1.1", "Placa de identificação de obra", "m²", "6,00", "870,95", "1.088,69", "6.532,13")
ESCRITORIO = _ia(
    "1.2",
    "Locação de container tipo escritório",
    "unid/mês",
    "3,00",
    "1.553,18",
    "1.941,48",
    "5.824,43",
)
DEPOSITO = _ia(
    "1.3",
    "Locação de container tipo depósito",
    "unid/mês",
    "3,00",
    "1.004,37",
    "1.255,46",
    "3.766,39",
)


def test_linhas_da_ia_ancoradas_no_texto_picotado() -> None:
    linhas = linhas_da_ia([PLACA, ESCRITORIO, DEPOSITO], IBATE, total_planilha=SUBTOTAL)
    assert [(linha.item, linha.preco_com_bdi, linha.preco_sem_bdi) for linha in linhas] == [
        ("1.1", Decimal("1088.69"), Decimal("870.95")),
        ("1.2", Decimal("1941.48"), Decimal("1553.18")),
        ("1.3", Decimal("1255.46"), Decimal("1004.37")),
    ]


def test_sem_total_da_planilha_nada_vale() -> None:
    assert linhas_da_ia([PLACA, ESCRITORIO, DEPOSITO], IBATE) == []
    # Total que não está no texto também não confere nada.
    assert linhas_da_ia([PLACA, ESCRITORIO, DEPOSITO], IBATE, total_planilha="16.122,95") == []


def test_soma_longe_do_total_da_planilha_derruba_tudo() -> None:
    assert linhas_da_ia([PLACA], IBATE, total_planilha=SUBTOTAL) == []


def test_tubo_colado_no_preco_e_total_de_outra_linha_cai() -> None:
    """O preço e o total são do tubo de FoFo DN 150 (item 2.1); a descrição é de outro tubo."""
    colado = _ia(
        "9.9", "TUBO PVC DN 200 PARA REDE DE ÁGUA", "m", "24,00", "625,02", "781,28", "18.750,60"
    )
    certo = {**colado, "item": "2.1", "descricao": 'Tubo de ferro fundido classe K7, DN 6" (150mm)'}
    # O total geral não está no trecho: o total da própria linha serve de total da planilha.
    assert linhas_da_ia([colado], IBATE, total_planilha="18.750,60") == []
    assert [linha.item for linha in linhas_da_ia([certo], IBATE, total_planilha="18.750,60")] == [
        "2.1"
    ]


def test_numero_dentro_de_outro_numero_nao_ancora() -> None:
    """ "1.088,69" não pode casar dentro de "11.088,69"; quantidade longe do preço não vale."""
    texto = "Placa m² 6,00 R$ 11.088,69 R$ 66.532,14 total R$ 66.532,14"
    falsa = _ia("1.1", "Placa", "m²", "6,00", "", "1.088,69", "6.532,14")
    assert linhas_da_ia([falsa], texto, total_planilha="66.532,14") == []
    longe = "Placa m² 6,00 " + "x" * 300 + " R$ 1.088,69 R$ 6.532,14 TOTAL R$ 6.532,14"
    assert linhas_da_ia([falsa], longe, total_planilha="6.532,14") == []
    perto = "Placa m² 6,00 R$ 1.088,69 R$ 6.532,14 TOTAL R$ 6.532,14"
    assert len(linhas_da_ia([falsa], perto, total_planilha="6.532,14")) == 1


def test_linha_da_ia_sem_conta_cai() -> None:
    sem_conta = {**PLACA, "quantidade": "7,00"}
    assert linhas_da_ia([sem_conta, ESCRITORIO, DEPOSITO], IBATE, total_planilha=SUBTOTAL) == []


def test_bdi_implausivel_vira_desconhecido() -> None:
    linha = replace(_linha("1.1", "120", "50"))  # 120 ÷ 50 − 1 = 140%
    (item,) = itens_da_planilha(RIBEIRAO, SHA, [linha])
    assert (item.bdi, item.bdi_desconhecido) == (None, True)
    peso = replace(_linha("1.2", "100"), bdi=Decimal("0.0035"))  # "Peso (%)" que escapasse
    (item_peso,) = itens_da_planilha(RIBEIRAO, SHA, [peso])
    assert (item_peso.bdi, item_peso.bdi_desconhecido) == (None, True)


def test_dn_nao_se_ancora_na_quantidade() -> None:
    """DN 100 na descrição e "100,00" de quantidade no texto: sem "DN 100"/"100 mm" no texto,
    a linha cai; com a forma de diâmetro, vale."""
    linha = _ia(
        "1.1", "Tubo PVC DN 100 para rede de esgoto", "m", "100,00", "", "50,00", "5.000,00"
    )
    sem_dn = "1.1 Tubo PVC rede de esgoto m 100,00 R$ 50,00 R$ 5.000,00 TOTAL R$ 5.000,00"
    assert linhas_da_ia([linha], sem_dn, total_planilha="5.000,00") == []
    for forma in ("DN 100", "DN100", "Ø100", "100 mm"):
        com_dn = sem_dn.replace("PVC", f"PVC {forma}")
        assert len(linhas_da_ia([linha], com_dn, total_planilha="5.000,00")) == 1, forma
