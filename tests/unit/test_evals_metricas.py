from decimal import Decimal

import pytest

from radar.analise.evals import Contagem, comparar_com_gabarito, mascarar_dados_pessoais, slug
from radar.analise.exigencias import Atestado, CategoriaEvidencia, Exigencias, ValorEvidencia


def _v(numero: str | None) -> ValorEvidencia:
    return ValorEvidencia(numero=None if numero is None else Decimal(numero), trecho="t", pagina=1)


def _c(valor: str) -> CategoriaEvidencia:
    return CategoriaEvidencia(valor=valor, trecho="t", pagina=1)


def _a(descricao: str, quantidade: str, unidade: str) -> Atestado:
    return Atestado(
        descricao=descricao, quantidade=Decimal(quantidade), unidade=unidade, trecho="t", pagina=1
    )


def test_slug() -> None:
    assert slug("pncp~78200482000110-1-000298/2026") == "pncp_78200482000110-1-000298_2026"


def test_escalar_igual_vp() -> None:
    c = comparar_com_gabarito(
        Exigencias(capital_social_minimo=_v("100000")),
        Exigencias(capital_social_minimo=_v("100000")),
    )["capital_social_minimo"]
    assert (c.vp, c.fp, c.fn) == (1, 0, 0)


def test_escalar_diferente_conta_fp_e_fn() -> None:
    c = comparar_com_gabarito(
        Exigencias(capital_social_minimo=_v("100000")),
        Exigencias(capital_social_minimo=_v("200000")),
    )["capital_social_minimo"]
    assert (c.vp, c.fp, c.fn, c.alucinacoes) == (0, 1, 1, 0)


def test_tolerancia_um_por_cento() -> None:
    gabarito = Exigencias(capital_social_minimo=_v("100000"))
    dentro = comparar_com_gabarito(Exigencias(capital_social_minimo=_v("100900")), gabarito)
    fora = comparar_com_gabarito(Exigencias(capital_social_minimo=_v("102000")), gabarito)
    assert dentro["capital_social_minimo"].vp == 1
    assert (fora["capital_social_minimo"].fp, fora["capital_social_minimo"].fn) == (1, 1)


def test_ausente_nos_dois_nao_conta_e_soma_ausentes() -> None:
    c = comparar_com_gabarito(Exigencias(), Exigencias())["patrimonio_liquido_minimo"]
    assert (c.vp, c.fp, c.fn, c.ausentes, c.alucinacoes) == (0, 0, 0, 1, 0)
    assert c.f1 is None
    assert c.taxa_alucinacao == 0.0


def test_gabarito_presente_modelo_ausente_fn() -> None:
    c = comparar_com_gabarito(Exigencias(), Exigencias(me_epp=_c("cota")))["me_epp"]
    assert (c.vp, c.fp, c.fn, c.ausentes) == (0, 0, 1, 0)
    assert c.recall == 0.0
    assert c.precisao is None


def test_alucinacao() -> None:
    c = comparar_com_gabarito(Exigencias(visita_tecnica=_c("obrigatoria")), Exigencias())[
        "visita_tecnica"
    ]
    assert (c.fp, c.alucinacoes, c.ausentes) == (1, 1, 1)
    assert c.taxa_alucinacao == 1.0


def test_categorico_diferente() -> None:
    c = comparar_com_gabarito(
        Exigencias(consorcio=_c("permitido")), Exigencias(consorcio=_c("vedado"))
    )["consorcio"]
    assert (c.vp, c.fp, c.fn) == (0, 1, 1)
    assert c.f1 == 0.0


def test_atestados_par_unidade_quantidade() -> None:
    previsto = Exigencias(
        atestados=[_a("vazamentos não visíveis", "5", "km"), _a("hidrômetros", "100", "un")]
    )
    gabarito = Exigencias(
        atestados=[_a("pesquisa de vazamentos", "5000", "m"), _a("ligações", "50", "un")]
    )
    c = comparar_com_gabarito(previsto, gabarito)["atestados"]
    assert (c.vp, c.fp, c.fn) == (1, 1, 1)


def test_atestados_alucinados() -> None:
    c = comparar_com_gabarito(Exigencias(atestados=[_a("x", "1", "km")]), Exigencias())["atestados"]
    assert (c.fp, c.ausentes, c.alucinacoes) == (1, 1, 1)


def test_documentos_por_item() -> None:
    previsto = Exigencias(documentos=[_c("certidao_fgts"), _c("balanco_patrimonial")])
    gabarito = Exigencias(documentos=[_c("certidao_fgts"), _c("certidao_trabalhista")])
    c = comparar_com_gabarito(previsto, gabarito)["documentos"]
    assert (c.vp, c.fp, c.fn, c.alucinacoes) == (1, 1, 1, 1)
    assert c.ausentes == 9


def test_divisao_por_zero_e_none() -> None:
    vazio = Contagem()
    assert (vazio.precisao, vazio.recall, vazio.f1, vazio.taxa_alucinacao) == (
        None,
        None,
        None,
        None,
    )
    c = Contagem(vp=1, fp=1, fn=0)
    assert c.precisao == 0.5
    assert c.recall == 1.0
    assert c.f1 is not None and abs(c.f1 - 2 / 3) < 1e-9


def test_somar() -> None:
    total = Contagem()
    total.somar(Contagem(vp=1, fp=2, fn=3, ausentes=4, alucinacoes=5))
    total.somar(Contagem(vp=1))
    assert (total.vp, total.fp, total.fn, total.ausentes, total.alucinacoes) == (2, 2, 3, 4, 5)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("inscrito no CPF sob o nº 255.499.628-88, residente", "inscrito no CPF sob o nº ***.***.***-**, residente"),  # noqa: E501
        ("CPF Nº 311148448/37\nAssinatura", "CPF Nº ***.***.***-**\nAssinatura"),
        ("CPF: 25549962888 e", "CPF: ***.***.***-** e"),
        ("CPF 255.499.628-88. Fim e RG 27.645.812-6.", "CPF ***.***.***-**. Fim e RG **.***.***-*."),  # noqa: E501
        ("digital por ANA \nREZENDE:05715322685 \nDados:", "digital por ANA \nREZENDE:*********** \nDados:"),  # noqa: E501
        ("Identidade RG nº 27.645.812-6 SSP/SP", "Identidade RG nº **.***.***-* SSP/SP"),
        ("RG nº 336637892 SSP", "RG nº **.***.***-* SSP"),
        ("RG: 4.123.456 SSP/PR", "RG: **.***.***-* SSP/PR"),
    ],
)  # fmt: skip
def test_mascara_cpf_e_rg(texto: str, esperado: str) -> None:
    assert mascarar_dados_pessoais(texto) == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "CNPJ 45.359.973/0001-50 e CEP 13.140-000",
        "R$ 1.250.000,00 e 2.000.000 de metros",
        "https://pncp.gov.br/app/editais/45359973000150/2026/249",
        "CREA nº 5061119656 e processo 0007337.989-24",
        "CPF, RG e e-mail pessoal",
    ],
)
def test_mascara_preserva_o_que_nao_e_cpf_nem_rg(texto: str) -> None:
    assert mascarar_dados_pessoais(texto) == texto
