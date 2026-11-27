# ruff: noqa: E501  (tabela de casos: uma linha por regra)
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from radar.analise.comparacao import Comparacao, comparar
from radar.analise.exigencias import Atestado, CategoriaEvidencia, Exigencias, ValorEvidencia
from radar.analise.perfil import AtestadoPerfil, Perfil, carregar_perfil

RAIZ = Path(__file__).resolve().parents[2]


def _perfil(**mudancas: Any) -> Perfil:
    base: dict[str, Any] = {
        "porte": "demais",
        "capital_social": Decimal("1000000"),
        "patrimonio_liquido": Decimal("1500000"),
        "garantia_maxima_percentual": Decimal("5"),
        "faz_visita_tecnica": True,
        "depende_de_consorcio": False,
        "documentos": ["certidao_fgts", "certidao_federal"],
        "atestados": [
            AtestadoPerfil(
                descricao="Pesquisa de vazamentos",
                termos=["vazamento", "vazamentos"],
                quantidade=Decimal("1500"),
                unidade="km",
            ),
            AtestadoPerfil(
                descricao="Rede de água",
                termos=["rede de agua"],
                quantidade=Decimal("30000"),
                unidade="m",
            ),
        ],
    }
    base.update(mudancas)
    return Perfil(**base)


def _valor(numero: str | None, conflito: bool = False) -> ValorEvidencia:
    return ValorEvidencia(
        numero=None if numero is None else Decimal(numero),
        trecho="trecho",
        pagina=3,
        conflito=conflito,
    )


def _cat(valor: str, conflito: bool = False) -> CategoriaEvidencia:
    return CategoriaEvidencia(valor=valor, trecho="trecho", pagina=4, conflito=conflito)


def _situacao(comparacao: Comparacao, campo: str) -> str:
    [item] = [i for i in comparacao.itens if i.campo == campo]
    return item.situacao


E = Exigencias
P = _perfil


@pytest.mark.parametrize(
    ("exigencias", "perfil", "campo", "esperado"),
    [
        (E(capital_social_minimo=_valor("900000")), P(), "capital_social_minimo", "atende"),
        (E(capital_social_minimo=_valor("1000000")), P(), "capital_social_minimo", "atende"),
        (E(capital_social_minimo=_valor("1200000")), P(), "capital_social_minimo", "nao_atende"),
        (E(capital_social_minimo=_valor(None)), P(), "capital_social_minimo", "verificar"),
        (E(capital_social_minimo=_valor("10")), P(capital_social=None), "capital_social_minimo", "verificar"),
        (E(patrimonio_liquido_minimo=_valor("2000000")), P(), "patrimonio_liquido_minimo", "nao_atende"),
        (E(patrimonio_liquido_minimo=_valor("1500000")), P(), "patrimonio_liquido_minimo", "atende"),
        (E(garantia_proposta_percentual=_valor("1")), P(), "garantia_proposta_percentual", "atende"),
        (E(garantia_contratual_percentual=_valor("5")), P(), "garantia_contratual_percentual", "atende"),
        (E(garantia_contratual_percentual=_valor("10")), P(), "garantia_contratual_percentual", "verificar"),
        (E(garantia_contratual_percentual=_valor(None)), P(), "garantia_contratual_percentual", "verificar"),
        (E(visita_tecnica=_cat("obrigatoria")), P(), "visita_tecnica", "atende"),
        (E(visita_tecnica=_cat("obrigatoria")), P(faz_visita_tecnica=False), "visita_tecnica", "nao_atende"),
        (E(visita_tecnica=_cat("facultativa")), P(faz_visita_tecnica=False), "visita_tecnica", "atende"),
        (E(consorcio=_cat("vedado")), P(depende_de_consorcio=True), "consorcio", "nao_atende"),
        (E(consorcio=_cat("vedado")), P(), "consorcio", "atende"),
        (E(consorcio=_cat("permitido")), P(depende_de_consorcio=True), "consorcio", "atende"),
        (E(me_epp=_cat("exclusiva")), P(), "me_epp", "nao_atende"),
        (E(me_epp=_cat("exclusiva")), P(porte="epp"), "me_epp", "atende"),
        (E(me_epp=_cat("cota")), P(), "me_epp", "atende"),
        (E(me_epp=_cat("ampla")), P(), "me_epp", "atende"),
        (E(prazo_execucao_dias=_valor("360")), P(), "prazo_execucao_dias", "informativo"),
        (E(documentos=[_cat("certidao_fgts")]), P(), "documentos", "atende"),
        (E(documentos=[_cat("certidao_fgts"), _cat("registro_conselho")]), P(), "documentos", "verificar"),
    ],
)  # fmt: skip
def test_regras(exigencias: Exigencias, perfil: Perfil, campo: str, esperado: str) -> None:
    assert _situacao(comparar(exigencias, perfil, incompleta=False), campo) == esperado


def test_cota_explica_cota_ampla() -> None:
    comparacao = comparar(Exigencias(me_epp=_cat("cota")), _perfil(), incompleta=False)
    assert "cota ampla" in comparacao.itens[0].motivo


def test_documentos_faltantes_listados() -> None:
    comparacao = comparar(
        Exigencias(documentos=[_cat("registro_conselho")]), _perfil(), incompleta=False
    )
    assert "registro_conselho" in comparacao.itens[0].motivo


def test_campo_ausente_nao_vira_item() -> None:
    comparacao = comparar(Exigencias(), _perfil(), incompleta=False)
    assert comparacao.itens == []
    assert comparacao.veredito == "apto"


def test_conflito_vira_verificar() -> None:
    comparacao = comparar(
        Exigencias(capital_social_minimo=_valor("100", conflito=True)), _perfil(), incompleta=False
    )
    assert _situacao(comparacao, "capital_social_minimo") == "verificar"
    assert "conflito" in comparacao.itens[0].motivo


def test_conflito_de_categoria_vira_verificar() -> None:
    comparacao = comparar(
        Exigencias(visita_tecnica=_cat("obrigatoria", conflito=True)), _perfil(), incompleta=False
    )
    assert _situacao(comparacao, "visita_tecnica") == "verificar"


def _atestado(descricao: str, quantidade: str | None, unidade: str | None) -> Atestado:
    return Atestado(
        descricao=descricao,
        quantidade=None if quantidade is None else Decimal(quantidade),
        unidade=unidade,
        trecho="trecho",
        pagina=8,
    )


def test_atestado_casa_por_termo_sem_acento() -> None:
    comparacao = comparar(
        Exigencias(atestados=[_atestado("pesquisa de VAZAMENTOS não visíveis", "1000", "km")]),
        _perfil(),
        incompleta=False,
    )
    assert _situacao(comparacao, "atestados") == "atende"


def test_atestado_quantidade_maior_verificar() -> None:
    comparacao = comparar(
        Exigencias(atestados=[_atestado("pesquisa de vazamentos", "2000", "km")]),
        _perfil(),
        incompleta=False,
    )
    assert _situacao(comparacao, "atestados") == "verificar"


def test_atestado_nunca_nao_atende() -> None:
    comparacao = comparar(
        Exigencias(atestados=[_atestado("pesquisa de vazamentos", "10", "un")]),
        _perfil(),
        incompleta=False,
    )
    assert _situacao(comparacao, "atestados") == "verificar"


def test_atestado_perfil_em_metros_casa_com_km() -> None:
    # Perfil com 30.000 m de rede; exigido 20 km.
    comparacao = comparar(
        Exigencias(atestados=[_atestado("Execução de rede de água", "20", "km")]),
        _perfil(),
        incompleta=False,
    )
    assert _situacao(comparacao, "atestados") == "atende"


def test_atestado_sem_quantidade_casa_por_termo() -> None:
    comparacao = comparar(
        Exigencias(atestados=[_atestado("pesquisa de vazamentos", None, None)]),
        _perfil(),
        incompleta=False,
    )
    assert _situacao(comparacao, "atestados") == "atende"


def test_veredito_inapto_vence_verificar() -> None:
    comparacao = comparar(
        Exigencias(
            capital_social_minimo=_valor("5000000"),
            garantia_contratual_percentual=_valor("10"),
        ),
        _perfil(),
        incompleta=True,
    )
    assert comparacao.veredito == "inapto"


def test_incompleta_sem_nao_atende_vira_verificar() -> None:
    comparacao = comparar(Exigencias(capital_social_minimo=_valor("10")), _perfil(), True)
    assert comparacao.veredito == "verificar"


def test_verificar_sem_nao_atende_vira_verificar() -> None:
    comparacao = comparar(
        Exigencias(garantia_contratual_percentual=_valor("10")), _perfil(), incompleta=False
    )
    assert comparacao.veredito == "verificar"


def test_tudo_atende_vira_apto() -> None:
    comparacao = comparar(
        Exigencias(
            capital_social_minimo=_valor("10"),
            visita_tecnica=_cat("obrigatoria"),
            prazo_execucao_dias=_valor("90"),
        ),
        _perfil(),
        incompleta=False,
    )
    assert comparacao.veredito == "apto"


def test_item_leva_trecho_e_pagina() -> None:
    comparacao = comparar(Exigencias(capital_social_minimo=_valor("10")), _perfil(), False)
    assert (comparacao.itens[0].trecho, comparacao.itens[0].pagina) == ("trecho", 3)


def test_perfil_de_exemplo_carrega() -> None:
    caminho = RAIZ / "config" / "perfil.toml"
    assert caminho.read_text(encoding="utf-8").startswith("# PERFIL DE EXEMPLO")
    perfil = carregar_perfil(caminho)
    assert perfil.porte == "demais"
    assert perfil.capital_social == Decimal("1000000")
    assert len(perfil.documentos) == 8
    assert len(perfil.atestados) == 4


def test_documento_invalido_no_perfil(tmp_path: Path) -> None:
    arquivo = tmp_path / "perfil.toml"
    arquivo.write_text(
        '[empresa]\nporte = "me"\ncapital_social = 1\npatrimonio_liquido = 1\n'
        "garantia_maxima_percentual = 5\nfaz_visita_tecnica = true\n"
        'depende_de_consorcio = false\ndocumentos = ["alvara"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="alvara"):
        carregar_perfil(arquivo)
