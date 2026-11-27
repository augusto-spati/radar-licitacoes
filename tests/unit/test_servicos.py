"""Serviços-padrão: descrições reais da pesquisa (TCE-PE, planilhas do PNCP) e as que não casam."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from radar.precos import (
    Classificacao,
    Contexto,
    Escopo,
    carregar_servicos,
    carregar_termos_processo,
    classificar,
    contexto_da_obra,
    escopo_de_rede,
    extrair_dn,
    faixa_profundidade,
    normalizar_unidade,
    secao_de_ramal,
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
        # SINAPI: "(NÃO INCLUI O FORNECIMENTO)" (Ibaté, 7b.2) era lido como completo.
        (
            "ASSENTAMENTO DE TUBO DE FERRO FUNDIDO DN 150 (NÃO INCLUI O FORNECIMENTO)",
            "assentamento",
        ),
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


# --- 7b.2: planilhas do PNCP (descrições reais do spike de 2026-11-27) ---

AGUA_ADUTORA = Contexto("agua", adutora=True)
ESGOTO = Contexto("esgoto")


@pytest.mark.parametrize(
    ("textos", "esperado"),
    [
        (("EXECUÇÃO DE COLETOR DE ESGOTOS", "obra"), Contexto("esgoto")),
        (("FORNECIMENTO DE MATERIAIS", "Substituição da adutora de água tratada."), AGUA_ADUTORA),
        (("EXECUÇÃO ADUTORA", None), AGUA_ADUTORA),
        (("Coletor", None), ESGOTO),
        ((None, "Melhorias no sistema de abastecimento de água"), Contexto("agua")),
        # Cita os dois: não decide.
        (("rede adutora de água ... saída para consumo, esgotamento e registros",), Contexto(None)),
        (("CANTEIRO DE OBRAS", "Pavimentação"), Contexto(None)),
    ],
)
def test_contexto_da_obra(textos: tuple[str | None, ...], esperado: Contexto) -> None:
    assert contexto_da_obra(*textos) == esperado


@pytest.mark.parametrize(
    ("descricao", "unidade", "contexto", "esperado"),
    [
        (
            "TUBO PVC-O DN 250 MM PN12,5",
            "M",
            AGUA_ADUTORA,
            replace(_rede("rede_agua", "pvc", 250, "material"), observacao="adutora"),
        ),
        (
            "Tubo, PVC Ocre, DN 200 - fornec./assent.",
            "m",
            ESGOTO,
            _rede("rede_esgoto", "pvc", 200, "completo"),
        ),
        (
            "Tubo PEAD Ocre, corrugado externo, liso interno, com parede dupla estruturada e "
            "sistema ponta/bolsa com junta elástica - DN/DI 200mm PE100, rigidez anular mínima "
            "SN8, fornecimento e assentamento",
            "m",
            ESGOTO,
            _rede("rede_esgoto", "pead", 200, "completo"),
        ),
        (
            "Ligação de esgoto completa com tubo PVC Ø 100 mm no terço",
            "unid",
            ESGOTO,
            Classificacao("ligacao_esgoto", None, None, None, "un", Decimal(1)),
        ),
        (
            "LIGACAO DE AGUA EM PEAD DN 20",
            "UN",
            None,
            Classificacao("ligacao_agua", None, None, None, "un", Decimal(1)),
        ),
        # O contexto não vence a descrição que cita a outra linha.
        (
            "TUBO PVC P/ REDE DE ESGOTO DN 150",
            "M",
            AGUA_ADUTORA,
            _rede("rede_esgoto", "pvc", 150, "material"),
        ),
        # A descrição já diz água: o contexto não muda nada, mas a adutora marca.
        (
            "ASSENTAMENTO DE TUBO DE PVC DEFOFO OU PRFV OU RPVC PARA REDE DE ÁGUA, DN 400 MM, "
            "JUNTA ELÁSTICA INTEGRADA (NÃO INCLUI FORNECIMENTO). AF_05/2024",
            "m",
            AGUA_ADUTORA,
            replace(_rede("rede_agua", "pvc", 400, "assentamento"), observacao="adutora"),
        ),
        (
            "FORNECIMENTO E ASSENTAMENTO DE TUBO PVC DN 150 PARA ADUTORA DE AGUA",
            "m",
            None,
            replace(_rede("rede_agua", "pvc", 150, "completo"), observacao="adutora"),
        ),
    ],
)
def test_classifica_com_contexto_da_obra(
    descricao: str, unidade: str, contexto: Contexto | None, esperado: Classificacao
) -> None:
    assert classificar(descricao, unidade, contexto=contexto) == esperado


@pytest.mark.parametrize(
    ("descricao", "unidade", "contexto"),
    [
        # Sem contexto, a descrição sem água nem esgoto continua fora.
        ("TUBO PVC-O DN 250 MM PN12,5", "M", None),
        ("Tubo, PVC Ocre, DN 200 - fornec./assent.", "m", Contexto(None)),
        # Método não destrutivo: outro preço por metro.
        (
            "PERFURAÇÃO HORIZONTAL DIRECIONAL E INSTALAÇÃO DE TUBULAÇÃO PEAD 500 MM, EM SOLO DE "
            "1ª CATEGORIA - INCLUSO ABERTURA DE VALA, EXCLUSO TUBO PEAD",
            "M",
            AGUA_ADUTORA,
        ),
        (
            "Execução de rede por Método Não-Destrutivo (MND) em PEAD DE 250 mm, inclusive "
            "juntas soldadas por termofusão ou eletrofusão",
            "m",
            ESGOTO,
        ),
        (
            "SUBSTITUICAO DE REDE DE AGUA PVC DN 100 POR PIPE BURSTING, INCLUSIVE ASSENTAMENTO",
            "m",
            None,
        ),
        # Escavação sem faixa e recomposição em m³ continuam fora.
        ("Escavação em solo de 1º Categoria", "m³", AGUA_ADUTORA),
        (
            "Recomposição de revestimento em concreto asfáltico (aquisição em usina), para o "
            "fechamento de valas - incluso demolição do pavimento.",
            "m³",
            AGUA_ADUTORA,
        ),
        # Peças e serviços auxiliares com o contexto da obra.
        ("Tubo, FoFo, flange/ponta, DN 250", "unid", AGUA_ADUTORA),
        ("Locação de rede", "m", ESGOTO),
        ("Escoramento de vala", "m2", ESGOTO),
    ],
)
def test_nao_casa_mesmo_com_contexto(
    descricao: str, unidade: str, contexto: Contexto | None
) -> None:
    assert classificar(descricao, unidade, contexto=contexto) is None


@pytest.mark.parametrize(
    "descricao",
    ["Tubo, PVC Ocre, DN 150 - fornec./assent.", "TUBO PVC DN 150 FORN. E ASSENT.",
     "TUBO PVC DN 150 - FORNECIMENTO/ASSENTAMENTO"],
)  # fmt: skip
def test_fornec_assent_abreviado_e_completo(descricao: str) -> None:
    assert escopo_de_rede(sem_acento(descricao)) == "completo"


def test_tcepe_de_teste_sem_mudanca() -> None:
    """Regressão da 7b.1: os orçamentos de `tests/dados/tcepe` classificam como antes."""
    import json

    raiz = Path(__file__).resolve().parents[1] / "dados" / "tcepe"
    achados = {}
    for arquivo in sorted(raiz.glob("orcamento_*.json")):
        for item in json.loads(arquivo.read_text(encoding="utf-8")):
            c = classificar(str(item["itemDescricao"]), str(item["UnidadeMedida"]))
            if c is not None:
                achados[item["itemId"]] = (c.servico, c.material, c.dn, c.escopo, c.faixa)
    assert achados == {
        "63978669": ("rede_esgoto", "pvc", 200, "completo", None),
        "63978670": ("ligacao_esgoto", None, None, None, None),
        "63978671": ("escavacao_vala", None, None, None, "ate_1_5m"),
        "63978683": ("ligacao_esgoto", None, None, None, None),
        "63515394": ("escavacao_vala", None, None, None, "ate_1_5m"),
        "63515395": ("escavacao_vala", None, None, None, "ate_1_5m"),
    }


# --- 7b.2: linhas reais da rodada de 2026-11-27 (PNCP) e do spike, depois do leitor ---

AGUA = Contexto("agua")
# SINAPI "PARA REDE DE ÁGUA OU ESGOTO" (Santa Bárbara d'Oeste, seção de adutora).
PEAD_AGUA_OU_ESGOTO = (
    "ASSENTAMENTO DE TUBO PEAD LISO PARA REDE DE ÁGUA OU ESGOTO, DIÂMETRO DE 110 MM, JUNTA "
    "SOLDADA (NÃO INCLUI A EXECUÇÃO DE SOLDA)"
)


@pytest.mark.parametrize(
    ("descricao", "unidade", "contexto", "esperado"),
    [
        # PNCP 25269069000146-1-000045/2026 (materiais hidráulicos): o tubo, por metro.
        (
            "TUBO EM FERRO FUNDIDO DÚCTIL PONTA/BOLSA, K7, DN 400 MM, CONFORME NBR 7675, PARA "
            "ÁGUA BRUTA OU TRATADA.",
            "METRO",
            None,
            _rede("rede_agua", "fofo", 400, "material"),
        ),
        (
            "TUBO 100MM PARA ESGOTO FABRICADO EM PVC OCRE LISO JEI PARA REDE COLETORA DE ESGOTO "
            "SANITÁRIO, FABRICADO DE ACORDO COM A NORMA NBR 10569, 10570, 7362-1.",
            "METRO",
            None,
            _rede("rede_esgoto", "pvc", 100, "material"),
        ),
        (
            "TUBO EM PVC NA COR MARROM CLASSE 15,JEI(JUNTA ELÁSTICA INTEGRADA INCLUSA) "
            "DIÂMETRO:DN 50MM X DE 60MM,PONTA E BOLSA,PARA O SISTEMA DE ADUÇÃO E DISTRIBUIÇÃO DE "
            "ÁGUA TRATADA",
            "METRO",
            None,
            _rede("rede_agua", "pvc", 50, "material"),
        ),
        # PBA é PVC de água, mesmo sem "PVC" na linha.
        ("TUBO PBA JE DN 100 CLASSE 15", "mts", AGUA, _rede("rede_agua", "pvc", 100, "material")),
        # PNCP 76995422000106-1-000127/2026 (sistemas rurais de abastecimento).
        (
            "Tubo PEAD PE80 DN 50 mm PN 12,5, próprio para adução de água potável em sistemas "
            "rurais de abastecimento",
            "Metros",
            AGUA,
            _rede("rede_agua", "pead", 50, "material"),
        ),
        # "Água ou esgoto": a linha da obra desempata.
        (
            PEAD_AGUA_OU_ESGOTO,
            "M",
            AGUA_ADUTORA,
            replace(_rede("rede_agua", "pead", 110, "assentamento"), observacao="adutora"),
        ),
        (PEAD_AGUA_OU_ESGOTO, "M", ESGOTO, _rede("rede_esgoto", "pead", 110, "assentamento")),
        (
            PEAD_AGUA_OU_ESGOTO.replace("ÁGUA OU ESGOTO", "ÁGUA/ESGOTO"),
            "M",
            ESGOTO,
            _rede("rede_esgoto", "pead", 110, "assentamento"),
        ),
    ],
)
def test_planilhas_do_pncp_classificam(
    descricao: str, unidade: str, contexto: Contexto | None, esperado: Classificacao
) -> None:
    assert classificar(descricao, unidade, contexto=contexto) == esperado


@pytest.mark.parametrize(
    ("descricao", "unidade", "contexto"),
    [
        # "Água ou esgoto" sem a linha da obra continua fora.
        (PEAD_AGUA_OU_ESGOTO, "M", None),
        (PEAD_AGUA_OU_ESGOTO, "M", Contexto(None)),
        # Conexão e junta da mesma composição não são metro de rede.
        (
            "ASSENTAMENTO DE CONEXÃO COM 2 ACESSOS, EM PEAD LISO PARA REDE DE ÁGUA OU ESGOTO, "
            "DIÂMETRO DE 110 MM, JUNTA SOLDADA (NÃO INCLUI O FORNECIMENTO)",
            "UNID",
            AGUA_ADUTORA,
        ),
        (
            "EXECUÇÃO DE JUNTA SOLDADA POR TERMOFUSÃO, DE TUBO OU CONEXÃO EM PEAD LISO PARA "
            "REDE DE ÁGUA OU ESGOTO, DIÂMETRO DE 110 MM",
            "UNID",
            AGUA_ADUTORA,
        ),
        # Ferro fundido sem água nem esgoto (objeto cita os dois): fica sem.
        (
            "TUBO DN 300MM DE FERRO FUNDIDO, CLASSE K7 COM JUNTAS JGS, FABRICADO EM FERRO "
            "FUNDIDO DÚCTIL CONFORME NORMA NBR 7675, DN 300MM.",
            "METRO",
            Contexto(None),
        ),
        ('TUBO PEAD PE 80 RAMAL PREDIAL AZUL, DN 20MM (1/2"), CONFORME NBR', "METRO", None),
        (
            "TUBO EM PVC SOLDÁVEL CLASSE 15, DIÂMETRO: DE 20MM, NA COR MARROM, EM SISTEMA DE "
            "JUNTA SOLDÁVEL COM PONTA E BOLSA PARA 75 MCA DE PRESSÃO DE SERVIÇO",
            "METRO",
            AGUA,
        ),
        ("TUBO CONCRETO 0,80X1,00MT MF. CLASSE PA-1 M/F SEÇÃO CIRCULAR", "METRO", ESGOTO),
        (
            "LUVA DE FERRO FUNDIDO DUCTIL, COM BOLSAS, ( LJGS), DN 400 MM, CONFORME NBR 7675, "
            "PARA ÁGUA BRUTA OU TRATADA.",
            "UNIDADE",
            None,
        ),
        ("União PEAD DN 50 mm para interligação de adutora de água potável", "Und", AGUA),
        (
            "Cabo PP flexível 3x10 mm² em cobre, isolação mínima 0,6/1kV, próprio para "
            "instalação submersa de bomba em poço tubular profundo",
            "Metro",
            AGUA,
        ),
        (
            "HIDRÔMETRO R100, Q3 = 1,6 M³/H, Q4 = 2,00 M³/H, H (HORIZONTAL), T50, MULTIJATO, "
            'DN 1/2", CLASSE B',
            "UNIDADE",
            None,
        ),
        # Spike: MND e escavação com faixa que atravessa 1,5 m continuam fora.
        (
            "Tubo PEAD liso DE 250 mm PE100 (com lista ocre), adequado à instalação por MND",
            "m",
            ESGOTO,
        ),
        ("Escavação mecanizada de valas prof. até 2,20m largura 0,80m", "m³", ESGOTO),
        # Seção de ramal: o tubo é do ramal (Ribeirão Preto, "4.0 Ramal").
        (
            "Tubo, PVC Ocre, DN 100 - fornec./assent.",
            "m",
            Contexto("esgoto", ramal=True),
        ),
    ],
)
def test_planilhas_do_pncp_nao_classificam(
    descricao: str, unidade: str, contexto: Contexto | None
) -> None:
    assert classificar(descricao, unidade, contexto=contexto) is None


def test_secao_de_ramal_tira_so_a_rede() -> None:
    ramal = Contexto("esgoto", ramal=True)
    assert classificar("Ligação de esgoto completa com tubo PVC Ø 100 mm", "unid", contexto=ramal)
    assert classificar("Tubo, PVC Ocre, DN 100 - fornec./assent.", "m", contexto=ESGOTO)


@pytest.mark.parametrize(
    ("secao", "esperado"),
    [
        ("Ramal", True),
        ("LIGAÇÕES DOMICILIARES DE ESGOTO", True),
        ("Ramais prediais", True),
        ("REDE COLETORA E RAMAIS", False),
        ("Coletor", False),
        ("EXECUÇÃO ADUTORA", False),
        (None, False),
    ],
)
def test_secao_de_ramal(secao: str | None, esperado: bool) -> None:
    assert secao_de_ramal(secao) is esperado
