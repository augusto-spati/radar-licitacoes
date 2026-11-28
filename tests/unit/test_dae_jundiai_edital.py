"""Data da sessão no texto do edital da DAE (capas reais de MDF e MDA de 2026)."""

import logging
from datetime import datetime
from pathlib import Path

import pytest

from radar.coleta.dae_jundiai import FUSO, data_sessao_no_edital

DADOS = Path(__file__).resolve().parent.parent / "dados" / "fontes"


def _capa(nome: str) -> str:
    linhas = (DADOS / nome).read_text(encoding="utf-8").splitlines()
    return "\n".join(linha for linha in linhas if not linha.startswith("#"))


CAPA_MDF_012 = _capa("dae-jundiai-edital-mdf-012-2026-capa.txt")
CAPA_MDA_01 = _capa("dae-jundiai-edital-mda-01-2026-capa.txt")


def test_capa_real_mdf():
    assert data_sessao_no_edital(CAPA_MDF_012) == datetime(2026, 6, 16, 9, 30, tzinfo=FUSO)


def test_capa_real_mda():
    assert data_sessao_no_edital(CAPA_MDA_01) == datetime(2026, 4, 14, 14, 30, tzinfo=FUSO)


def test_capa_no_meio_do_edital():
    corpo = (
        "4.5 – A sessão de abertura dos envelopes será efetuada conforme data, horário e "
        "endereço discriminados neste edital: 5.2.5 – validade de 90 dias, a contar da data "
        "de abertura da sessão. Edital de 12/05/2026"
    )
    texto = f"Licitação Modo de Disputa Fechado nº 012/2026\n{CAPA_MDF_012}\n{corpo}"
    assert data_sessao_no_edital(texto) == datetime(2026, 6, 16, 9, 30, tzinfo=FUSO)


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("SESSÃO PÚBLICA: dia 03/12/2026 às 10h00", datetime(2026, 12, 3, 10, 0)),
        ("Data da disputa: 1º de dezembro de 2026, às 9:00", datetime(2026, 12, 1, 9, 0)),
        (
            "ABERTURA DOS ENVELOPES: 05/01/2027 HORÁRIO: 14:00",
            datetime(2027, 1, 5, 14, 0),
        ),
    ],
)
def test_outras_redacoes(texto: str, esperado: datetime):
    assert data_sessao_no_edital(texto) == esperado.replace(tzinfo=FUSO)


def test_datas_diferentes_ambiguo_none(caplog: pytest.LogCaptureFixture):
    texto = CAPA_MDF_012.replace(
        "SESSÃO PÚBLICA \nDATA: 16 de junho", "SESSÃO PÚBLICA \nDATA: 17 de junho"
    )
    assert texto != CAPA_MDF_012
    with caplog.at_level(logging.INFO, logger="radar.coleta.dae_jundiai"):
        assert data_sessao_no_edital(texto) is None
    assert any(r.message == "dae_jundiai_data_sessao_ambigua" for r in caplog.records)


def test_horas_diferentes_ambiguo_none():
    texto = CAPA_MDA_01.replace("Até as 14:30", "Até as 09:00", 1)
    assert texto != CAPA_MDA_01
    assert data_sessao_no_edital(texto) is None


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "SESSÃO PÚBLICA DATA: 16 de junho de 2026",  # sem hora
        "A sessão de abertura dos envelopes será efetuada conforme data e horário do edital.",
        "SESSÃO PÚBLICA DATA: 31/02/2026 HORÁRIO: 09:30",  # data impossível
        "SESSÃO PÚBLICA DATA: 16 de juno de 2026 HORÁRIO: 09:30",  # mês desconhecido
        "SESSÃO PÚBLICA DATA: 16/06/2026 HORÁRIO: 29:30",  # hora impossível
        "Edital de 12/05/2026, publicado em 13/05/2026 às 10:00",  # data sem rótulo de sessão
    ],
)
def test_sem_data_confiavel_none(texto: str):
    assert data_sessao_no_edital(texto) is None
