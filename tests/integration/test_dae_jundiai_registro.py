from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar import cli
from radar.coleta import PaginaColetada, ResumoRetrato
from radar.coleta import dae_jundiai as dae
from radar.coleta.dae_jundiai import URL_LISTA, FonteDaeJundiai, ler_detalhe, ler_lista
from radar.editais import datas_sessao_dae_jundiai, registrar_normalizados
from radar.editais.modelos import Edital

DADOS = Path(__file__).resolve().parent.parent / "dados" / "fontes"


def test_pregoes_reais_gravam_com_a_fonte_nova_e_sem_mudanca_na_segunda(sessao: Session) -> None:
    lista = (DADOS / "dae-jundiai-lista-2026-p3.html").read_text(encoding="utf-8")
    paginas = [PaginaColetada(f"{URL_LISTA}#exercicio=2026&pagina=3", "html", lista)]
    fonte = FonteDaeJundiai(relogio=lambda: datetime(2026, 11, 27, 15, tzinfo=UTC))

    primeiro = registrar_normalizados(sessao, fonte.extrair(paginas))
    segundo = registrar_normalizados(sessao, fonte.extrair(paginas))

    assert (primeiro.inseridos, primeiro.invalidos) == (10, 0)
    assert (segundo.atualizados, segundo.ignorados) == (0, 10)
    edital = sessao.scalars(
        select(Edital).where(Edital.fonte == "dae_jundiai", Edital.id_na_fonte == "PE-2026/00063")
    ).one()
    assert edital.situacao_normalizada == "aberta"
    assert edital.data_encerramento_proposta == datetime(2026, 12, 18, 9, 0)
    assert edital.uf == "SP" and edital.orgao_cnpj == "03582243000173"


def _ne_846(edital: dict[str, str | None] | None, hoje: date) -> dict[str, Any]:
    """O MDF 017/26 real (cdcompra 846, publicado em 01/10/2026, sem ata) normalizado."""
    lista = ler_lista((DADOS / "dae-jundiai-lista-2026-p1.html").read_text(encoding="utf-8"))
    linha = next(linha for linha in lista.linhas if linha.cdcompra == "846")
    html = (DADOS / "dae-jundiai-detalhe-846.html").read_text(encoding="utf-8")
    return dae._normalizar("MDF-2026/00017", linha, ler_detalhe(html, "846"), hoje, edital)


def test_data_do_pdf_grava_e_volta_como_cache_so_dos_abertos(
    sessao: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    lido = {"arquivo": "MDF 017 26.pdf", "data": "2026-12-16T09:30:00-03:00"}
    registrar_normalizados(sessao, [_ne_846(lido, date(2026, 11, 27))])
    edital = sessao.scalars(
        select(Edital).where(Edital.fonte == "dae_jundiai", Edital.id_na_fonte == "MDF-2026/00017")
    ).one()
    assert edital.situacao_normalizada == "aberta"
    assert edital.data_encerramento_proposta == datetime(2026, 12, 16, 9, 30)
    assert edital.data_abertura_proposta == datetime(2026, 12, 16, 9, 30)
    assert datas_sessao_dae_jundiai(sessao) == {
        ("846", "MDF 017 26.pdf"): "2026-12-16T09:30:00-03:00"
    }

    # A cli entrega o cache à fonte antes do retrato.
    visto: dict[tuple[str, str], str | None] = {}

    def executar(_sessao: Session, fonte: FonteDaeJundiai, *_: object) -> ResumoRetrato:
        visto.update(fonte.datas_edital)
        return ResumoRetrato(retrato_id=1, status="concluido", registros=1)

    monkeypatch.setattr(cli, "executar_retrato", executar)
    assert cli._rodar_retrato(sessao, FonteDaeJundiai()) == 0
    assert visto == {("846", "MDF 017 26.pdf"): "2026-12-16T09:30:00-03:00"}

    # Encerrado (sem ata há mais de 180 dias) sai do cache.
    registrar_normalizados(sessao, [_ne_846(lido, date(2027, 6, 1))])
    assert datas_sessao_dae_jundiai(sessao) == {}
