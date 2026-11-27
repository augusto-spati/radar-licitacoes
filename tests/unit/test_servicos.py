"""Serviços-padrão: descrições reais da pesquisa (TCE-PE, planilhas do PNCP) e as que não casam."""

from decimal import Decimal
from pathlib import Path

import pytest

from radar.precos import (
    Classificacao,
    Escopo,
    carregar_servicos,
    carregar_termos_processo,
    classificar,
    escopo_de_rede,
    extrair_dn,
    faixa_profundidade,
    normalizar_unidade,
    sem_acento,
    tem_termo,
)

FFFD = "�"


def test_config_tem_os_oito_servicos_do_spec() -> None:
    servicos = carregar_servicos()
    assert {s.nome: (s.linha, s.unidade) for s in servicos} == {
        "rede_agua": ("redes", "m"),
        "rede_esgoto": ("redes", "m"),
        "ligacao_agua": ("redes", "un"),
        "ligacao_esgoto": ("redes", "un"),
        "escavacao_vala": ("redes", "m3"),
        "recomposicao_asfalto": ("redes", "m2"),
        "hidrometro": ("perdas", "un"),
        "pesquisa_vazamento": ("perdas", "km"),
    }
    redes = {s.nome: s for s in servicos if s.de_rede}
    assert set(redes) == {"rede_agua", "rede_esgoto"}
    assert all((s.dn_min, s.dn_max) == (50, 600) for s in redes.values())
    assert [m.nome for m in redes["rede_agua"].materiais] == ["pvc", "pead", "fofo"]


def test_config_sem_fator_1_na_canonica_e_recusada(tmp_path: Path) -> None:
    caminho = tmp_path / "servicos.toml"
    caminho.write_text(
        '[[servico]]\nnome = "x"\nrotulo = "X"\nlinha = "redes"\nunidade = "m"\n'
        'termos_obrigatorios = ["x"]\n[servico.unidades_aceitas]\nkm = "1000"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="fator 1"):
        carregar_servicos(caminho)


@pytest.mark.parametrize(
    ("bruta", "esperada"),
    [("M", "m"), ("m", "m"), ("M3", "m3"), ("m³", "m3"), ("m²", "m2"), ("KM", "km")]
    + [("UN", "un"), ("und", "und"), ("Unid.", "unid"), ("M L", "ml"), ("pç", "pc")],
)
def test_normalizar_unidade(bruta: str, esperada: str) -> None:
    assert normalizar_unidade(bruta) == esperada


def test_curinga_vale_por_uma_letra() -> None:
    assert tem_termo(sem_acento(f"REDE DE {FFFD}GUA"), "agua")
    assert tem_termo(sem_acento(f"LIGA{FFFD}{FFFD}O PREDIAL"), "ligacao predial")
    assert tem_termo(sem_acento("pavimento asfáltico"), "asfalt")
    assert not tem_termo(sem_acento("RELIGACAO PREDIAL"), "ligacao predial")
    assert not tem_termo(sem_acento(f"{FFFD}GUAS"), "esgoto")


@pytest.mark.parametrize(
    ("descricao", "dn"),
    [
        ("TUBO PVC DN 150", 150),
        ("TUBO PVC DN150 JE", 150),
        ("TUBO PEAD DN=200 PN 10", 200),
        ("TUBO DE FERRO FUNDIDO Ø 300MM", 300),
        ("TUBO PVC DEFOFO 150 MM", 150),
        # PEAD: o número é o diâmetro externo; fica em `dn` e só se compara entre PEAD.
        ("TUBO PEAD PE 100 DE 110 MM", 110),
        ("TUBO PVC PBA JE 6,0 MPA DN 100 / 110 MM", 100),
        ("REDE DE AGUA DIAMETRO NOMINAL DE 250 MM", 250),
        ("TUBO PVC DN 100 A DN 150", None),
        ("TUBO PVC 1,25 MM", None),
        ("PAVIMENTO ESPESSURA 50 MM", None),
        ("TUBO PVC JE", None),
    ],
)
def test_extrair_dn(descricao: str, dn: int | None) -> None:
    assert extrair_dn(sem_acento(descricao)) == dn


def _rede(servico: str, material: str, dn: int, escopo: Escopo, fator: str = "1") -> Classificacao:
    return Classificacao(servico, material, dn, escopo, "m", Decimal(fator))


@pytest.mark.parametrize(
    ("descricao", "unidade", "esperado"),
    [
        (
            f"TUBO PVC P/ REDE DE {FFFD}GUA DN 150",
            "M",
            _rede("rede_agua", "pvc", 150, "material"),
        ),
        (
            "TUBO PVC P/ REDE DE ÁGUA DN 150",
            "m",
            _rede("rede_agua", "pvc", 150, "material"),
        ),
        (
            "FORNECIMENTO DE TUBO PVC PBA JE P/ REDE DE AGUA DN 100",
            "M",
            _rede("rede_agua", "pvc", 100, "material"),
        ),
        (
            "FORNECIMENTO E ASSENTAMENTO DE TUBO PVC DEFOFO JE PARA REDE DE AGUA DN 200",
            "m",
            _rede("rede_agua", "pvc", 200, "completo"),
        ),
        (
            "ASSENTAMENTO DE TUBO DE PVC DEFOFO P/ AGUA, DN 300",
            "M",
            _rede("rede_agua", "pvc", 300, "assentamento"),
        ),
        (
            f"ASSENTAMENTO DE TUBO DE FERRO FUNDIDO P/ REDE DE {FFFD}GUA DN 400",
            "M",
            _rede("rede_agua", "fofo", 400, "assentamento"),
        ),
        (
            "INSTALACAO DE REDE DE AGUA EM PEAD DN 90",
            "m",
            _rede("rede_agua", "pead", 90, "assentamento"),
        ),
        (
            "ASSENTAMENTO DE TUBO PVC P/ REDE DE AGUA DN 150, SEM FORNECIMENTO DO TUBO",
            "M",
            _rede("rede_agua", "pvc", 150, "assentamento"),
        ),
        (
            "EXECUCAO DE REDE DE AGUA EM TUBO PEAD PE 100 DN 110, INCLUSIVE FORNECIMENTO",
            "KM",
            _rede("rede_agua", "pead", 110, "completo", "1000"),
        ),
        (
            "REDE DE AGUA PVC DN 150, MAO DE OBRA",
            "M",
            _rede("rede_agua", "pvc", 150, "assentamento"),
        ),
        (
            "TUBO PVC OCRE JEI P/ REDE COLETORA DE ESGOTO DN 150",
            "M",
            _rede("rede_esgoto", "pvc", 150, "material"),
        ),
        (
            "LIGACAO PREDIAL DE AGUA EM PEAD DN 20, INCLUSIVE HIDROMETRO",
            "UN",
            Classificacao("ligacao_agua", None, None, None, "un", Decimal(1)),
        ),
        (
            f"LIGA{FFFD}{FFFD}O PREDIAL DE {FFFD}GUA",
            "und",
            Classificacao("ligacao_agua", None, None, None, "un", Decimal(1)),
        ),
        (
            "RAMAL PREDIAL DE ESGOTO EM PVC DN 100",
            "un",
            Classificacao("ligacao_esgoto", None, None, None, "un", Decimal(1)),
        ),
        (
            "ESCAVACAO MECANICA DE VALA EM MATERIAL DE 1A CATEGORIA ATE 1,50 M",
            "M3",
            Classificacao("escavacao_vala", None, None, None, "m3", Decimal(1), "ate_1_5m"),
        ),
        (
            f"ESCAVA{FFFD}{FFFD}O MEC{FFFD}NICA DE VALA AT{FFFD} 1,5 M",
            "m³",
            Classificacao("escavacao_vala", None, None, None, "m3", Decimal(1), "ate_1_5m"),
        ),
        (
            "ESCAVAÇÃO MECANIZADA DE VALA COM PROF. MAIOR QUE 1,5 M ATÉ 3,0 M (MÉDIA MONTANTE E "
            "JUSANTE/UMA COMPOSIÇÃO POR TRECHO), RETROESCAV. (0,26 M3), LARG. DE 0,8 M A 1,5 M",
            "M3",
            Classificacao("escavacao_vala", None, None, None, "m3", Decimal(1), "1_5_a_3m"),
        ),
        (
            "RECOMPOSICAO DE PAVIMENTO ASFALTICO EM CBUQ E=5CM",
            "m²",
            Classificacao("recomposicao_asfalto", None, None, None, "m2", Decimal(1)),
        ),
        (
            "HIDROMETRO UNIJATO 1,5 M3/H - FORNECIMENTO E INSTALACAO",
            "UN",
            Classificacao("hidrometro", None, None, None, "un", Decimal(1)),
        ),
        (
            f"SUBSTITUI{FFFD}{FFFD}O DE HIDR{FFFD}METRO",
            "und",
            Classificacao("hidrometro", None, None, None, "un", Decimal(1)),
        ),
        (
            f"PESQUISA DE VAZAMENTO N{FFFD}O VIS{FFFD}VEL COM GEOFONE",
            "KM",
            Classificacao("pesquisa_vazamento", None, None, None, "km", Decimal(1)),
        ),
        (
            "PESQUISA DE VAZAMENTO NAO VISIVEL",
            "m",
            Classificacao("pesquisa_vazamento", None, None, None, "km", Decimal("0.001")),
        ),
    ],
)
def test_classifica_descricoes_reais(descricao: str, unidade: str, esperado: Classificacao) -> None:
    assert classificar(descricao, unidade) == esperado


@pytest.mark.parametrize(
    ("descricao", "unidade"),
    [
        # Mão de obra e planilha da UNESP / Itaú de Minas (seção 5.3).
        ("1.1 SINAPI 90777 ENGENHEIRO CIVIL DE OBRA JUNIOR COM ENCARGOS COMPLEMENTARES", "h"),
        ("01.01.01 CPU 001 ENGENHEIRO CIVIL", "MÊS"),
        ("PEDREIRO COM ENCARGOS COMPLEMENTARES", "h"),
        # Material e conexão: a curva em FoFo DN 400 é peça, não metro de rede.
        ("Curva 90°, em FoFo, JE DN 400", "Peça"),
        ("CURVA 90 PVC PARA REDE DE AGUA DN 150", "M"),
        ("HIDROMETRO UNIJATO 1,5 M3/H", "UN"),
        ("CIMENTO PORTLAND CP II-32", "KG"),
        # Escavação manual custa várias vezes a mecânica (Compesa, manutenção de esgoto).
        ("escavação manual de valas", "m3"),
        ("ESCAVACAO MECANICA DE VALA EM ROCHA", "M3"),
        # Unidade errada para o serviço: comparar cru seria m com m³.
        ("ESCAVACAO MECANICA DE VALA", "M"),
        ("RECOMPOSICAO DE PAVIMENTO ASFALTICO", f"m{FFFD}"),
        ("LIGACAO PREDIAL DE AGUA", "M"),
        ("TUBO PVC P/ REDE DE AGUA DN 150", "UN"),
        # Rede sem DN, sem material ou com DN fora da faixa.
        ("TUBO PVC P/ REDE DE AGUA", "M"),
        ("REDE DE AGUA DN 150", "M"),
        ("TUBO PEAD P/ REDE DE AGUA DN 20", "M"),
        ("TUBO PVC P/ REDE DE AGUA DN 100 A DN 150", "M"),
        # Serviço auxiliar que cita rede de água.
        ("ESCORAMENTO DE VALA P/ REDE DE AGUA PVC DN 150", "M"),
        ("TESTE HIDROSTATICO DE REDE DE AGUA PVC DN 150", "M"),
        ("TUBO PVC SERIE NORMAL ESGOTO PREDIAL DN 100", "M"),
        ("LEITURA DE HIDROMETRO", "UN"),
        # Rede sem escopo claro (nem tubo, nem verbo de serviço).
        ("REDE COLETORA DE ESGOTO PVC DN 150", "M"),
        # Logística e remanejamento não são o metro de rede nova nem a escavação.
        ("TRANSPORTE DE TUBO PVC DN 150 PARA REDE DE AGUA", "M"),
        ("REMANEJAMENTO DE REDE DE AGUA PVC DN 100", "M"),
        ("CARGA, MANOBRA E DESCARGA DE TUBO PVC P/ REDE DE ESGOTO DN 150", "M"),
        ("CARGA, MANOBRA E DESCARGA DE MATERIAL DE ESCAVACAO DE VALA", "M3"),
        ("CARGA E TRANSPORTE DE MATERIAL DE ESCAVACAO DE VALA", "M3"),
        # Com verbo de serviço o escopo seria claro: quem barra é a exclusão.
        ("ASSENTAMENTO E TRANSPORTE DE TUBO PVC DN 150 P/ REDE DE AGUA", "M"),
        ("REMANEJAMENTO DE REDE DE AGUA PVC DN 100, INCLUSIVE ASSENTAMENTO", "M"),
        ("CARGA, MANOBRA E DESCARGA DE TUBO PVC P/ REDE DE ESGOTO DN 150, MAO DE OBRA", "M"),
        ("ESCAVACAO MECANICA DE VALA, INCLUSIVE CARGA", "M3"),
        # Fix round 1: falsos positivos vistos no TCE-PE (2026-11-27).
        ("CONSERTO DE RAMAIS PREDIAIS DE ÁGUA DANIFICADOS DURANTE EXECUÇÃO DAS OBRAS.", "UN"),
        ("CONSERTO DE RAMAIS PREDIAIS DE ESGOTO DANIFICADOS DURANTE EXECUÇÃO DAS OBRAS.", "UN"),
        ("REPARO DE LIGACAO PREDIAL DE AGUA", "UN"),
        ("MANUTENÇÃO DE RAMAL PREDIAL DE ESGOTO", "UN"),
        ("INSTALAÇÃO OU SUBSTITUIÇÃO DE RAMAL PREDIAL DE ESGOTO ATÉ 5,00 M SEM PAVIMENTO.", "UN"),
        (
            "TUBO DE POLIETILENO DE ALTA DENSIDADE, PEAD, PE-80, DE= 315 MM X 28,7 MM PAREDE, "
            "(SDR 11 - PN 12,5) PARA REDE DE AGUA OU ESGOTO",
            "M",
        ),
        ("FORNECIMENTO E ASSENTAMENTO DE TUBO PVC DN 150 PARA REDE DE ÁGUA E ESGOTO", "M"),
        ("Escavação mecânica, reaterro e compactação vala material 1ª categoria ate 1,5 m", "M3"),
        ("ESCAVACAO MECANICA DE VALA ATE 1,5 M, INCLUSIVE COMPACTACAO DO FUNDO", "M3"),
        # Escavação sem faixa de profundidade, ou com faixa que atravessa um limite.
        (f"ESCAVA{FFFD}{FFFD}O MEC{FFFD}NICA DE VALA", "m³"),
        ("ESCAVACAO MECANICA DE VALA ATE 3,0 M", "M3"),
        ("ESCAVACAO MECANICA DE VALA ACIMA DE 1,5 M", "M3"),
        ("ESCAVACAO MECANICA DE VALA PROF. ATE 7,0 M", "M3"),
        ("RELIGACAO DE AGUA", "UN"),
    ],
)
def test_nao_casa(descricao: str, unidade: str) -> None:
    assert classificar(descricao, unidade) is None


@pytest.mark.parametrize(
    ("descricao", "escopo"),
    [
        ("TUBO PVC P/ REDE DE AGUA DN 150", "material"),
        ("FORNECIMENTO DE TUBO PEAD DN 110", "material"),
        ("AQUISICAO DE TUBO DE FERRO FUNDIDO DN 300", "material"),
        ("ASSENTAMENTO DE TUBO PVC DN 150", "assentamento"),
        ("INSTALACAO DE REDE DE AGUA DN 100", "assentamento"),
        ("FORNECIMENTO E ASSENTAMENTO DE TUBO PVC DN 150", "completo"),
        ("EXECUCAO DE REDE DE AGUA DN 100 INCLUSIVE FORNECIMENTO", "completo"),
        ("ASSENTAMENTO DE TUBO PVC DN 150 EXCLUSIVE FORNECIMENTO", "assentamento"),
        (f"FORNECIMENTO E INSTALA{FFFD}{FFFD}O DE TUBO PVC DN 150", "completo"),
        ("REDE DE AGUA PVC DN 150, MAO DE OBRA", "assentamento"),
        # Sem verbo nem "TUBO" no começo: não dá para saber se inclui o tubo.
        ("REDE COLETORA DE ESGOTO PVC DN 150", None),
        ("REDE DE AGUA EM TUBO PEAD DN 110", None),
    ],
)
def test_escopo_de_rede(descricao: str, escopo: str | None) -> None:
    assert escopo_de_rede(sem_acento(descricao)) == escopo


@pytest.mark.parametrize(
    ("descricao", "faixa"),
    [
        # Descrições reais do TCE-PE (Compesa, Pedra, Palmares; 2026).
        (
            "ESCAVAÇÃO MECANIZADA DE VALA COM PROF. ATÉ 1,5 M (MÉDIA MONTANTE E JUSANTE/UMA "
            "COMPOSIÇÃO POR TRECHO), RETROESCAV. (0,26 M3), LARG. DE 0,8 M A 1,5 M, EM SOLO DE",
            "ate_1_5m",
        ),
        (
            "ESCAVAÇÃO MECANIZADA DE VALA COM PROFUNDIDADE ATÉ 1,5\nM (MÉDIA ENTRE MONTANTE E "
            "JUSANTE/UMA COMPOSIÇÃO POR\nTRECHO) COM RETROESCAVADEIRA (CAPACIDADE DA CAÇAMBA DA "
            "RETRO: 0,26 M³ / POTÊNCIA: 88\nHP), LARGURA MENOR QUE 0,8 M",
            "ate_1_5m",
        ),
        (
            "ESCAVAÇÃO MANUAL DE VALA COM PROFUNDIDADE MENOR OU IGUAL A 1,30 M. AF_02/2021",
            "ate_1_5m",
        ),
        (
            "ESCAVAÇÃO MECANIZADA DE VALA COM PROF. MAIOR QUE 1,50 M ATÉ 3,0 M (MÉDIA MONTANTE E "
            "JUSANTE/UMA COMPOSIÇÃO POR TRECHO)",
            "1_5_a_3m",
        ),
        (
            "ESCAVAÇÃO MANUAL DE VALAS EM MATERIAL DE 1ª E/OU 2ª CATEGORIAS ACIMA DE 1,50 M E ATÉ "
            "3,00 M DE PROFUNDIDADE",
            "1_5_a_3m",
        ),
        ("ESCAVACAO DE VALA 1,5 A 3,0 M", "1_5_a_3m"),
        (
            "ESCAVAÇÃO MECANIZADA DE VALA COM PROF. DE 3,0 M ATÉ 4,5 M(MÉDIA MONTANTE E "
            "JUSANTE/UMA COMPOSIÇÃO POR TRECHO), ESCAVADEIRA",
            "3_a_6m",
        ),
        (
            "ESCAVAÇÃO MECANIZADA DE VALA COM PROF. MAIOR QUE 4,5 M ATÉ 6,0 M (MÉDIA MONTANTE E "
            "JUSANTE/UMA COMPOSIÇÃO POR TRECHO)",
            "3_a_6m",
        ),
        ("ESCAVACAO DE VALA ACIMA DE 3,00 M", "3_a_6m"),
        # Sem profundidade, só a largura, faixa que atravessa um limite ou duas faixas.
        ("ESCAVACAO MECANICA DE VALA", None),
        ("ESCAVACAO DE VALA, LARG. DE 0,8 M A 1,5 M", None),
        ("ESCAVACAO DE VALA ATE 3,0 M", None),
        ("ESCAVACAO DE VALA ACIMA DE 1,5 M", None),
        ("ESCAVACAO DE VALA ATE 1,5 M E DE 1,5 A 3,0 M", None),
        ("RETROESCAVADEIRA (0,26 M3)", None),
    ],
)
def test_faixa_profundidade(descricao: str, faixa: str | None) -> None:
    assert faixa_profundidade(sem_acento(descricao)) == faixa


def test_termos_de_processo_na_config() -> None:
    termos = carregar_termos_processo()
    assert {"esgotamento sanitario", "rede coletora", "rede de agua"} <= set(termos)
    assert all(t == sem_acento(t) for t in termos)
