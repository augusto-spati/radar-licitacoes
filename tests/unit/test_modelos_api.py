from datetime import datetime
from decimal import Decimal

import pytest

from radar.api.modelos import detalhe_out, resumo_out
from radar.editais import EditalDetalhe


def _edital(link_origem: str | None) -> EditalDetalhe:
    return EditalDetalhe(
        numero_controle_pncp="13127340000120-1-000036/2025",
        objeto="Conserto de vazamento",
        orgao_nome="SAAE",
        uf="SP",
        municipio="Holambra",
        modalidade_id=6,
        valor_estimado=Decimal("845000"),
        data_encerramento_proposta=None,
        link_origem=link_origem,
        linhas=["perdas"],
        orgao_cnpj="13127340000120",
        situacao_nome="Divulgada no PNCP",
        data_publicacao=datetime(2026, 11, 20, 10, 16),
        data_abertura_proposta=None,
    )


@pytest.mark.parametrize(
    "link",
    [None, "", "   ", "www.compras.gov.br/edital/1", "javascript:alert(1)", "ftp://x.gov.br/a"],
)
def test_link_origem_vazio_ou_sem_http_vira_nulo(link: str | None) -> None:
    assert resumo_out(_edital(link)).link_origem is None
    assert detalhe_out(_edital(link)).link_origem is None


@pytest.mark.parametrize(
    "link", ["https://compras.exemplo.gov.br/edital/36", "http://licita.exemplo.gov.br/36"]
)
def test_link_origem_http_passa_inteiro(link: str) -> None:
    assert detalhe_out(_edital(link)).link_origem == link


def test_link_origem_com_espacos_nas_pontas_e_aparado() -> None:
    assert resumo_out(_edital("  https://a.gov.br/1 ")).link_origem == "https://a.gov.br/1"
