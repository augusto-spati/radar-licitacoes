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
from radar.precos.modelos import PrecoItemNovo
from radar.precos.planilhas import (
    marcar_fora_de_escala,
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


def test_tubo_da_secao_de_ramal_nao_e_rede() -> None:
    """Ribeirão Preto (Anexo IV do ZIP): o mesmo tubo em "Coletor" é rede; em "Ramal", não."""

    def tubo(item: str, secao: str) -> LinhaPlanilha:
        dn = "100" if secao == "Ramal" else "150"
        descricao = f"Tubo, PVC Ocre, DN {dn} - fornec./assent."
        return replace(_linha(item, "120", "100"), descricao=descricao, secao=secao)

    itens = itens_da_planilha(RIBEIRAO, SHA, [tubo("2.11", "Coletor"), tubo("4.12", "Ramal")])
    assert [(i.chave_origem.rsplit("#", 1)[-1], i.servico, i.dn) for i in itens] == [
        ("2.11", "rede_esgoto", 150)
    ]


MATERIAIS_HIDRAULICOS = replace(
    RIBEIRAO,
    id_pncp="25269069000146-1-000045/2026",
    objeto="Materiais hidráulicos para manutenção, reparo e execução de serviços na rede de "
    "água e esgoto",
)


def test_item_seguinte_nao_vira_ligacao_com_o_preco_do_hidrometro() -> None:
    """Revisão: com a descrição do 18 herdada, o 19 viraria ligação de água a R$ 1.216,92."""
    pagina = """18
Ligação predial de água
completa com kit cavalete
Serviço 15  R$   2.283,33  R$ 34.249,95
19 Hidrometro medidor de vazão com capacidade de 16m3 h. Un 8  R$   1.216,92  R$ 9.735,36
"""
    rural = replace(RIBEIRAO, objeto="Sistemas rurais de abastecimento de água")
    assert itens_da_planilha(rural, SHA, ler_planilha([pagina])) == []


def test_continuacao_nao_decide_agua_ou_esgoto_no_edital_dos_dois() -> None:
    """ "50 MM PARA REDE DE ESGOTO" virava seção e desempatava o "ÁGUA OU ESGOTO"."""
    pagina = (
        "50 MM PARA REDE DE ESGOTO\n"
        "1.1 ASSENTAMENTO DE TUBO PEAD LISO PARA REDE DE ÁGUA OU ESGOTO, DIÂMETRO DE 110 MM "
        "M 20,00 1,80 2,25 45,00\n"
    )
    (linha,) = ler_planilha([pagina])
    assert itens_da_planilha(MATERIAIS_HIDRAULICOS, SHA, [linha]) == []


# --- Preço fora da escala do DN (barra de 6 m numa linha "METRO") ---

TRES_PONTAS = replace(
    RIBEIRAO,
    id_pncp="00000000000000-1-000001/2026",
    municipio="Três Pontas",
    orgao_nome="SAAE Três Pontas",
    uf="MG",
    objeto="Ampliação da rede de distribuição de água",
)


def _tubo(item: str, descricao: str, com: str) -> LinhaPlanilha:
    return replace(_linha(item, com), descricao=descricao, total=Decimal(com) * 10)


def _suspeitos(itens: list[PrecoItemNovo]) -> dict[tuple[str | None, int | None], bool]:
    return {(i.material, i.dn): i.suspeito for i in itens}


def test_tres_pontas_preco_da_barra_no_metro_e_suspeito() -> None:
    descricao = "Tubo PVC DN {} fornecimento e assentamento para rede de água"
    linhas = [
        _tubo("1.1", descricao.format(50), "12.32"),
        _tubo("1.2", descricao.format(75), "207.74"),
        _tubo("1.3", descricao.format(100), "274.90"),
    ]
    itens = itens_da_planilha(TRES_PONTAS, SHA, linhas)
    assert len({(i.servico, i.material, i.escopo, i.unidade_canonica) for i in itens}) == 1
    assert _suspeitos(itens) == {("pvc", 50): False, ("pvc", 75): True, ("pvc", 100): True}
    # A linha fica como veio; só a marca muda.
    assert [i.preco_com_bdi for i in itens] == [
        Decimal("12.32"),
        Decimal("207.74"),
        Decimal("274.90"),
    ]
    assert all(i.observacao is None for i in itens)


def _item(
    dn: int | None, preco: str, material: str | None = "pvc", fator: str = "1"
) -> PrecoItemNovo:
    return PrecoItemNovo(
        origem="pncp_planilha",
        chave_origem=f"x#{material}{dn}",
        uf="MG",
        municipio=None,
        orgao_nome="SAAE",
        orgao_cnpj=None,
        processo="x",
        data=None,
        data_base=None,
        descricao="tubo",
        unidade="m",
        quantidade=Decimal(10),
        preco_sem_bdi=None,
        bdi=None,
        preco_com_bdi=Decimal(preco),
        bdi_desconhecido=True,
        preco_contratado_com_bdi=None,
        fonte_referencia=None,
        codigo_referencia=None,
        servico="rede_agua",
        material=material,
        dn=dn,
        escopo="completo",
        faixa=None,
        unidade_canonica="m",
        fator_unidade=Decimal(fator),
        suspeito=False,
    )


def test_serie_crescente_normal_nao_marca_nada() -> None:
    itens = [
        _item(50, "25"),
        _item(75, "40"),
        _item(100, "60"),
        _item(150, "110"),
        _item(300, "420"),
    ]
    assert not any(i.suspeito for i in marcar_fora_de_escala(itens))


def test_economia_de_escala_e_mao_de_obra_nao_marcam() -> None:
    # Assentamento: cresce bem menos que DN²; DN maior pode até sair um pouco mais barato.
    itens = [_item(50, "8"), _item(100, "7"), _item(400, "40")]
    assert not any(i.suspeito for i in marcar_fora_de_escala(itens))


def test_uma_linha_so_nao_marca() -> None:
    assert not marcar_fora_de_escala([_item(75, "207.74")])[0].suspeito


def test_materiais_diferentes_nao_se_comparam() -> None:
    itens = marcar_fora_de_escala([_item(50, "12.32", "pvc"), _item(75, "207.74", "fofo")])
    assert not any(i.suspeito for i in itens)


def test_dn_menor_muito_mais_caro_que_o_maior_e_suspeito() -> None:
    itens = marcar_fora_de_escala([_item(50, "74"), _item(75, "20"), _item(100, "28")])
    assert _suspeitos(itens) == {("pvc", 50): True, ("pvc", 75): False, ("pvc", 100): False}


def test_mesmo_dn_com_precos_diferentes_fica_como_esta() -> None:
    itens = marcar_fora_de_escala([_item(75, "20"), _item(75, "120")])
    assert not any(i.suspeito for i in itens)


def test_preco_comparado_na_unidade_canonica() -> None:
    # Preço por barra de 6 m com fator 6: R$ 120/barra = R$ 20/m, na escala do DN 50.
    itens = marcar_fora_de_escala([_item(50, "12"), _item(75, "120", fator="6")])
    assert not any(i.suspeito for i in itens)


def test_sem_dn_nao_entra() -> None:
    itens = marcar_fora_de_escala([_item(None, "500"), _item(50, "10")])
    assert not any(i.suspeito for i in itens)
