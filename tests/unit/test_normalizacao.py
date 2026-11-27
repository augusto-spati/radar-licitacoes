from decimal import Decimal

import pytest

from radar.analise.exigencias import Achado
from radar.analise.normalizacao import (
    AchadoAncorado,
    ancorar,
    juntar,
    normalizar_unidade,
)


def _achado(
    campo: str, valor: str = "", numero: float | None = None, unidade: str = "", trecho: str = "t"
) -> Achado:
    return Achado.model_validate(
        {"campo": campo, "valor": valor, "numero": numero, "unidade": unidade, "trecho": trecho}
    )


def _ancorado(
    campo: str, valor: str = "", numero: float | None = None, unidade: str = "", pagina: int = 1
) -> AchadoAncorado:
    return AchadoAncorado(_achado(campo, valor, numero, unidade, f"trecho de {campo}"), pagina)


TEXTO = (
    "[[pagina 6]]\nItem 8.1. Exige-se capital  social\nmínimo de R$ 100.000,00.\n"
    "[[pagina 7]]\nA visita técnica é obrigatória."
)


def test_trecho_parafraseado_descartado() -> None:
    ancorados, descartados = ancorar(
        [_achado("capital_social_minimo", trecho="o capital mínimo é de cem mil reais")], TEXTO, 6
    )
    assert ancorados == []
    assert descartados == 1


def test_trecho_vazio_descartado() -> None:
    ancorados, descartados = ancorar([_achado("consorcio", "vedado", trecho="  ")], TEXTO, 6)
    assert ancorados == []
    assert descartados == 1


def test_trecho_com_quebra_de_linha_ancora() -> None:
    ancorados, descartados = ancorar(
        [_achado("capital_social_minimo", trecho="Capital social mínimo")], TEXTO, 6
    )
    assert descartados == 0
    assert ancorados[0].pagina == 6


def test_pagina_vem_do_marcador() -> None:
    ancorados, _ = ancorar(
        [_achado("visita_tecnica", "obrigatoria", trecho="A visita técnica é obrigatória.")],
        TEXTO,
        6,
    )
    assert ancorados[0].pagina == 7


def test_sem_marcador_usa_pagina_inicial() -> None:
    ancorados, _ = ancorar([_achado("consorcio", "vedado", trecho="vedada")], "é vedada", 12)
    assert ancorados[0].pagina == 12


def test_numeros_diferentes_ficam_com_maior_e_conflito() -> None:
    exigencias, _ = juntar(
        [
            _ancorado("capital_social_minimo", numero=100000, pagina=3),
            _ancorado("capital_social_minimo", numero=150000, pagina=9),
        ],
        None,
    )
    assert exigencias.capital_social_minimo is not None
    assert exigencias.capital_social_minimo.numero == Decimal("150000")
    assert exigencias.capital_social_minimo.pagina == 9
    assert exigencias.capital_social_minimo.conflito is True


def test_mesmo_numero_sem_conflito() -> None:
    exigencias, _ = juntar(
        [
            _ancorado("garantia_contratual", "seguro", 5, "%"),
            _ancorado("garantia_contratual", "seguro", 5, "%"),
        ],
        None,
    )
    assert exigencias.garantia_contratual_percentual is not None
    assert exigencias.garantia_contratual_percentual.numero == Decimal("5")
    assert exigencias.garantia_contratual_percentual.conflito is False


def test_duplicata_da_sobreposicao_conta_uma_vez() -> None:
    atestado = _ancorado("atestado_tecnico", "Pesquisa de vazamentos", 500, "km")
    exigencias, _ = juntar([atestado, atestado], None)
    assert len(exigencias.atestados) == 1


def test_atestados_diferentes_por_uniao() -> None:
    exigencias, _ = juntar(
        [
            _ancorado("atestado_tecnico", "Pesquisa de vazamentos", 500, "km"),
            _ancorado("atestado_tecnico", "Troca de hidrômetros", 2000, "hidrometros"),
        ],
        None,
    )
    assert [(a.unidade, a.quantidade) for a in exigencias.atestados] == [
        ("km", Decimal("500")),
        ("un", Decimal("2000")),
    ]


def test_precedencia_visita() -> None:
    exigencias, _ = juntar(
        [_ancorado("visita_tecnica", "facultativa"), _ancorado("visita_tecnica", "obrigatoria")],
        None,
    )
    assert exigencias.visita_tecnica is not None
    assert exigencias.visita_tecnica.valor == "obrigatoria"
    assert exigencias.visita_tecnica.conflito is True


@pytest.mark.parametrize(
    ("campo", "valores", "esperado"),
    [
        ("consorcio", ["permitido", "vedado"], "vedado"),
        ("me_epp", ["ampla", "exclusiva", "cota"], "exclusiva"),
        ("me_epp", ["ampla", "cota"], "cota"),
        ("visita_tecnica", ["Não exigida", "facultativa"], "facultativa"),
    ],
)
def test_precedencia_categorias(campo: str, valores: list[str], esperado: str) -> None:
    exigencias, descartados = juntar([_ancorado(campo, v) for v in valores], None)
    categoria = getattr(exigencias, campo)
    assert descartados == 0
    assert categoria.valor == esperado
    assert categoria.conflito is True


def test_percentual_vira_reais_com_valor_estimado() -> None:
    exigencias, _ = juntar(
        [_ancorado("patrimonio_liquido_minimo", "percentual", 10, "%")], Decimal("2000000")
    )
    assert exigencias.patrimonio_liquido_minimo is not None
    assert exigencias.patrimonio_liquido_minimo.numero == Decimal("200000")
    assert "10%" in exigencias.patrimonio_liquido_minimo.observacao


def test_percentual_sem_valor_estimado() -> None:
    exigencias, _ = juntar([_ancorado("capital_social_minimo", "percentual", 10, "%")], None)
    assert exigencias.capital_social_minimo is not None
    assert exigencias.capital_social_minimo.numero is None
    assert "10%" in exigencias.capital_social_minimo.observacao


def test_capital_com_valor_em_texto_fica_em_reais() -> None:
    # Na sonda, os modelos puseram "250.000,00" no `valor` em vez de "".
    exigencias, descartados = juntar(
        [_ancorado("capital_social_minimo", "250.000,00", 250000, "reais")], None
    )
    assert descartados == 0
    assert exigencias.capital_social_minimo is not None
    assert exigencias.capital_social_minimo.numero == Decimal("250000")


def test_meses_viram_dias() -> None:
    exigencias, _ = juntar([_ancorado("prazo_execucao", "", 12, "meses")], None)
    assert exigencias.prazo_execucao_dias is not None
    assert exigencias.prazo_execucao_dias.numero == Decimal("360")


def test_prazo_em_dias() -> None:
    exigencias, _ = juntar([_ancorado("prazo_execucao", "", 90, "dias corridos")], None)
    assert exigencias.prazo_execucao_dias is not None
    assert exigencias.prazo_execucao_dias.numero == Decimal("90")


def test_metros_viram_km() -> None:
    exigencias, _ = juntar([_ancorado("atestado_tecnico", "Rede de água", 5000, "metros")], None)
    assert exigencias.atestados[0].quantidade == Decimal("5")
    assert exigencias.atestados[0].unidade == "km"


@pytest.mark.parametrize(
    ("unidade", "esperado"),
    [
        ("metros", ("km", Decimal("0.001"))),
        ("m", ("km", Decimal("0.001"))),
        ("km", ("km", Decimal("1"))),
        ("Quilômetros", ("km", Decimal("1"))),
        ("un", ("un", Decimal("1"))),
        ("unidades", ("un", Decimal("1"))),
        ("hidrômetros", ("un", Decimal("1"))),
        ("leituras", ("leitura", Decimal("1"))),
        ("ligações", ("ligacao", Decimal("1"))),
        ("Pontos", ("pontos", Decimal("1"))),
    ],
)
def test_normalizar_unidade(unidade: str, esperado: tuple[str, Decimal]) -> None:
    assert normalizar_unidade(unidade) == esperado


def test_valor_fora_do_vocabulario_descartado() -> None:
    exigencias, descartados = juntar([_ancorado("visita_tecnica", "talvez")], None)
    assert exigencias.visita_tecnica is None
    assert descartados == 1


def test_documento_desconhecido_descartado() -> None:
    exigencias, descartados = juntar(
        [
            _ancorado("documento_habilitacao", "certidao_fgts"),
            _ancorado("documento_habilitacao", "alvara_de_funcionamento"),
            _ancorado("documento_habilitacao", "certidao_fgts"),
        ],
        None,
    )
    assert [d.valor for d in exigencias.documentos] == ["certidao_fgts"]
    assert descartados == 1


def test_prazo_com_unidade_desconhecida_descartado() -> None:
    exigencias, descartados = juntar([_ancorado("prazo_execucao", "", 3, "semestres")], None)
    assert exigencias.prazo_execucao_dias is None
    assert descartados == 1
