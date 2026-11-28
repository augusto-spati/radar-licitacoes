from datetime import datetime
from decimal import Decimal

import pytest

from radar.api.modelos import detalhe_out, link_reserva, resumo_out
from radar.editais import EditalDetalhe, separar_id_publico

NUMERO_PNCP = "13127340000120-1-000036/2025"
LINK_PNCP = "https://pncp.gov.br/app/editais/13127340000120/2025/36"


def _edital(
    link_origem: str | None, fonte: str = "pncp", id_na_fonte: str = NUMERO_PNCP
) -> EditalDetalhe:
    return EditalDetalhe(
        fonte=fonte,
        id_na_fonte=id_na_fonte,
        objeto="Conserto de vazamento",
        orgao_nome="SAAE",
        uf="SP",
        municipio="Holambra",
        modalidade_id=6,
        modalidade_nome="Pregão - Eletrônico",
        valor_estimado=Decimal("845000"),
        data_encerramento_proposta=None,
        link_origem=link_origem,
        linhas=["perdas"],
        veredito=None,
        motivo_verificar=None,
        primeiro_visto_em=None,
        orgao_cnpj="13127340000120",
        situacao_nome="Divulgada no PNCP",
        situacao_normalizada="aberta",
        data_publicacao=datetime(2026, 11, 20, 10, 16),
        data_abertura_proposta=None,
    )


@pytest.mark.parametrize(
    "link",
    [None, "", "   ", "www.compras.gov.br/edital/1", "javascript:alert(1)", "ftp://x.gov.br/a"],
)
def test_link_origem_vazio_ou_sem_http_cai_no_link_de_reserva(link: str | None) -> None:
    assert resumo_out(_edital(link)).link_origem == LINK_PNCP
    assert detalhe_out(_edital(link)).link_origem == LINK_PNCP


def test_sem_link_e_sem_reserva_fica_nulo() -> None:
    assert resumo_out(_edital(None, id_na_fonte="fora-do-formato")).link_origem is None


@pytest.mark.parametrize(
    "link", ["https://compras.exemplo.gov.br/edital/36", "http://licita.exemplo.gov.br/36"]
)
def test_link_origem_http_passa_inteiro(link: str) -> None:
    assert detalhe_out(_edital(link)).link_origem == link


def test_link_origem_com_espacos_nas_pontas_e_aparado() -> None:
    assert resumo_out(_edital("  https://a.gov.br/1 ")).link_origem == "https://a.gov.br/1"


@pytest.mark.parametrize(
    ("fonte", "id_na_fonte", "esperado"),
    [
        ("pncp", NUMERO_PNCP, LINK_PNCP),
        ("pncp", "qualquer-coisa", None),
        ("sanepar", "OE-345/26", "https://www.sanepar.com.br/licitacao/detalhe/345/26"),
        ("sanepar", "345/26", None),
        ("compesa", "2026LC213CEL-1", "https://servicos.compesa.com.br/licitacoes-e-contratos/"),
        ("sabesp", "1", None),
    ],
)
def test_link_reserva_por_fonte(fonte: str, id_na_fonte: str, esperado: str | None) -> None:
    assert link_reserva(fonte, id_na_fonte) == esperado


def test_resumo_traz_fonte_e_id_publico() -> None:
    saida = resumo_out(_edital(None, fonte="sanepar", id_na_fonte="OE-345/26"))

    assert (saida.fonte, saida.id_publico) == ("sanepar", "sanepar~OE-345/26")


def test_detalhe_traz_situacao_normalizada() -> None:
    assert detalhe_out(_edital(None)).situacao_normalizada == "aberta"


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("pncp~13127340000120-1-000036/2025", ("pncp", NUMERO_PNCP)),
        ("compesa~2026LC213CEL-1", ("compesa", "2026LC213CEL-1")),
        ("sanepar~OE~estranho", ("sanepar", "OE~estranho")),  # só o primeiro ~ separa
        ("13127340000120-1-000036/2025", None),
        ("sabesp~1", None),
        ("pncp~", None),
        ("~1", None),
    ],
)
def test_separar_id_publico(valor: str, esperado: tuple[str, str] | None) -> None:
    assert separar_id_publico(valor) == esperado
