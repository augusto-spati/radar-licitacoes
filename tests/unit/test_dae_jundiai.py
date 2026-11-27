import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

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
