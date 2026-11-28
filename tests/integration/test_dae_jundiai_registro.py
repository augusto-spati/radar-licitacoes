from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pdf_minimo import pdf_com_texto
from radar import cli
from radar.coleta import (
    ClienteEducado,
    PaginaColetada,
    ResumoRetrato,
    executar_retrato,
    paginas_do_retrato,
)
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
    lido: dict[str, str | None] = {
        "arquivo": "MDF 017 26.pdf",
        "data": "2026-12-16T09:30:00-03:00",
        "conferido_em": "2026-11-27",
    }
    registrar_normalizados(sessao, [_ne_846(lido, date(2026, 11, 27))])
    edital = sessao.scalars(
        select(Edital).where(Edital.fonte == "dae_jundiai", Edital.id_na_fonte == "MDF-2026/00017")
    ).one()
    assert edital.situacao_normalizada == "aberta"
    assert edital.data_encerramento_proposta == datetime(2026, 12, 16, 9, 30)
    assert edital.data_abertura_proposta == datetime(2026, 12, 16, 9, 30)
    assert datas_sessao_dae_jundiai(sessao) == {("846", "MDF 017 26.pdf"): lido}

    # A cli entrega o cache à fonte antes do retrato.
    visto: dict[tuple[str, str], dict[str, Any]] = {}

    def executar(_sessao: Session, fonte: FonteDaeJundiai, *_: object) -> ResumoRetrato:
        visto.update(fonte.datas_edital)
        return ResumoRetrato(retrato_id=1, status="concluido", registros=1)

    monkeypatch.setattr(cli, "executar_retrato", executar)
    assert cli._rodar_retrato(sessao, FonteDaeJundiai()) == 0
    assert visto == {("846", "MDF 017 26.pdf"): lido}

    # Encerrado (sem ata há mais de 180 dias) sai do cache.
    registrar_normalizados(sessao, [_ne_846(lido, date(2027, 6, 1))])
    assert datas_sessao_dae_jundiai(sessao) == {}


# --- ponta a ponta: rodada N grava a data, a rodada N+1 não baixa de novo ----------------------

_PDF = pdf_com_texto(
    [["SESSÃO PÚBLICA", "DATA: 16 de dezembro de 2026", "HORÁRIO: Até as 09:30 horas"]]
)


def _lista(ano: str, linha: str) -> str:
    return (
        f"<html><body><form id='form1' method='post'>"
        f"<input type='hidden' name='__VIEWSTATE' value='vs-{ano}'>"
        f"<input type='hidden' name='__EVENTVALIDATION' value='ev-{ano}'>"
        f"<input name='txtNrexer' type='text' value='{ano}'>"
        f"<div id='UpdatePanel2'><table id='gdvCompras'>{linha}</table></div></form></body></html>"
    )


def _linha(modalidade: str, aspx: str, cd: str, numero: str, inicio: str, fim: str) -> str:
    return (
        f"<tr><td><span id='g_lbldsmodal'>{modalidade}</span></td>"
        f"<td><a id='g_lnkMural' href='{aspx}?cdcompra={cd}'>{numero}</a></td>"
        f"<td><span id='g_lblObjeto'>OBRA</span></td><td><span id='g_lblDTInic'>{inicio}</span>"
        f"</td><td><span id='g_lblDTFim'>{fim}</span></td>"
        f"<td><img id='g_imgSituacao' src='x/cp_configuracao.gif'></td></tr>"
    )


LISTA_2026 = _lista(
    "2026",
    _linha("MODO DE DISPUTA FECHADO", "CompraNEWeb.aspx", "900", "2026/00020", "01/10/2026", ""),
)
LISTA_2025 = _lista(
    "2025",
    _linha("PREGÃO ELETRÔNICO", "mural.aspx", "500", "2025/00090", "20/12/2025", "10/01/2026"),
)
DETALHE_900 = (
    "<html><body><form id='form1' method='post' action='./CompraNEWeb.aspx?cdcompra=900'>"
    "<input type='hidden' name='__VIEWSTATE' value='vs-900'>"
    "<input type='hidden' name='__EVENTVALIDATION' value='ev-900'>"
    "<span id='CompraNEMod1_lblTitulo'>MODO DE DISPUTA FECHADO - 2026/20 - PMJ</span>"
    "<span id='CompraNEMod1_lblDtPubl'>01/10/2026</span><table id='grdArquivos'><tr><th>a</th>"
    "</tr><tr><td>E.pdf</td><td>EDITAL DA COMPRA</td><td><a id='grdArquivos_ctl02_lnkArquivo' "
    'href="javascript:__doPostBack(&#39;grdArquivos$ctl02$lnkArquivo&#39;,&#39;&#39;)">'
    "E.pdf</a></td></tr></table></form></body></html>"
)


def _responder(downloads: list[str]) -> httpx.MockTransport:
    def tratar(requisicao: httpx.Request) -> httpx.Response:
        if requisicao.method == "GET":
            if "cdcompra=900" in str(requisicao.url):
                return httpx.Response(200, text=DETALHE_900)
            return httpx.Response(200, text=LISTA_2026)
        if b"lnkArquivo" in requisicao.content:
            downloads.append(requisicao.url.params["cdcompra"])
            return httpx.Response(200, content=_PDF, headers={"content-type": "application/pdf"})
        return httpx.Response(200, text=LISTA_2025)  # a busca do exercício anterior

    return httpx.MockTransport(tratar)


def _rodada(sessao: Session, downloads: list[str], agora: datetime) -> None:
    """Como a cli: cache do banco, retrato, e o consumidor relendo as páginas gravadas."""
    fonte = FonteDaeJundiai(relogio=lambda: agora, baixar_editais=True)
    fonte.minimo_registros = 1
    fonte.usar_datas_edital(datas_sessao_dae_jundiai(sessao))
    with ClienteEducado(httpx.Client(transport=_responder(downloads)), dormir=lambda _s: None) as c:
        resumo = executar_retrato(sessao, fonte, c, agora)
    assert resumo.status == "concluido", resumo.erro
    assert resumo.retrato_id is not None
    registrar_normalizados(sessao, fonte.extrair(paginas_do_retrato(sessao, resumo.retrato_id)))


def test_data_do_pdf_passa_pelo_banco_e_a_rodada_seguinte_nao_baixa(sessao: Session) -> None:
    agora = datetime(2026, 11, 27, 15, tzinfo=UTC)
    downloads: list[str] = []
    _rodada(sessao, downloads, agora)
    _rodada(sessao, downloads, agora + timedelta(days=1))
    assert downloads == ["900"]  # a segunda rodada usou o cache gravado pela primeira
    edital = sessao.scalars(
        select(Edital).where(Edital.fonte == "dae_jundiai", Edital.id_na_fonte == "MDF-2026/00020")
    ).one()
    assert edital.data_encerramento_proposta == datetime(2026, 12, 16, 9, 30)
    assert edital.bruto["detalhe"]["data_sessao_edital"] == {
        "arquivo": "E.pdf",
        "data": "2026-12-16T09:30:00-03:00",
        "conferido_em": "2026-11-27",
    }
