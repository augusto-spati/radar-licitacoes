"""Leitura determinística das planilhas orçamentárias, com trechos reais do spike da 7b.2.

`tests/dados/planilhas/*.txt`: trechos do texto que o pypdf extraiu (uma página por marca
`=====PAGINA n`) das planilhas de Cosmópolis (adutora), Santa Bárbara d'Oeste (adutora),
Ribeirão Preto (coletor), Araraquara (coletor) e Ibaté (planilha dentro do edital, texto
picotado). Só trechos, nunca o PDF.
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from radar.precos.planilha_leitura import (
    LinhaPlanilha,
    coerente,
    data_de_token,
    datas_do_cabecalho,
    ler_planilha,
    numero_br,
    pagina_de_composicao,
)

DADOS = Path(__file__).resolve().parents[1] / "dados" / "planilhas"


def paginas(nome: str) -> list[str]:
    texto = (DADOS / f"{nome}.txt").read_text(encoding="utf-8")
    return [p for p in re.split(r"=====PAGINA \d+\n", texto) if p.strip()]


def _por_item(linhas: list[LinhaPlanilha]) -> dict[str | None, LinhaPlanilha]:
    return {linha.item: linha for linha in linhas}


@pytest.mark.parametrize(
    ("texto", "valor"),
    [("2.142,00", "2142.00"), ("1487,20", "1487.20"), ("6", "6"), ("R$ 1.088,69", "1088.69")],
)
def test_numero_br(texto: str, valor: str) -> None:
    assert numero_br(texto) == Decimal(valor)


@pytest.mark.parametrize("texto", ["1.2.3", "abc", "1,2,3", "", "1.23"])
def test_numero_br_recusa(texto: str) -> None:
    assert numero_br(texto) is None


def test_coerente_com_tolerancia() -> None:
    assert coerente(Decimal("2142"), Decimal("28.59"), Decimal("61239.78"))
    # 5 × 636,19 = 3.180,95; a planilha arredondou para 3.180,94.
    assert coerente(Decimal("5"), Decimal("636.19"), Decimal("3180.94"))
    assert not coerente(Decimal("2142"), Decimal("23.02"), Decimal("61239.78"))
    assert not coerente(Decimal("0"), Decimal("1"), Decimal("0"))


@pytest.mark.parametrize(
    ("token", "data"),
    [("ago/26", date(2026, 8, 1)), ("jul-26", date(2026, 7, 1)), ("07/2026", date(2026, 7, 1))],
)
def test_data_de_token(token: str, data: date) -> None:
    assert data_de_token(token) == data


def test_datas_do_cabecalho_por_fonte() -> None:
    assert datas_do_cabecalho(
        "SINAPI - 07/2026 SABESP - 01/2026 DATA 02/09/2026 SICRO-04/2026"
    ) == {
        "sinapi": date(2026, 7, 1),
        "sabesp": date(2026, 1, 1),
        "sicro": date(2026, 4, 1),
    }
    assert datas_do_cabecalho("(**) SINAPI ref. ago/26; SICRO ref. jul/26")["sicro"] == date(
        2026, 7, 1
    )
    assert datas_do_cabecalho("Adotada tabela de Preços SINAPI (07/2026), SBC (08/2026)") == {
        "sinapi": date(2026, 7, 1),
        "sbc": date(2026, 8, 1),
    }


def test_cosmopolis_codigo_antes_do_item_e_descricao_quebrada() -> None:
    linhas = ler_planilha(paginas("cosmopolis"))
    assert len(linhas) >= 30
    itens = _por_item(linhas)
    engenheiro = itens["1.1"]
    assert (engenheiro.fonte, engenheiro.codigo, engenheiro.descricao) == (
        "sinapi",
        "93568",
        "Engenheiro",
    )
    assert (engenheiro.preco_sem_bdi, engenheiro.preco_com_bdi) == (
        Decimal("34765.53"),
        Decimal("43171.84"),
    )
    # Cabeçalho "SINAPI - 07/2026": data-base da linha pela fonte.
    assert engenheiro.data_base == date(2026, 7, 1)
    assentamento = itens["4.4"]
    assert assentamento.descricao.startswith("ASSENTAMENTO DE TUBO DE PVC DEFOFO")
    assert assentamento.descricao.endswith("AF_05/2024")
    assert (assentamento.unidade, assentamento.quantidade) == ("m", Decimal("2142.00"))
    assert assentamento.preco_com_bdi == Decimal("28.59")
    assert assentamento.preco_sem_bdi == Decimal("23.02")
    assert assentamento.secao == "EXECUÇÃO ADUTORA"
    tubo = itens["4.5"]
    assert (tubo.fonte, tubo.descricao) == ("cotacao", "Fornecimento de Tubo Defofo - Dn 400mm")


def test_santa_barbara_bdi_da_linha_e_data_base_da_linha() -> None:
    linhas = ler_planilha(paginas("santa_barbara"))
    itens = _por_item(linhas)
    tubo = itens["4.1"]
    assert tubo.descricao == "TUBO PVC-O DN 250 MM PN12,5"
    assert (tubo.fonte, tubo.codigo) == ("sabesp", "HM07752")
    assert tubo.data_base == date(2026, 7, 1)
    assert (tubo.preco_sem_bdi, tubo.preco_com_bdi, tubo.total) == (
        Decimal("284.07"),
        Decimal("327.48"),
        Decimal("938760.72"),
    )
    assert tubo.bdi == Decimal("0.1528")
    assert tubo.secao == "FORNECIMENTO DE MATERIAIS"
    assert itens["3.9"].unidade == "M3 X KM"
    assert itens["1.1"].unidade == "UN.MES"
    # Descrição em várias linhas antes da linha do preço (código e fonte no começo dela).
    escavacao = itens["8.2"]
    assert escavacao.descricao.startswith("ESCAVAÇÃO MECANIZADA DE VALA COM PROF. MAIOR QUE 1,5 M")
    assert escavacao.descricao.endswith("AF_09/2024")
    assert (escavacao.fonte, escavacao.codigo) == ("sinapi", "90092")
    # Página de composições (insumos por hora) fica de fora.
    assert all(linha.unidade.upper() != "H" for linha in linhas)


def test_ribeirao_fonte_e_codigo_depois_da_descricao() -> None:
    linhas = ler_planilha(paginas("ribeirao"))
    itens = _por_item(linhas)
    tubo = itens["2.12"]
    assert tubo.descricao == "Tubo, PVC Ocre, DN 200 - fornec./assent."
    assert (tubo.fonte, tubo.codigo) == ("sinapi", "90702")
    assert (tubo.preco_sem_bdi, tubo.preco_com_bdi) == (Decimal("149.73"), Decimal("184.95"))
    assert tubo.secao == "Coletor"
    assert itens["2.9"].codigo == "CV00625"
    # "(**) SINAPI ref. ago/26; ... SABESP ref. jul/26" no rodapé.
    assert tubo.data_base == date(2026, 8, 1)
    assert itens["1.1"].data_base == date(2026, 7, 1)


def test_araraquara_memorial_entre_quantidade_e_precos() -> None:
    linhas = ler_planilha(paginas("araraquara"))
    itens = _por_item(linhas)
    escavacao = itens["2.5"]
    assert escavacao.descricao == "Escavação mecanizada de valas prof. até 2,20m largura 0,80m"
    assert (escavacao.unidade, escavacao.quantidade) == ("m³", Decimal("1487.20"))
    assert (escavacao.fonte, escavacao.codigo) == ("sinapi", "90084")
    assert escavacao.data_base == date(2026, 7, 1)
    assert escavacao.preco_com_bdi == Decimal("13.60")
    mobilizacao = itens["1.5"]
    assert mobilizacao.descricao.startswith("Mobilização e Desmobilização de canteiro")
    assert mobilizacao.secao == "SERVIÇOS PRELIMINARES"
    assert itens["2.6"].secao == "EXECUÇÃO DE COLETOR DE ESGOTOS"
    # Sem o unitário sem BDI ("-"): só o com BDI.
    mnd = itens["3.5"]
    assert (mnd.preco_sem_bdi, mnd.preco_com_bdi) == (None, Decimal("1474.15"))


def test_ibate_texto_picotado_nao_vira_linha() -> None:
    assert ler_planilha(paginas("ibate")) == []


def test_linha_sem_conta_que_feche_nao_entra() -> None:
    pagina = (
        "1.1 Tubo PVC DN 150 m 10,00 5,00 51,00\n"  # 10 × 5 ≠ 51
        "1.2 Tubo PVC DN 200 m 10,00 5,00 50,00\n"
    )
    assert [linha.item for linha in ler_planilha([pagina])] == ["1.2"]


def test_copia_repetida_conta_uma() -> None:
    pagina = "1.2 Tubo PVC DN 200 m 10,00 5,00 50,00\n"
    assert len(ler_planilha([pagina, pagina])) == 1


def test_pagina_de_composicao() -> None:
    assert pagina_de_composicao("COMPOSIÇÕES - AD CINTEC_META 6\nTOTAL DA COMPOSIÇÃO 1,80")
    assert not pagina_de_composicao("2.1 ADMINISTRAÇÃO LOCAL CP-01 COMPOSICAO ago/26 MÊS 6,00")


# --- BDI: só o plausível (5% a 45%) e coerente com o com e o sem ---


def _uma(linha: str) -> LinhaPlanilha:
    (lida,) = ler_planilha([linha])
    return lida


def test_peso_da_curva_abc_nao_e_bdi() -> None:
    lida = _uma("1.1 Tubo PVC DN 150 m 10,00 90,00 112,50 1.125,00 0,35%")
    assert (lida.preco_sem_bdi, lida.preco_com_bdi, lida.bdi) == (
        Decimal("90.00"),
        Decimal("112.50"),
        None,
    )


def test_bdi_da_linha_que_nao_confere_com_sem_e_com_e_ignorado() -> None:
    assert _uma("1.1 Tubo PVC DN 150 m 10,00 90,00 112,50 1.125,00 30,00%").bdi is None
    assert _uma("1.1 Tubo PVC DN 150 m 10,00 90,00 112,50 1.125,00 25,00%").bdi == Decimal("0.25")


def test_material_mais_mao_de_obra_nao_e_sem_bdi() -> None:
    # Material 30 + mão de obra 70 = unitário 100: nenhum dos dois é o "sem BDI".
    assert _uma("1.1 Tubo PVC DN 150 m 10,00 30,00 70,00 100,00 1.000,00").preco_sem_bdi is None
    com_totais = _uma("1.1 Tubo PVC DN 150 m 10,00 30,00 300,00 70,00 700,00 100,00 1.000,00")
    assert (com_totais.preco_sem_bdi, com_totais.preco_com_bdi) == (None, Decimal("100.00"))


def test_razao_fora_da_faixa_nao_e_sem_bdi() -> None:
    assert _uma("1.1 Tubo PVC DN 150 m 10,00 50,00 112,50 1.125,00").preco_sem_bdi is None


def test_coluna_zero_antes_do_preco_nao_divide_por_zero() -> None:
    """Regressão: "0,00" (mão de obra, desoneração) antes do preço fazia com ÷ sem estourar
    DivisionByZero e derrubava o edital inteiro."""
    lida = _uma("1.1 ASSENTAMENTO DE TUBO PVC DN 100 m 10,00 0,00 50,00 500,00")
    assert (lida.preco_sem_bdi, lida.preco_com_bdi, lida.bdi) == (None, Decimal("50.00"), None)


@pytest.mark.parametrize(
    "linha",
    [
        "1.1 Tubo m 10,00 0,00 0,00 0,00",
        "1.1 Tubo m 0,00 0,00 50,00 500,00",
        "1.1 Tubo m 10,00 0,00 0,00 50,00 500,00 0,00%",
        "1.1 Tubo m 10,00 -5,00 50,00 500,00",
        "1.1 Tubo m -10,00 50,00 -500,00",
        "1.1 Tubo m 10,00",
        "1.1 Tubo m 10,00 500,00",
        "m 1,00 1,00 1,00 1,00 1,00 1,00 1,00 1,00",
        "1.1 Tubo m 10,00 50,00 500,00 0%",
        "1.1 Tubo m 10,00 50,00 500,00 999,99%",
        "1.1 Tubo m 0,001 0,00 0,00",
        "- - - - -",
        "0,00 0,00 0,00",
        "12",
    ],
)
def test_ler_planilha_nunca_levanta_em_linha_numerica_estranha(linha: str) -> None:
    for lida in ler_planilha([linha, f"2.1 Rede de esgoto PVC DN 150 {linha}"]):
        assert lida.preco_com_bdi > 0 and lida.total > 0
        assert lida.preco_sem_bdi is None or lida.preco_sem_bdi > 0


def test_data_de_token_com_ano_absurdo_devolve_none_sem_levantar() -> None:
    """ "01/0000" (ou um data_base inventado pela IA) levantava ValueError em `date(0, 1, 1)` e
    derrubava a planilha inteira."""
    for token in ("01/0000", "12/0001", "07/9999", "jan/00x"):
        assert data_de_token(token) is None
    assert data_de_token("07/2026") == date(2026, 7, 1)
    assert data_de_token("ago/26") == date(2026, 8, 1)


def test_dn_nao_casa_com_de_dentro_de_palavra() -> None:
    from radar.precos.planilha_leitura import _dn_no_texto

    assert not _dn_no_texto(100, "assentamentoderede100,00m")  # "rede100" não é "DE 100"
    assert _dn_no_texto(110, "tubopvcde=110mm")
    assert _dn_no_texto(100, "tubopvcdn100mm")


# --- Descrição em várias linhas com item inteiro (editais do PNCP de 2026-11-27) ---

# PNCP 76995422000106-1-000127/2026: o item sozinho na linha, a descrição embaixo e a unidade
# "Metros" na linha do preço. Antes só a última linha da descrição ("de abastecimento") ficava.
RURAL = """LOTE 02 - ADUTORAS EM PEAD E INTERLIGAÇÕES HIDRÁULICAS ENTRE POÇO E
Item Descrição
1
Tubo PEAD PE80 DN 50 mm PN
12,5, próprio para adução de
água potável em sistemas rurais
de abastecimento
Metros 1000  R$        28,19  R$ 28.190,00
3
União PEAD DN 50 mm para
interligação de adutora de água
potável
Und 20  R$      139,72  R$ 2.794,40
19 Hidrometro medidor de vazão com capacidade de 16m3 h. Un 8  R$   1.216,92  R$ 9.735,36
"""

# PNCP 25269069000146-1-000045/2026: "item código DESCRIÇÃO", a descrição continua nas linhas de
# baixo (uma delas começa com número: "75 GRAMAS.") e a unidade por extenso.
HIDRAULICO = """Item Código Descrição Unidade Quant. Valor
6 63642 ADESIVO BISNAGA.
ADESIVO PLÁSTICO PARA COLAR PVC , BISNAGA COM
75 GRAMAS.
TUBO 1500 R$ 8,30 R$ 12.450,00
76 82147 TUBO EM FERRO FUNDIDO DÚCTIL PONTA/BOLSA, K7,
DN 400 MM, CONFORME NBR 7675, PARA ÁGUA BRUTA
OU TRATADA.
METRO 24 R$ 1.402,42 R$ 33.658,08
8 81802 BRAÇADEIRA EM AÇO COM PARAFUSOS PARA FIXAÇÃO
DE TUBULAÇÃO DE FERRO FUNDIDO, DN 300 MM.
UNIDADE 55 R$ 940,00 R$ 51.700,00
"""


def test_item_inteiro_sozinho_e_descricao_nas_linhas_de_baixo() -> None:
    itens = _por_item(ler_planilha([RURAL]))
    assert set(itens) == {"1", "3", "19"}
    tubo = itens["1"]
    assert tubo.descricao == (
        "Tubo PEAD PE80 DN 50 mm PN 12,5, próprio para adução de água potável em sistemas "
        "rurais de abastecimento"
    )
    assert (tubo.unidade, tubo.quantidade, tubo.preco_com_bdi) == (
        "Metros",
        Decimal(1000),
        Decimal("28.19"),
    )
    assert itens["3"].descricao.startswith("União PEAD DN 50 mm")
    assert itens["19"].descricao.startswith("Hidrometro medidor de vazão")


def test_item_inteiro_com_codigo_e_unidade_por_extenso() -> None:
    itens = _por_item(ler_planilha([HIDRAULICO]))
    tubo = itens["76"]
    assert tubo.codigo == "82147"
    assert tubo.descricao == (
        "TUBO EM FERRO FUNDIDO DÚCTIL PONTA/BOLSA, K7, DN 400 MM, CONFORME NBR 7675, PARA "
        "ÁGUA BRUTA OU TRATADA."
    )
    assert (tubo.unidade, tubo.quantidade) == ("METRO", Decimal(24))
    assert itens["8"].unidade == "UNIDADE"
    assert itens["8"].descricao.startswith("BRAÇADEIRA EM AÇO")
    # "TUBO" não é unidade conhecida: a linha do adesivo fica de fora (a conta não basta).
    assert "6" not in itens


# --- Revisão: o item da própria linha, preço não lido, barra, seção e número de página ---

# PNCP 76995422000106-1-000127/2026: a linha de preço do 18 tem unidade desconhecida
# ("Serviço") e não é lida; o 19 traz o item na própria linha e não herda a descrição do 18.
MOTOBOMBA_E_HIDROMETRO = """18
Serviço de instalação e retirada
completa de conjunto
motobomba submerso em poço
tubular profundo, incluindo
montagem da coluna de
recalque, instalação elétrica
interna, conexões hidráulicas,
fixações e testes operacionais
Serviço 15  R$   2.283,33  R$ 34.249,95
19 Hidrometro medidor de vazão com capacidade de 16m3 h. Un 8  R$   1.216,92  R$ 9.735,36
20 Cabo P. P. 2 x 2,5mm para automação de sistema. Mt 900  R$          9,54  R$ 8.586,00
"""


def test_item_na_propria_linha_nao_herda_o_item_anterior() -> None:
    itens = _por_item(ler_planilha([MOTOBOMBA_E_HIDROMETRO]))
    assert set(itens) == {"19", "20"}
    assert itens["19"].descricao == "Hidrometro medidor de vazão com capacidade de 16m3 h."
    assert itens["19"].preco_com_bdi == Decimal("1216.92")


def test_preco_nao_lido_fecha_o_item() -> None:
    """Sem o item na linha do preço seguinte, a descrição do 18 também não passa adiante."""
    pagina = MOTOBOMBA_E_HIDROMETRO.replace(
        "19 Hidrometro medidor de vazão com capacidade de 16m3 h. Un 8",
        "Hidrometro medidor de vazão com capacidade de 16m3 h.\nUn 8",
    )
    lidas = ler_planilha([pagina])
    assert [linha.descricao for linha in lidas][0] == (
        "Hidrometro medidor de vazão com capacidade de 16m3 h."
    )
    assert all("motobomba" not in linha.descricao for linha in lidas)


def test_comprimento_da_barra_nao_e_unidade() -> None:
    pagina = (
        "1.1 TUBO PVC PBA DN 100 BARRA DE 6 METROS 50 R$ 300,00 R$ 15.000,00\n"
        "1.2 TUBO PVC PBA DN 100 METROS 50 R$ 300,00 R$ 15.000,00\n"
    )
    assert [linha.item for linha in ler_planilha([pagina])] == ["1.2"]


@pytest.mark.parametrize(
    "continuacao",
    [
        # PNCP 25269069000146-1-000045/2026: viravam título de seção.
        "75 GRAMAS.",
        "2 E 15813-1.",
        "08 MM,UTILIZADA NA MANUTENÇÃO DAS REDES",
        "50 MM PARA REDE DE ESGOTO",
        "20 MM PARA RAMAL PREDIAL",
    ],
)
def test_continuacao_com_numero_nao_e_secao(continuacao: str) -> None:
    pagina = f"{continuacao}\n1.1 TUBO PVC DN 150 m 10,00 5,00 50,00\n"
    (linha,) = ler_planilha([pagina])
    assert linha.secao is None


@pytest.mark.parametrize(
    ("titulo", "secao"),
    [
        ("1.0 Seviços Preliminares", "Seviços Preliminares"),
        ("4.0 EXECUÇÃO ADUTORA 2.578.505,98", "EXECUÇÃO ADUTORA"),
        ("2 EXECUÇÃO DE COLETOR DE ESGOTOS", "EXECUÇÃO DE COLETOR DE ESGOTOS"),
        ("4.0 Ramal", "Ramal"),
    ],
)
def test_secao_de_verdade_continua(titulo: str, secao: str) -> None:
    (linha,) = ler_planilha([f"{titulo}\n1.1 TUBO PVC DN 150 m 10,00 5,00 50,00\n"])
    assert linha.secao == secao


def test_numero_de_pagina_no_topo_nao_abre_item() -> None:
    """PNCP 76995422000106-1-000127/2026: a página começa com "35" e o resto do item 15."""
    pagina = "35\ncircuito, acionamento\nautomático/manual\nUnd 2  R$   2.173,33  R$ 4.346,66\n"
    (linha,) = ler_planilha([pagina])
    assert linha.item is None
    assert linha.descricao == "automático/manual"


# --- Revisão 2: número de continuação, seção com traço, código na linha, topo de página ---


@pytest.mark.parametrize(
    ("pagina", "item", "descricao"),
    [
        (
            "1.1 TUBO PVC PBA JE DN\n100 MM PARA REDE DE AGUA m 10,00 5,00 50,00\n",
            "1.1",
            "TUBO PVC PBA JE DN 100 MM PARA REDE DE AGUA",
        ),
        (
            "1.2 FORNECIMENTO E ASSENTAMENTO DE TUBO PEAD\n110 MM m 10,00 5,00 50,00\n",
            "1.2",
            "FORNECIMENTO E ASSENTAMENTO DE TUBO PEAD 110 MM",
        ),
    ],
)
def test_numero_no_comeco_da_continuacao_nao_e_item(pagina: str, item: str, descricao: str) -> None:
    (linha,) = ler_planilha([pagina])
    assert (linha.item, linha.descricao) == (item, descricao)


def test_item_seguinte_ao_aberto_e_da_propria_linha() -> None:
    """ "18" aberto e sem preço lido; "19 ..." é o item seguinte, não continuação do 18."""
    pagina = (
        "18\nServiço de instalação de conjunto motobomba\n"
        "19 Hidrometro medidor de vazão com capacidade de 16m3 h. Un 8  R$   1.216,92  "
        "R$ 9.735,36\n"
    )
    (linha,) = ler_planilha([pagina])
    assert (linha.item, linha.descricao) == (
        "19",
        "Hidrometro medidor de vazão com capacidade de 16m3 h.",
    )


@pytest.mark.parametrize(
    ("titulo", "secao"),
    [
        ("1 - SERVIÇOS PRELIMINARES", "SERVIÇOS PRELIMINARES"),
        ("4 – RAMAIS PREDIAIS", "RAMAIS PREDIAIS"),
        ("4. RAMAL.", "RAMAL"),
    ],
)
def test_secao_com_traco_ou_ponto(titulo: str, secao: str) -> None:
    (linha,) = ler_planilha([f"{titulo}\n4.1 TUBO PVC DN 100 m 10,00 5,00 50,00\n"])
    assert linha.secao == secao


def test_codigo_depois_do_item_inteiro_na_mesma_linha() -> None:
    """PNCP 25269069000146-1-000045/2026, item 45."""
    (linha,) = ler_planilha(["45 81797 PARAFUSO COM PORCA UNIDADE 96 R$ 19,72 R$ 1.893,12\n"])
    assert (linha.item, linha.codigo, linha.descricao) == ("45", "81797", "PARAFUSO COM PORCA")


def test_item_sozinho_no_topo_da_pagina_continua_item() -> None:
    pagina = "12\nTUBO PVC PBA JE\nDN 100 MM PARA AGUA\nm 10,00 5,00 50,00\n"
    (linha,) = ler_planilha([pagina])
    assert (linha.item, linha.descricao) == ("12", "TUBO PVC PBA JE DN 100 MM PARA AGUA")


def test_metros_como_unidade_continua() -> None:
    pagina = (
        "1.1 CABO PP 3X10 METROS 900 R$ 9,54 R$ 8.586,00\n"
        "1.2 TUBO PEAD DN 50 METROS 100 R$ 10,00 R$ 1.000,00\n"
    )
    assert [(lin.item, lin.unidade) for lin in ler_planilha([pagina])] == [
        ("1.1", "METROS"),
        ("1.2", "METROS"),
    ]
