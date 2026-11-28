import json
import logging
import signal
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from pdf_minimo import pdf_com_texto
from radar.coleta import ClienteEducado, ErroFonte, PaginaColetada
from radar.coleta import dae_jundiai as dae
from radar.coleta.dae_jundiai import (
    BASE,
    URL_LISTA,
    Detalhe,
    FonteDaeJundiai,
    LinhaLista,
    dados_postback,
    estado_formulario,
    inferir_situacao_ne,
    ler_detalhe,
    ler_lista,
    parse_data,
    sigla,
    situacao_pregao,
)

DADOS = Path(__file__).resolve().parent.parent / "dados" / "fontes"


def _html(nome: str) -> str:
    return (DADOS / nome).read_text(encoding="utf-8")


LISTA_P1 = _html("dae-jundiai-lista-2026-p1.html")
LISTA_P3 = _html("dae-jundiai-lista-2026-p3.html")
LISTA_P9 = _html("dae-jundiai-lista-2026-p9.html")
LISTA_2025 = _html("dae-jundiai-lista-2025-p1.html")
LISTA_VAZIA = _html("dae-jundiai-lista-2031-vazia.html")
DETALHE_843 = _html("dae-jundiai-detalhe-843.html")
DETALHE_846 = _html("dae-jundiai-detalhe-846.html")
DETALHE_841 = _html("dae-jundiai-detalhe-841.html")

AGORA = datetime(2026, 11, 27, 15, 0, tzinfo=UTC)
HOJE = date(2026, 11, 27)


# --- parsers sobre as amostras reais ---------------------------------------------------------


def test_parse_data_formatos() -> None:
    assert parse_data("26/11/2026 16:30:00") == datetime(2026, 11, 26, 16, 30)
    assert parse_data("22/07/2026") == datetime(2026, 7, 22)
    assert parse_data("") is None
    assert parse_data("31/02/2026") is None


def test_lista_p1_do_get() -> None:
    lista = ler_lista(LISTA_P1)
    assert lista.exercicio == "2026"
    assert lista.pagina_atual == 1
    assert lista.paginas == frozenset(range(2, 10))
    assert not lista.vazia
    assert len(lista.linhas) == 10
    primeira = lista.linhas[0]
    assert primeira == LinhaLista(
        modalidade="ALIENACAO",
        numero="2026/00001",
        sistema="NE",
        cdcompra="847",
        caminho="CompraNEWeb.aspx?cdcompra=847",
        objeto="SERVICO DE LEILOEIRO PARA ALIENACAO DE INSERVIVEIS",
        inicio=datetime(2026, 10, 22),
        fim=None,
        icone="cp_configuracao",
        titulo_icone="",
    )
    assert lista.linhas[8].objeto.startswith("Execução de drenagem e enrocamento")


def test_lista_p3_traz_pregoes_com_prazo_e_icone() -> None:
    lista = ler_lista(LISTA_P3)
    assert lista.pagina_atual == 3
    assert {1, 2, 4, 9} <= lista.paginas and 3 not in lista.paginas
    primeira = lista.linhas[0]
    assert primeira.modalidade == "PREGÃO ELETRÔNICO"
    assert primeira.sistema == "PREGAO"
    assert primeira.caminho == "mural.aspx?cdcompra=567"
    assert primeira.inicio == datetime(2026, 11, 26, 16, 30)
    assert primeira.fim == datetime(2026, 12, 18, 9, 0)
    assert primeira.icone == "prop_preco_env"


def test_lista_p9_e_a_ultima() -> None:
    lista = ler_lista(LISTA_P9)
    assert lista.pagina_atual == 9
    assert 10 not in lista.paginas
    assert len(lista.linhas) == 3
    assert lista.linhas[1].icone == "cp_suspensa"
    assert lista.linhas[1].titulo_icone == "SUSPENSA - IMPUGNAÇÃO DO EDITAL"


def test_lista_da_busca_de_2025_tem_10_paginas() -> None:
    lista = ler_lista(LISTA_2025)
    assert lista.exercicio == "2025"
    assert 10 in lista.paginas
    assert lista.linhas[0].numero == "2025/00001"


def test_lista_vazia_tem_a_mensagem() -> None:
    lista = ler_lista(LISTA_VAZIA)
    assert lista.vazia and lista.exercicio == "2031" and not lista.linhas


def test_lista_sem_grade_nem_mensagem_e_layout() -> None:
    with pytest.raises(ErroFonte, match="sem a grade"):
        ler_lista(LISTA_VAZIA.replace(dae.SEM_REGISTROS, "outra coisa"))


def test_pagina_sem_formulario_e_layout() -> None:
    with pytest.raises(ErroFonte, match="exercício"):
        ler_lista("<html><body><table id='gdvCompras'></table></body></html>")


def test_estado_do_formulario_da_p1() -> None:
    estado = estado_formulario(LISTA_P1)
    assert estado == {
        "__VIEWSTATE": "viewstate-p1",
        "__VIEWSTATEGENERATOR": "1DFC8244",
        "__EVENTVALIDATION": "eventvalidation-p1",
        "txtObjeto": "",
        "txtNrexer": "2026",
        "txtnrlici": "",
        "ddlModalidade": "0",
        "ddlUnidadeCompradora": "0",
        "ddlSituacao": "0",
    }  # o botão Pesquisar não entra: paginação não clica em botão


def test_dados_postback_somam_o_evento() -> None:
    dados = dados_postback(LISTA_P1, "gdvCompras", "Page$2")
    assert dados["__EVENTTARGET"] == "gdvCompras"
    assert dados["__EVENTARGUMENT"] == "Page$2"
    assert dados["__VIEWSTATE"] == "viewstate-p1"


def test_dados_postback_sem_viewstate_e_layout() -> None:
    with pytest.raises(ErroFonte, match="__VIEWSTATE"):
        dados_postback(LISTA_P1.replace('value="viewstate-p1"', 'value=""'), "gdvCompras", "")


def test_detalhe_843_com_atas() -> None:
    detalhe = ler_detalhe(DETALHE_843, "843")
    assert detalhe.titulo == "MODO DE DISPUTA FECHADO - 2026/14 - PMJ"
    assert detalhe.objeto == "REMANEJ DE REDE NA RUA ELIAS JUVENAL POR METODO HDD"
    assert detalhe.situacao_sistema == "Em Configuracao"
    assert detalhe.publicacao == datetime(2026, 7, 22)
    assert len(detalhe.anexos) == 11
    assert detalhe.anexos[0] == {
        "nome": "EDITAL MDF 014 26.pdf",
        "tipo": "EDITAL DA COMPRA",
        "arquivo": "EDITAL MDF 014 26.pdf",
    }
    assert [a["tipo"] for a in detalhe.anexos].count("ATA") == 4
    assert [a["tipo"] for a in detalhe.anexos].count("PLANILHA") == 2


def test_detalhe_sem_titulo_e_layout() -> None:
    with pytest.raises(ErroFonte, match="sem título"):
        ler_detalhe("<html><body><form id='form1'></form></body></html>", "1")


def test_detalhe_de_outra_compra_e_layout() -> None:
    with pytest.raises(ErroFonte, match="pedi o detalhe de 846, veio o de 843"):
        ler_detalhe(DETALHE_843, "846")


def test_detalhe_sem_cdcompra_no_formulario_e_layout() -> None:
    sem_action = DETALHE_843.replace('action="./CompraNEWeb.aspx?cdcompra=843"', 'action=""')
    with pytest.raises(ErroFonte, match="sem o cdcompra"):
        ler_detalhe(sem_action, "843")


def test_detalhe_sem_a_grade_de_anexos_e_layout() -> None:
    sem_grade = DETALHE_846.replace('id="grdArquivos"', 'id="outraGrade"')
    assert sem_grade != DETALHE_846
    with pytest.raises(ErroFonte, match="sem a grade de anexos"):
        ler_detalhe(sem_grade, "846")


def test_detalhe_sem_nenhum_anexo_e_layout() -> None:
    with pytest.raises(ErroFonte, match="sem nenhum anexo"):
        ler_detalhe(
            _detalhe("846", "MODO DE DISPUTA FECHADO - 2026/17 - PMJ", "01/10/2026", []), "846"
        )


def test_titulo_do_detalhe_tem_de_ser_o_numero_da_linha() -> None:
    detalhe = ler_detalhe(DETALHE_846, "846")  # 2026/17
    dae.conferir_titulo(_linha(numero="2026/00017"), detalhe)
    for numero in ("2026/00014", "2025/00017"):
        with pytest.raises(ErroFonte, match="não é de"):
            dae.conferir_titulo(_linha(numero=numero), detalhe)


# --- situação ---------------------------------------------------------------------------------


def _linha(**campos: object) -> LinhaLista:
    base: dict[str, object] = {
        "modalidade": "MODO DE DISPUTA FECHADO",
        "numero": "2026/00017",
        "sistema": "NE",
        "cdcompra": "846",
        "caminho": "CompraNEWeb.aspx?cdcompra=846",
        "objeto": "OBRA",
        "inicio": datetime(2026, 9, 30),
        "fim": None,
        "icone": "cp_configuracao",
        "titulo_icone": "",
    }
    return LinhaLista(**{**base, **campos})  # type: ignore[arg-type]


def test_ne_sem_ata_recente_e_aberta() -> None:
    detalhe = ler_detalhe(DETALHE_846, "846")  # MDF 17/26, publicado em 01/10/2026, sem ata
    assert inferir_situacao_ne(_linha(), detalhe, HOJE) == (
        "Sem ata (sessão por realizar)",
        "aberta",
    )


def test_ne_sem_ata_antiga_e_encerrada() -> None:
    # Não "outra": a reconciliação nunca fecha "outra" e o edital ficaria aberto para sempre.
    detalhe = ler_detalhe(DETALHE_846, "846")
    _, situacao = inferir_situacao_ne(_linha(), detalhe, date(2027, 6, 1))
    assert situacao == "encerrada"


def test_ne_com_ata_antiga_e_encerrada_e_recente_em_julgamento() -> None:
    detalhe = ler_detalhe(DETALHE_843, "843")  # publicado em 22/07/2026, com 4 atas
    assert inferir_situacao_ne(_linha(), detalhe, HOJE)[1] == "encerrada"
    assert inferir_situacao_ne(_linha(), detalhe, date(2026, 8, 30))[1] == "em_julgamento"


def test_ne_com_aviso_de_revogacao_e_encerrada() -> None:
    detalhe = ler_detalhe(DETALHE_841, "841")  # MDA 2/26: SUSP e Revogacao como AVISO
    assert inferir_situacao_ne(_linha(), detalhe, HOJE) == (
        "Revogada ou anulada (aviso publicado)",
        "encerrada",
    )


@pytest.mark.parametrize(
    ("texto", "fim"),
    [
        ("Revogacao _ MDA 02 26.pdf", True),
        ("AVISO_DE_ANULAÇÃO.pdf", True),
        ("Cancelamento do certame", True),
        ("REVOGADA", True),
        ("Revogação da suspensão.pdf", False),
        ("REVOGACAO_DA_SUSPENSAO MDA 02 26.pdf", False),
        ("Cancelamento de suspensão", False),
        ("SUSP MDA 02 26.pdf", False),
        ("Suspensão do certame", False),
        ("Licitação não revogada", False),
        ("NAO ANULADA - segue o certame", False),
        ("Retificação do edital", False),
    ],
)
def test_aviso_de_fim(texto: str, fim: bool) -> None:
    assert dae.aviso_de_fim(texto) is fim


def test_ne_com_aviso_so_de_suspensao_ou_revogacao_da_suspensao_nao_encerra() -> None:
    detalhe = Detalhe(
        titulo="x",
        objeto="x",
        situacao_sistema="",
        publicacao=datetime(2026, 10, 1),
        anexos=[
            {"nome": "SUSP.pdf", "tipo": "AVISO", "arquivo": "SUSPENSAO.pdf"},
            {"nome": "Revogação da suspensão", "tipo": "AVISO", "arquivo": "R.pdf"},
        ],
    )
    assert inferir_situacao_ne(_linha(), detalhe, HOJE)[1] == "aberta"


def test_ne_icone_cancelada_vale() -> None:
    detalhe = Detalhe(titulo="x", objeto="x", situacao_sistema="", publicacao=None)
    assert inferir_situacao_ne(_linha(icone="cp_cancelada"), detalhe, HOJE)[1] == "encerrada"


@pytest.mark.parametrize(
    ("icone", "esperada"),
    [
        ("prop_preco_env", "aberta"),
        ("cp_negociacao", "em_julgamento"),
        ("intencao_recurso", "em_julgamento"),
        ("adjudicacao", "encerrada"),
        ("cp_homologada", "encerrada"),
        ("cp_cancelada", "encerrada"),
        ("cp_suspensa", "outra"),
    ],
)
def test_situacao_do_pregao_pelo_icone(icone: str, esperada: str) -> None:
    assert situacao_pregao(_linha(sistema="PREGAO", icone=icone))[1] == esperada


def test_pregao_titulo_do_icone_vira_a_situacao_original() -> None:
    linha = _linha(sistema="PREGAO", icone="cp_suspensa", titulo_icone="SUSPENSA - IMPUGNAÇÃO")
    assert situacao_pregao(linha) == ("SUSPENSA - IMPUGNAÇÃO", "outra")


def test_icone_desconhecido_e_outra_com_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert situacao_pregao(_linha(sistema="PREGAO", icone="novo"))[1] == "outra"
    assert "situacao_desconhecida" in caplog.messages


def test_siglas() -> None:
    assert sigla("MODO DE DISPUTA FECHADO") == "MDF"
    assert sigla("PREGÃO ELETRÔNICO") == "PE"
    assert sigla("CHAMADA PÚBLICA") == "CHP"
    assert sigla("Nova Coisa") == "NOVA-COISA"


# --- coleta com postback (páginas sintéticas) -------------------------------------------------

Linha = tuple[str, str, str, str, str, str, str]  # modalidade, sistema, cd, número, ini, fim, ícone


_ASPX = {"NE": "CompraNEWeb.aspx", "PREGAO": "mural.aspx"}


def _pagina(ano: str, atual: int, paginas: list[int], linhas: list[Linha], extra: str = "") -> str:
    trs = "".join(
        f"<tr><td><span id='g_lblsgcliente'>DAE</span></td>"
        f"<td><span id='g_lbldsmodal'>{m}</span></td>"
        f"<td><a id='g_lnkMural' href='{_ASPX[s]}?cdcompra={cd}'>"
        f"{n}</a></td><td><span id='g_lblObjeto'>OBJ {cd}</span></td>"
        f"<td><span id='g_lblDTInic'>{ini}</span></td><td><span id='g_lblDTFim'>{fim}</span></td>"
        f"<td><img id='g_imgSituacao' src='../Imagens/Icones/{ic}.gif'></td></tr>"
        for m, s, cd, n, ini, fim, ic in linhas
    )
    links = "".join(
        f"<td><span>{p}</span></td>"
        if p == atual
        else f"<td><a href=\"javascript:__doPostBack('gdvCompras','Page${p}')\">{p}</a></td>"
        for p in sorted({*paginas, atual})
    )
    pager = f"<tr class='pagination-ys'><td colspan='7'><table><tr>{links}</tr></table></td></tr>"
    return (
        f"<html><head><title>x</title></head><body>{extra}<form id='form1' method='post'>"
        f"<input type='hidden' name='__VIEWSTATE' value='vs-{ano}-{atual}'>"
        f"<input type='hidden' name='__EVENTVALIDATION' value='ev-{ano}-{atual}'>"
        f"<input name='txtNrexer' type='text' value='{ano}'>"
        f"<input type='submit' name='btnPesquisar' value='Pesquisar'>"
        f"<select name='ddlSituacao'><option selected='selected' value='0'>TODAS</option></select>"
        f"<div id='UpdatePanel2'><table id='gdvCompras'>{trs}{pager if paginas else ''}</table>"
        f"</div></form></body></html>"
    )


def _ne(cd: str, numero: str, inicio: str) -> Linha:
    return ("MODO DE DISPUTA FECHADO", "NE", cd, numero, inicio, "", "cp_configuracao")


def _pe(cd: str, numero: str, inicio: str, fim: str, icone: str = "prop_preco_env") -> Linha:
    return ("PREGÃO ELETRÔNICO", "PREGAO", cd, numero, inicio, fim, icone)


P2026_1 = _pagina("2026", 1, [2], [_ne("846", "2026/00017", "30/09/2026 00:00:00")])
P2026_2 = _pagina(
    "2026",
    2,
    [1],
    [_pe("567", "2026/00063", "26/11/2026 16:30:00", "18/12/2026 09:00:00")],
)
P2025_1 = _pagina(
    "2025",
    1,
    [],
    [
        _ne("826", "2025/00011", "16/12/2025 00:00:00"),
        _ne("805", "2025/00001", "12/02/2025 00:00:00"),  # fora da janela de 12 meses
    ],
)


def _detalhe(cd: str, titulo: str, publicacao: str, anexos: list[tuple[str, str]]) -> str:
    linhas = "".join(
        f"<tr class='rowtable'><td>{nome}</td><td>{tipo}</td><td><a>{nome}</a></td></tr>"
        for nome, tipo in anexos
    )
    return (
        f"<html><body><form id='form1' action='./CompraNEWeb.aspx?cdcompra={cd}'>"
        f"<span id='CompraNEMod1_lblTitulo'>{titulo}</span>"
        f"<span id='CompraNEMod1_lblDsobjeto'>OBJETO DO DETALHE</span>"
        f"<select id='CompraNEMod1_ddlSituacao'><option selected='selected' value='1'>"
        f"Em Configuracao</option></select><span id='CompraNEMod1_lblDtPubl'>{publicacao}</span>"
        f"<table id='grdArquivos'><tr><th>a</th><th>b</th><th>c</th></tr>{linhas}</table>"
        f"</form></body></html>"
    )


DETALHES = {
    "846": _detalhe(
        "846",
        "MODO DE DISPUTA FECHADO - 2026/17 - PMJ",
        "01/10/2026",
        [("E.pdf", "EDITAL DA COMPRA")],
    ),
    "826": _detalhe(
        "826", "MODO DE DISPUTA FECHADO - 2025/11 - PMJ", "17/12/2025", [("A.pdf", "ATA")]
    ),
}

Rota = Callable[[httpx.Request], str]


def _servidor(
    paginas: dict[tuple[str, int], str],
    primeira: str = P2026_1,
    detalhes: dict[str, str] = DETALHES,
) -> Rota:
    """Responde como o Compra Eletrônica: GET da lista, postback de página e de busca, detalhe."""

    def responder(requisicao: httpx.Request) -> str:
        if requisicao.method == "GET":
            if "cdcompra=" in str(requisicao.url):
                return detalhes[requisicao.url.params["cdcompra"]]
            return primeira
        corpo = {
            k: v[0]
            for k, v in parse_qs(requisicao.content.decode(), keep_blank_values=True).items()
        }
        if corpo.get("btnPesquisar"):
            return paginas[(corpo["txtNrexer"], 1)]
        assert corpo["__EVENTTARGET"] == "gdvCompras"
        pagina = int(corpo["__EVENTARGUMENT"].removeprefix("Page$"))
        return paginas[(corpo["txtNrexer"], pagina)]

    return responder


def _cliente(rota: Rota, chamadas: list[httpx.Request], pausas: list[float]) -> ClienteEducado:
    def tratar(requisicao: httpx.Request) -> httpx.Response:
        chamadas.append(requisicao)
        return httpx.Response(200, text=rota(requisicao))

    return ClienteEducado(
        httpx.Client(transport=httpx.MockTransport(tratar)),
        dormir=pausas.append,
        marcador_bloqueio=FonteDaeJundiai.marcador_bloqueio,
    )


def _coletar(
    rota: Rota, chamadas: list[httpx.Request] | None = None
) -> tuple[FonteDaeJundiai, list[PaginaColetada]]:
    fonte = FonteDaeJundiai(relogio=lambda: AGORA)
    chamadas = [] if chamadas is None else chamadas
    with _cliente(rota, chamadas, []) as cliente:
        return fonte, list(fonte.coletar(cliente))


PAGINAS_OK = {("2026", 2): P2026_2, ("2025", 1): P2025_1}


def test_coleta_pagina_por_postback_busca_o_ano_anterior_e_os_detalhes() -> None:
    chamadas: list[httpx.Request] = []
    pausas: list[float] = []
    fonte = FonteDaeJundiai(relogio=lambda: AGORA)
    with _cliente(_servidor(PAGINAS_OK), chamadas, pausas) as cliente:
        paginas = list(fonte.coletar(cliente))
    assert [p.url for p in paginas] == [
        f"{URL_LISTA}#exercicio=2026&pagina=1",
        f"{URL_LISTA}#exercicio=2026&pagina=2",
        f"{URL_LISTA}#exercicio=2025&pagina=1",
        f"{BASE}CompraNEWeb.aspx?cdcompra=846",
        f"{BASE}CompraNEWeb.aspx?cdcompra=826",  # o 805 está fora da janela: sem detalhe
    ]
    assert [c.method for c in chamadas] == ["GET", "POST", "POST", "GET", "GET"]
    pagina2 = parse_qs(chamadas[1].content.decode(), keep_blank_values=True)
    assert pagina2["__VIEWSTATE"] == ["vs-2026-1"]  # o estado da página anterior
    assert pagina2["__EVENTVALIDATION"] == ["ev-2026-1"]
    assert pagina2["__EVENTARGUMENT"] == ["Page$2"]
    assert "btnPesquisar" not in pagina2
    busca = parse_qs(chamadas[2].content.decode(), keep_blank_values=True)
    assert busca["txtNrexer"] == ["2025"] and busca["btnPesquisar"] == ["Pesquisar"]
    assert busca["__VIEWSTATE"] == ["vs-2026-2"]
    assert busca["__EVENTTARGET"] == [""]
    assert all(c.headers["User-Agent"].startswith("radar-licitacoes-estudo") for c in chamadas)
    assert pausas == [3.0] * 4  # cortesia entre todas as requisições, POST inclusive


def test_extrair_normaliza_ne_e_pregao_na_janela() -> None:
    fonte, paginas = _coletar(_servidor(PAGINAS_OK))
    editais = {e["id_na_fonte"]: e for e in fonte.extrair(paginas)}
    assert set(editais) == {"MDF-2026/00017", "PE-2026/00063", "MDF-2025/00011"}
    ne = editais["MDF-2026/00017"]
    assert ne["fonte"] == "dae_jundiai"
    assert ne["orgao_cnpj"] == "03582243000173"
    assert (ne["uf"], ne["municipio"]) == ("SP", "Jundiaí")
    assert ne["modalidade_nome"] == "Modo de Disputa Fechado"
    assert ne["objeto"] == "OBJETO DO DETALHE"
    assert ne["situacao_normalizada"] == "aberta"
    assert ne["data_publicacao"] == datetime(2026, 10, 1)
    assert ne["data_encerramento_proposta"] is None  # só no PDF do edital
    assert ne["link_origem"] == f"{BASE}CompraNEWeb.aspx?cdcompra=846"
    assert ne["anexos"] == [{"nome": "EDITAL DA COMPRA: E.pdf", "id": "E.pdf"}]
    assert ne["bruto"]["detalhe"]["anexos"][0]["tipo"] == "EDITAL DA COMPRA"
    pe = editais["PE-2026/00063"]
    assert pe["situacao_original"] == "Recebendo propostas"
    assert pe["situacao_normalizada"] == "aberta"
    assert pe["data_encerramento_proposta"] == datetime(2026, 12, 18, 9, 0)
    assert pe["data_publicacao"] == datetime(2026, 11, 26, 16, 30)
    assert pe["anexos"] == [] and pe["bruto"]["detalhe"] is None
    assert editais["MDF-2025/00011"]["situacao_normalizada"] == "encerrada"  # ata, > 90 dias


def test_extrair_sem_o_detalhe_de_um_ne_falha() -> None:
    fonte, paginas = _coletar(_servidor(PAGINAS_OK))
    with pytest.raises(ErroFonte, match="detalhe ausente"):
        fonte.extrair([p for p in paginas if "cdcompra=846" not in p.url])


def test_sessao_perdida_que_repete_a_pagina_falha() -> None:
    repetida = P2026_1.replace("<td><span>1</span></td>", "<td><span>2</span></td>")
    with pytest.raises(ErroFonte, match="repete a anterior"):
        _coletar(_servidor({**PAGINAS_OK, ("2026", 2): repetida}))


def test_pagina_corrente_diferente_da_pedida_falha() -> None:
    with pytest.raises(ErroFonte, match="pedi a página 2"):
        _coletar(_servidor({**PAGINAS_OK, ("2026", 2): P2026_1}))


def test_paginacao_sem_fim_para_no_limite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dae, "MAX_PAGINAS", 3)
    paginas = {
        ("2026", n): _pagina(
            "2026", n, [n + 1], [_pe(str(n), f"2026/{n:05}", "26/11/2026 10:00:00", "")]
        )
        for n in range(1, 5)
    }
    with pytest.raises(ErroFonte, match="lista sem fim"):
        _coletar(_servidor(paginas, primeira=paginas[("2026", 1)]))


def test_busca_ignorada_falha() -> None:
    with pytest.raises(ErroFonte, match="exercício '2026'"):
        _coletar(_servidor({**PAGINAS_OK, ("2025", 1): P2026_1}))


def test_exercicio_anterior_vazio_tenta_page_1_e_falha_se_continua_vazio() -> None:
    # A busca feita da página 9 do exercício corrente pode herdar o índice e responder vazia:
    # retrato que aceitasse isso fecharia na reconciliação todo o exercício anterior.
    vazia = LISTA_VAZIA.replace("2031", "2025")
    chamadas: list[httpx.Request] = []
    with pytest.raises(ErroFonte, match="exercício anterior 2025 sem registros"):
        _coletar(_servidor({**PAGINAS_OK, ("2025", 1): vazia}), chamadas)
    eventos = [
        parse_qs(c.content.decode(), keep_blank_values=True).get("__EVENTARGUMENT", [""])[0]
        for c in chamadas
        if c.method == "POST"
    ]
    assert eventos == ["Page$2", "", "Page$1"]  # uma nova tentativa, só uma


def test_exercicio_anterior_vazio_na_busca_e_cheio_no_page_1_segue() -> None:
    vazia = LISTA_VAZIA.replace("2031", "2025")
    servidor = _servidor(PAGINAS_OK)

    def busca_vazia(requisicao: httpx.Request) -> str:
        if b"btnPesquisar" in requisicao.content:
            return vazia
        return servidor(requisicao)

    fonte, paginas = _coletar(busca_vazia)
    assert {e["id_na_fonte"] for e in fonte.extrair(paginas)} >= {"MDF-2025/00011"}


def test_exercicio_corrente_vazio_e_aceito_mas_pagina_2_vazia_nao() -> None:
    # Janeiro: o exercício novo ainda não tem nada; só o corrente pode vir vazio.
    vazia_2026 = LISTA_VAZIA.replace("2031", "2026")
    _, paginas = _coletar(_servidor(PAGINAS_OK, primeira=vazia_2026))
    assert [p.url.rsplit("#", 1)[-1] for p in paginas[:2]] == [
        "exercicio=2026&pagina=1",
        "exercicio=2025&pagina=1",
    ]
    with pytest.raises(ErroFonte, match="página 2 de 2026 sem registros"):
        _coletar(_servidor({**PAGINAS_OK, ("2026", 2): vazia_2026}))


def test_grade_de_outro_exercicio_com_o_campo_certo_falha() -> None:
    # O WebForms devolve o txtNrexer digitado mesmo sem atualizar a grade.
    grade_2027 = _pagina(
        "2026", 2, [1], [_pe("600", "2027/00001", "26/11/2026 16:30:00", "18/12/2026 09:00:00")]
    )
    with pytest.raises(ErroFonte, match="números de outro exercício: 2027/00001"):
        _coletar(_servidor({**PAGINAS_OK, ("2026", 2): grade_2027}))
    with pytest.raises(ErroFonte, match="números de outro exercício"):
        _coletar(_servidor({**PAGINAS_OK, ("2025", 1): P2026_1.replace("'2026'", "'2025'")}))


def test_extrair_confere_o_exercicio_das_paginas_gravadas() -> None:
    fonte = FonteDaeJundiai(relogio=lambda: AGORA)
    gravada = PaginaColetada(f"{URL_LISTA}#exercicio=2025&pagina=2", "html", P2026_2)
    with pytest.raises(ErroFonte, match="exercício '2026'"):
        fonte.extrair([gravada])


def test_detalhe_de_outra_compra_no_retrato_falha() -> None:
    trocados = {**DETALHES, "846": DETALHES["826"]}
    fonte, paginas = _coletar(_servidor(PAGINAS_OK, detalhes=trocados))
    with pytest.raises(ErroFonte, match="pedi o detalhe de 846"):
        fonte.extrair(paginas)


def test_detalhe_com_titulo_de_outra_compra_no_retrato_falha() -> None:
    errado = _detalhe("846", "MODO DE DISPUTA FECHADO - 2026/18 - PMJ", "01/10/2026", [("E", "X")])
    fonte, paginas = _coletar(_servidor(PAGINAS_OK, detalhes={**DETALHES, "846": errado}))
    with pytest.raises(ErroFonte, match="não é de 2026/00017"):
        fonte.extrair(paginas)


def test_detalhe_sem_anexos_no_retrato_falha() -> None:
    vazio = _detalhe("846", "MODO DE DISPUTA FECHADO - 2026/17 - PMJ", "01/10/2026", [])
    fonte, paginas = _coletar(_servidor(PAGINAS_OK, detalhes={**DETALHES, "846": vazio}))
    with pytest.raises(ErroFonte, match="sem nenhum anexo"):
        fonte.extrair(paginas)


def test_pagina_de_bloqueio_para_o_retrato() -> None:
    bloqueio = "<html><head><title>Request Rejected</title></head><body>no</body></html>"
    with pytest.raises(ErroFonte, match="bloqueio"):
        _coletar(_servidor({**PAGINAS_OK, ("2026", 2): bloqueio}))


def test_http_de_erro_no_postback_para_o_retrato() -> None:
    def tratar(requisicao: httpx.Request) -> httpx.Response:
        if requisicao.method == "POST":
            return httpx.Response(500, text="Server Error")
        return httpx.Response(200, text=P2026_1)

    fonte = FonteDaeJundiai(relogio=lambda: AGORA)
    with ClienteEducado(
        httpx.Client(transport=httpx.MockTransport(tratar)), dormir=lambda _s: None
    ) as c:
        with pytest.raises(ErroFonte) as erro:
            list(fonte.coletar(c))
    assert erro.value.status == 500


def test_corte_e_de_12_meses_em_brasilia() -> None:
    fonte = FonteDaeJundiai(relogio=lambda: datetime(2027, 1, 1, 2, 0, tzinfo=UTC))
    assert fonte.hoje() == date(2026, 12, 31)  # 23h em Brasília
    assert fonte.corte() == date(2025, 12, 31)


def test_busca_que_herda_a_pagina_da_grade_volta_para_a_1() -> None:
    # Visto em 27/11/2026: a busca de 2025 feita da página 9 de 2026 já vem na página 9.
    p1 = _pagina("2025", 1, [2], [_ne("826", "2025/00011", "16/12/2025 00:00:00")])
    p2 = _pagina("2025", 2, [1], [_pe("487", "2025/00065", "20/12/2025 10:00:00", "")])
    servidor = _servidor({**PAGINAS_OK, ("2025", 1): p1, ("2025", 2): p2})

    def busca_na_pagina_2(requisicao: httpx.Request) -> str:
        if b"btnPesquisar" in requisicao.content:
            return p2
        return servidor(requisicao)

    chamadas: list[httpx.Request] = []
    _, paginas = _coletar(busca_na_pagina_2, chamadas)
    eventos = [
        parse_qs(c.content.decode(), keep_blank_values=True).get("__EVENTARGUMENT", [""])[0]
        for c in chamadas
        if c.method == "POST"
    ]
    assert eventos == ["Page$2", "", "Page$1", "Page$2"]
    assert [p.url.rsplit("#", 1)[-1] for p in paginas[:4]] == [
        "exercicio=2026&pagina=1",
        "exercicio=2026&pagina=2",
        "exercicio=2025&pagina=1",
        "exercicio=2025&pagina=2",
    ]


def test_primeira_pagina_fora_do_lugar_falha() -> None:
    with pytest.raises(ErroFonte, match="pedi a página 1"):
        _coletar(_servidor(PAGINAS_OK, primeira=P2026_2))


# --- PDF do edital: postback de download (aprovado em 28/11/2026, até 5 por rodada) -----------
# Desligado por padrão (`BAIXAR_EDITAIS_PDF`): o site real pede identificação. Os testes ligam.

CAPA = [
    "RECEBIMENTO DOS ENVELOPES",
    "DATA: 16 de dezembro de 2026",
    "HORÁRIO: Até as 09:30 horas (horário de Brasília/DF)",
    "SESSÃO PÚBLICA",
    "DATA: 16 de dezembro de 2026",
    "HORÁRIO: Até as 09:30 horas (horário de Brasília/DF)",
]
PDF_OK = pdf_com_texto([CAPA, ["corpo do edital"]])
PDF_AMBIGUO = pdf_com_texto(
    [["SESSÃO PÚBLICA DATA: 16/12/2026 HORÁRIO: 09:30", "SESSÃO PÚBLICA DATA: 17/12/2026"]]
)
SESSAO = datetime(2026, 12, 16, 9, 30)  # Brasília, sem fuso, como as datas da lista
ISO_SESSAO = "2026-12-16T09:30:00-03:00"
CONFERIDO = HOJE.isoformat()  # o relógio dos testes: 27/11/2026


def _detalhe_pdf(
    cd: str, numero: int, anexos: list[tuple[str, str]] | None = None, estado: bool = True
) -> str:
    """Detalhe com o estado do formulário e os links de postback, como o real."""
    anexos = anexos if anexos is not None else [(f"EDITAL {cd}.pdf", "EDITAL DA COMPRA")]
    linhas = "".join(
        f"<tr class='rowtable'><td>{nome}</td><td>{tipo}</td><td>"
        f"<a id='grdArquivos_ctl{i:02}_lnkArquivo' href=\"javascript:__doPostBack("
        f'&#39;grdArquivos$ctl{i:02}$lnkArquivo&#39;,&#39;&#39;)">{nome}   </a></td></tr>'
        for i, (nome, tipo) in enumerate(anexos, start=2)
    )
    campos = (
        f"<input type='hidden' name='__VIEWSTATE' value='vs-det-{cd}'>"
        f"<input type='hidden' name='__EVENTVALIDATION' value='ev-det-{cd}'>"
        if estado
        else ""
    )
    return (
        f"<html><body><form id='form1' method='post' action='./CompraNEWeb.aspx?cdcompra={cd}'>"
        f"{campos}"
        f"<select name='CompraNEMod1$ddlSituacao' id='CompraNEMod1_ddlSituacao' disabled>"
        f"<option selected value='1'>Em Configuracao</option></select>"
        f"<span id='CompraNEMod1_lblTitulo'>MODO DE DISPUTA FECHADO - 2026/{numero} - PMJ</span>"
        f"<span id='CompraNEMod1_lblDsobjeto'>OBRA {cd}</span>"
        f"<span id='CompraNEMod1_lblDtPubl'>01/10/2026</span>"
        f"<table id='grdArquivos'><tr><th>a</th><th>b</th><th>c</th></tr>{linhas}</table>"
        f"</form></body></html>"
    )


# Sete NE abertos (sem ata, publicados há menos de 180 dias), do mais velho ao mais novo.
ABERTOS = [(str(900 + n), 20 + n, f"{n + 1:02}/10/2026 00:00:00") for n in range(7)]
P2026_PDF = _pagina(
    "2026", 1, [], [_ne(cd, f"2026/{numero:05}", inicio) for cd, numero, inicio in ABERTOS]
)
DETALHES_PDF = {**DETALHES, **{cd: _detalhe_pdf(cd, numero) for cd, numero, _ in ABERTOS}}

Resposta = Callable[[httpx.Request], httpx.Response]


def _pdf_ok(cd: str) -> httpx.Response:
    return httpx.Response(
        200,
        content=PDF_OK,
        headers={
            "content-type": "application/pdf",
            "content-disposition": f'attachment; filename="EDITAL {cd}.pdf"',
        },
    )


def _servidor_pdf(
    pdf: Callable[[str], httpx.Response] = _pdf_ok,
    detalhes: dict[str, str] = DETALHES_PDF,
    primeira: str = P2026_PDF,
) -> Resposta:
    """O Compra Eletrônica com o download: o postback `lnkArquivo` devolve o PDF da compra."""
    lista = _servidor({("2025", 1): P2025_1}, primeira=primeira, detalhes=detalhes)

    def responder(requisicao: httpx.Request) -> httpx.Response:
        if _e_download(requisicao):
            return pdf(requisicao.url.params["cdcompra"])
        return httpx.Response(200, text=lista(requisicao))

    return responder


def _coletar_pdf(
    servidor: Resposta,
    datas: dict[tuple[str, str], dict[str, Any]] | None = None,
    chamadas: list[httpx.Request] | None = None,
    pausas: list[float] | None = None,
    baixar: bool = True,
) -> tuple[FonteDaeJundiai, list[PaginaColetada]]:
    feitas = [] if chamadas is None else chamadas
    dormidas = [] if pausas is None else pausas

    def tratar(requisicao: httpx.Request) -> httpx.Response:
        feitas.append(requisicao)
        return servidor(requisicao)

    fonte = FonteDaeJundiai(relogio=lambda: AGORA, datas_edital=datas, baixar_editais=baixar)
    with ClienteEducado(
        httpx.Client(transport=httpx.MockTransport(tratar), follow_redirects=True),
        dormir=dormidas.append,
        marcador_bloqueio=FonteDaeJundiai.marcador_bloqueio,
    ) as cliente:
        return fonte, list(fonte.coletar(cliente))


def _e_download(chamada: httpx.Request) -> bool:
    return chamada.method == "POST" and b"lnkArquivo" in chamada.content


def _downloads(chamadas: list[httpx.Request]) -> list[str]:
    return [c.url.params["cdcompra"] for c in chamadas if _e_download(c)]


def _editais(paginas: list[PaginaColetada]) -> dict[str, dict[str, Any]]:
    """As páginas `#edital=` por cdcompra, já lidas do JSON."""
    lidas = {}
    for pagina in paginas:
        if dae.MARCA_EDITAL in pagina.url:
            assert pagina.formato == "json"
            lidas[pagina.url.split("cdcompra=")[1].split("#")[0]] = json.loads(pagina.texto)
    return lidas


def _entrada(cd: str, data: str | None = ISO_SESSAO, conferido: str | None = CONFERIDO) -> Any:
    return {"arquivo": f"EDITAL {cd}.pdf", "data": data, "conferido_em": conferido}


def _por_cdcompra(editais: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(e["link_origem"]).rsplit("=", 1)[-1]: e for e in editais}


def test_edital_do_detalhe_pega_o_pdf_do_tipo_edital() -> None:
    assert dae.edital_do_detalhe(DETALHE_846) == (
        "MDF 017 26.pdf",
        "grdArquivos$ctl02$lnkArquivo",
    )
    so_planilha = _detalhe_pdf("900", 20, [("P.xls", "PLANILHA"), ("E.doc", "EDITAL DA COMPRA")])
    assert dae.edital_do_detalhe(so_planilha) is None
    retificado = _detalhe_pdf(
        "900",
        20,
        [
            ("E1.pdf", "EDITAL DA COMPRA"),
            ("A.pdf", "ANEXO DA COMPRA"),
            ("E2.pdf", "EDITAL DA COMPRA"),
        ],
    )
    assert dae.edital_do_detalhe(retificado) == ("E2.pdf", "grdArquivos$ctl04$lnkArquivo")


def test_estado_do_formulario_nao_manda_campo_desabilitado() -> None:
    # O navegador não envia o <select disabled> de situação do detalhe (visto no 846 real).
    estado = estado_formulario(DETALHE_846)
    assert "CompraNEMod1$ddlSituacao" not in estado
    assert estado["__VIEWSTATE"] == "viewstate-det-846"


def test_desligado_por_padrao_nao_baixa_nada() -> None:
    assert dae.BAIXAR_EDITAIS_PDF is False
    assert FonteDaeJundiai().baixar_editais is False
    chamadas: list[httpx.Request] = []
    _, paginas = _coletar_pdf(_servidor_pdf(), chamadas=chamadas, baixar=False)
    assert _downloads(chamadas) == [] and _editais(paginas) == {}


def test_download_logo_depois_do_detalhe_com_o_estado_dele_e_teto_de_5_mais_novos() -> None:
    chamadas: list[httpx.Request] = []
    pausas: list[float] = []
    fonte, paginas = _coletar_pdf(_servidor_pdf(), chamadas=chamadas, pausas=pausas)
    novos = [cd for cd, _, _ in reversed(ABERTOS)]  # 906 é o mais novo
    assert _downloads(chamadas) == novos[:5]
    for i, chamada in enumerate(chamadas):
        if not _e_download(chamada):
            continue
        cd = chamada.url.params["cdcompra"]
        anterior = chamadas[i - 1]  # o GET do próprio detalhe, logo antes
        assert anterior.method == "GET" and anterior.url.params["cdcompra"] == cd
        corpo = parse_qs(chamada.content.decode(), keep_blank_values=True)
        assert corpo["__VIEWSTATE"] == [f"vs-det-{cd}"]
        assert corpo["__EVENTVALIDATION"] == [f"ev-det-{cd}"]
        assert corpo["__EVENTTARGET"] == ["grdArquivos$ctl02$lnkArquivo"]
        assert corpo["__EVENTARGUMENT"] == [""]
        assert "CompraNEMod1$ddlSituacao" not in corpo  # disabled
        assert str(chamada.url) == f"{BASE}CompraNEWeb.aspx?cdcompra={cd}"
    assert all(c.headers["User-Agent"].startswith("radar-licitacoes-estudo") for c in chamadas)
    assert pausas == [3.0] * (len(chamadas) - 1)  # cortesia também no download
    assert _editais(paginas) == {cd: _entrada(cd) for cd in novos[:5]}
    editais = _por_cdcompra(fonte.extrair(paginas))
    for cd in novos[:5]:
        assert editais[cd]["data_abertura_proposta"] == SESSAO
        assert editais[cd]["data_encerramento_proposta"] == SESSAO
        assert editais[cd]["bruto"]["detalhe"]["data_sessao_edital"] == _entrada(cd)
    for cd in novos[5:]:  # passaram do teto: ficam para a próxima rodada
        assert editais[cd]["data_encerramento_proposta"] is None
        assert "data_sessao_edital" not in editais[cd]["bruto"]["detalhe"]
    assert "data_sessao_edital" not in editais["826"]["bruto"]["detalhe"]  # com ata: não baixa


def test_cache_nao_baixa_de_novo_e_volta_ao_retrato() -> None:
    datas = {
        ("906", "EDITAL 906.pdf"): _entrada("906", "2026-12-20T10:00:00-03:00", "2026-11-01"),
        ("905", "EDITAL 905.pdf"): _entrada("905", None),  # nulo conferido hoje: não baixa
        ("904", "OUTRO NOME.pdf"): _entrada("904"),  # retificado com outro nome: baixa de novo
        ("903", "EDITAL 903.pdf"): _entrada("903", "2026-12-20T10:00:00", None),  # iso sem fuso
    }
    chamadas: list[httpx.Request] = []
    fonte, paginas = _coletar_pdf(_servidor_pdf(), datas=datas, chamadas=chamadas)
    assert _downloads(chamadas) == ["904", "902", "901", "900"]  # o cache não conta no teto
    assert _editais(paginas)["905"] == _entrada("905", None)
    editais = _por_cdcompra(fonte.extrair(paginas))
    assert editais["906"]["data_encerramento_proposta"] == datetime(2026, 12, 20, 10, 0)
    assert (
        editais["906"]["bruto"]["detalhe"]["data_sessao_edital"] == datas[("906", "EDITAL 906.pdf")]
    )  # a entrada volta igual (conferido_em não muda sem reler)
    assert editais["905"]["data_encerramento_proposta"] is None
    assert editais["905"]["bruto"]["detalhe"]["data_sessao_edital"] == _entrada("905", None)
    assert editais["904"]["data_encerramento_proposta"] == SESSAO
    assert editais["903"]["data_encerramento_proposta"] == datetime(2026, 12, 20, 10, 0)


def test_cache_vencido_rele_o_edital() -> None:
    # Data já passada com o NE ainda aberto, ou nulo, conferidos há DIAS_RECONFERIR ou mais:
    # relê (retificação com o mesmo nome). Conferido há menos: não relê.
    velho = "2026-11-20"  # 7 dias antes de HOJE
    recente = "2026-11-21"
    datas = {
        ("906", "EDITAL 906.pdf"): _entrada("906", "2026-11-25T09:30:00-03:00", velho),
        ("905", "EDITAL 905.pdf"): _entrada("905", None, velho),
        ("904", "EDITAL 904.pdf"): _entrada("904", "2026-11-25T09:30:00-03:00", recente),
        ("903", "EDITAL 903.pdf"): _entrada("903", None, None),  # sem conferido: relê
        ("902", "EDITAL 902.pdf"): {"data": "ontem"},  # ilegível: relê
    }
    chamadas: list[httpx.Request] = []
    fonte, paginas = _coletar_pdf(_servidor_pdf(), datas=datas, chamadas=chamadas)
    assert _downloads(chamadas) == ["906", "905", "903", "902", "901"]
    lidas = _editais(paginas)
    assert lidas["906"] == _entrada("906") and lidas["905"] == _entrada("905")
    assert lidas["904"] == datas[("904", "EDITAL 904.pdf")]
    # O teto adiou o 900: ficou sem página (não estava no cache).
    assert "900" not in lidas
    assert _por_cdcompra(fonte.extrair(paginas))["906"]["data_encerramento_proposta"] == SESSAO


def test_cache_vencido_adiado_pelo_teto_ou_releitura_que_falha_continua_no_retrato() -> None:
    velha = _entrada("900", "2026-11-25T09:30:00-03:00", "2026-11-01")
    datas = {
        ("900", "EDITAL 900.pdf"): velha,
        ("906", "EDITAL 906.pdf"): _entrada("906", "2026-11-25T09:30:00-03:00", "2026-11-01"),
    }

    def falha_906(cd: str) -> httpx.Response:
        return httpx.Response(500, text="erro") if cd == "906" else _pdf_ok(cd)

    chamadas: list[httpx.Request] = []
    _, paginas = _coletar_pdf(_servidor_pdf(falha_906), datas=datas, chamadas=chamadas)
    assert _downloads(chamadas) == ["906", "905", "904", "903", "902"]  # 900 adiado pelo teto
    lidas = _editais(paginas)
    assert lidas["900"] == velha  # adiado: a entrada anterior segue
    assert lidas["906"] == datas[("906", "EDITAL 906.pdf")]  # releitura falhou: idem


def _falhas(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.message == "dae_jundiai_edital_falhou"]


PDF_QUEBRADO = b"%PDF-1.4\nlixo"
NOME_906 = {"content-disposition": 'attachment; filename="EDITAL 906.pdf"'}


@pytest.mark.parametrize(
    ("status", "corpo", "headers", "guarda", "motivo"),
    [
        (500, b"Server Error", {}, False, "HTTP 500"),
        (404, b"Not Found", {}, False, "HTTP 404"),
        (200, b"<html><title>Compra Aberta</title></html>", {}, False, "não é PDF"),
        (200, PDF_QUEBRADO, {}, True, "PDF ilegível"),
        (200, PDF_AMBIGUO, NOME_906, True, "ambígua"),
        (200, pdf_com_texto([["sem data nenhuma"]]), {}, True, "ambígua"),
        (
            200,
            PDF_OK,
            {"content-disposition": "attachment; filename=ANEXO.pdf"},
            False,
            "veio o arquivo 'ANEXO.pdf'",
        ),
    ],
)
def test_falha_do_pdf_nao_derruba_o_retrato(
    status: int,
    corpo: bytes,
    headers: dict[str, str],
    guarda: bool,
    motivo: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def pdf(cd: str) -> httpx.Response:
        if cd == "906":
            return httpx.Response(status, content=corpo, headers=headers)
        return _pdf_ok(cd)

    with caplog.at_level(logging.INFO):
        fonte, paginas = _coletar_pdf(_servidor_pdf(pdf))
    [registro] = _falhas(caplog)
    assert registro.__dict__["fonte"] == "dae_jundiai"
    assert registro.__dict__["cdcompra"] == "906"
    assert motivo in registro.__dict__["motivo"]
    # Nulo que o mesmo arquivo repetiria vai para o cache; rede, HTTP e outro arquivo, não.
    assert _editais(paginas).get("906") == (_entrada("906", None) if guarda else None)
    editais = _por_cdcompra(fonte.extrair(paginas))
    assert len(editais) == 8  # o retrato inteiro
    assert editais["906"]["data_encerramento_proposta"] is None
    assert editais["905"]["data_encerramento_proposta"] == SESSAO


def test_nao_e_pdf_loga_o_titulo_da_resposta(caplog: pytest.LogCaptureFixture) -> None:
    html = b"<html><head><title>  Compra\n Aberta </title></head></html>"
    with caplog.at_level(logging.WARNING):
        _coletar_pdf(_servidor_pdf(lambda _cd: httpx.Response(200, content=html)))
    assert "'Compra Aberta'" in _falhas(caplog)[0].__dict__["motivo"]


def test_nome_do_content_disposition() -> None:
    assert dae.nome_no_content_disposition('attachment; filename="MDF 017 26.pdf"') == (
        "MDF 017 26.pdf"
    )
    assert dae.nome_no_content_disposition("attachment; filename=E.pdf; size=3") == "E.pdf"
    assert (
        dae.nome_no_content_disposition(
            "attachment; filename=x.pdf; filename*=UTF-8''EDITAL%20N%C2%BA%201.pdf"
        )
        == "EDITAL Nº 1.pdf"
    )
    assert dae.nome_no_content_disposition("inline") is None


def test_nome_igual_com_espacos_e_caixa_diferentes_vale() -> None:
    def pdf(cd: str) -> httpx.Response:
        return httpx.Response(
            200,
            content=PDF_OK,
            headers={"content-disposition": f"attachment; filename=edital%20{cd}.PDF"},
        )

    _, paginas = _coletar_pdf(_servidor_pdf(pdf))
    assert _editais(paginas)["906"] == _entrada("906")


def test_erro_de_rede_no_download_nao_derruba_o_retrato(caplog: pytest.LogCaptureFixture) -> None:
    servidor = _servidor_pdf()

    def rede_caiu(requisicao: httpx.Request) -> httpx.Response:
        if _e_download(requisicao):
            raise httpx.ConnectError("caiu", request=requisicao)
        return servidor(requisicao)

    with caplog.at_level(logging.WARNING):
        fonte, paginas = _coletar_pdf(rede_caiu)
    assert len(_falhas(caplog)) == 5 and not _editais(paginas)
    assert len(fonte.extrair(paginas)) == 8


@pytest.mark.parametrize("status", [403, 429])
def test_403_ou_429_no_download_derruba_o_retrato(status: int) -> None:
    with pytest.raises(ErroFonte) as erro:
        _coletar_pdf(_servidor_pdf(lambda _cd: httpx.Response(status, text="no")))
    assert erro.value.status == status


def test_pagina_de_bloqueio_no_download_derruba_o_retrato() -> None:
    bloqueio = "<html><head><title>Access Denied</title></head></html>"
    with pytest.raises(ErroFonte, match="bloqueio"):
        _coletar_pdf(_servidor_pdf(lambda _cd: httpx.Response(200, text=bloqueio)))


def test_redirecionamento_vira_outro_get_com_pausa() -> None:
    # Mesmo com o httpx configurado para seguir, o download não segue na mesma requisição.
    servidor = _servidor_pdf()

    def redireciona(requisicao: httpx.Request) -> httpx.Response:
        if _e_download(requisicao):
            cd = requisicao.url.params["cdcompra"]
            return httpx.Response(302, headers={"location": f"/dae/aberto/Arquivo.aspx?cd={cd}"})
        if requisicao.url.path.endswith("Arquivo.aspx"):
            return _pdf_ok(requisicao.url.params["cd"])
        return servidor(requisicao)

    chamadas: list[httpx.Request] = []
    pausas: list[float] = []
    fonte, paginas = _coletar_pdf(redireciona, chamadas=chamadas, pausas=pausas)
    i = next(i for i, c in enumerate(chamadas) if _e_download(c))
    seguinte = chamadas[i + 1]
    assert seguinte.method == "GET"
    assert str(seguinte.url) == (
        "https://compraeletronica.jundiai.sp.gov.br/dae/aberto/Arquivo.aspx?cd=906"
    )
    assert pausas == [3.0] * (len(chamadas) - 1)  # o GET do redirect também espera
    assert _editais(paginas)["906"] == _entrada("906")


def test_identificacao_do_requerente_para_os_downloads_da_rodada(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # O que o site real respondeu em 28/11/2026: 302 para a identificação (facultativa, botão
    # "Pular"). Não segue, não tenta outro edital na mesma rodada e não guarda nada.
    def identificacao(cd: str) -> httpx.Response:
        destino = f"/dae/aberto/CompraNEIdentificacao.aspx?cdcompra={cd}&sq=1"
        return httpx.Response(302, headers={"location": destino}, text="Object moved")

    chamadas: list[httpx.Request] = []
    datas = {("900", "EDITAL 900.pdf"): _entrada("900", None, "2026-01-01")}  # vencida
    with caplog.at_level(logging.INFO):
        fonte, paginas = _coletar_pdf(_servidor_pdf(identificacao), datas=datas, chamadas=chamadas)
    assert _downloads(chamadas) == ["906"]
    assert not any("Identificacao" in str(c.url) for c in chamadas)
    assert "identificação" in _falhas(caplog)[0].__dict__["motivo"]
    assert _editais(paginas) == {"900": datas[("900", "EDITAL 900.pdf")]}  # a anterior segue
    resumo = next(r for r in caplog.records if r.message == "dae_jundiai_editais_pdf")
    assert resumo.__dict__["identificacao"] is True
    assert len(fonte.extrair(paginas)) == 8


def test_content_length_acima_do_teto_para_antes_de_ler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dae, "MAX_BYTES_PDF", 1000)
    lidos: list[int] = []

    def grande(cd: str) -> httpx.Response:
        def corpo() -> Any:
            for _ in range(100):
                lidos.append(1)
                yield b"x" * 1000

        cabecalho = {"content-length": "100000"} if cd == "906" else {}
        return httpx.Response(200, content=corpo(), headers=cabecalho)

    fonte, paginas = _coletar_pdf(_servidor_pdf(grande))
    lidas = _editais(paginas)
    assert lidas["906"] == _entrada("906", None)  # grande demais: nulo guardado
    assert lidas["905"] == _entrada("905", None)  # sem Content-Length: parou no streaming
    assert len(lidos) <= 5 * 2  # nenhum download leu o corpo inteiro (100 partes)
    assert len(fonte.extrair(paginas)) == 8


def test_detalhe_sem_viewstate_isola_a_falha(caplog: pytest.LogCaptureFixture) -> None:
    sem_estado = {**DETALHES_PDF, "906": _detalhe_pdf("906", 26, estado=False)}
    chamadas: list[httpx.Request] = []
    with caplog.at_level(logging.WARNING):
        fonte, paginas = _coletar_pdf(_servidor_pdf(detalhes=sem_estado), chamadas=chamadas)
    assert "906" not in _downloads(chamadas)
    assert "__VIEWSTATE" in _falhas(caplog)[0].__dict__["motivo"]
    assert "906" not in _editais(paginas) and len(fonte.extrair(paginas)) == 8


def test_linha_sem_data_inicial_fica_por_ultimo() -> None:
    linhas = [_ne(cd, f"2026/{numero:05}", inicio) for cd, numero, inicio in ABERTOS]
    linhas[6] = _ne("906", "2026/00026", "")  # o "mais novo" sem data
    chamadas: list[httpx.Request] = []
    _coletar_pdf(_servidor_pdf(primeira=_pagina("2026", 1, [], linhas)), chamadas=chamadas)
    assert _downloads(chamadas) == ["905", "904", "903", "902", "901"]


def test_ne_que_nao_esta_aberto_nao_baixa() -> None:
    com_ata = {
        cd: _detalhe_pdf(cd, numero, [(f"E{cd}.pdf", "EDITAL DA COMPRA"), ("A.pdf", "ATA")])
        for cd, numero, _ in ABERTOS
    }
    chamadas: list[httpx.Request] = []
    _coletar_pdf(_servidor_pdf(detalhes={**DETALHES, **com_ata}), chamadas=chamadas)
    assert _downloads(chamadas) == []


def test_pdf_grande_demais_fica_nulo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dae, "MAX_BYTES_PDF", 100)
    with pytest.raises(dae.FalhaEdital, match="grande demais") as falha:
        dae.texto_do_pdf(PDF_OK)
    assert falha.value.guardar


def test_so_as_3_primeiras_paginas_do_pdf_sao_lidas() -> None:
    texto = dae.texto_do_pdf(pdf_com_texto([["p1"], ["p2"], ["p3"], CAPA]))
    assert "p3" in texto and "SESS" not in texto


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="sem SIGALRM (Windows)")
def test_leitura_do_pdf_tem_limite_de_tempo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dae, "LIMITE_PDF_S", 0.05)

    class Lento:
        def __init__(self, *_: object) -> None:
            time.sleep(1)

    monkeypatch.setattr(dae, "PdfReader", Lento)
    with pytest.raises(dae.FalhaEdital, match="TimeoutError") as falha:
        dae.texto_do_pdf(PDF_OK)
    assert falha.value.guardar


def test_limite_de_tempo_e_inocuo_onde_nao_ha_sinal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(signal, "setitimer", raising=False)
    assert "SESS" in dae.texto_do_pdf(PDF_OK)


def test_pagina_de_edital_gravada_ilegivel_falha_no_extrair() -> None:
    fonte, paginas = _coletar_pdf(_servidor_pdf())
    for texto in ("x", '{"arquivo": "outro.pdf", "data": null}', '{"arquivo": "E.pdf", "data": 1}'):
        url = f"{BASE}CompraNEWeb.aspx?cdcompra=906#edital=E.pdf"
        quebrada = PaginaColetada(url, "json", texto)
        with pytest.raises(ErroFonte, match="página de edital ilegível"):
            fonte.extrair([*paginas, quebrada])
