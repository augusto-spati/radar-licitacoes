import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from radar.coleta import ErroFonte, PaginaColetada
from radar.coleta.sanepar import (
    FonteSanepar,
    ler_detalhe,
    ler_lista,
    parse_data,
)

DADOS = Path(__file__).resolve().parent.parent / "dados" / "fontes"
BASE = "https://www.sanepar.com.br"


def _html(nome: str) -> str:
    return (DADOS / nome).read_text(encoding="utf-8")


LISTA_P1 = _html("lista-oe-abertura-p1.html")
LISTA_P2 = _html("lista-oe-abertura-p2.html")
LISTA_VAZIA = _html("lista-oe-abertura-p4-vazia.html")
DETALHE_345 = _html("detalhe-345-26-vazamentos.html")
DETALHE_349 = _html("detalhe-349-26-vazamentos.html")


def test_parse_data_formatos() -> None:
    assert parse_data("18/12/2026 às 10h30") == datetime(2026, 12, 18, 10, 30)
    assert parse_data("18/12/2026 às 10:30") == datetime(2026, 12, 18, 10, 30)
    assert parse_data("18/12/2026") == datetime(2026, 12, 18, 0, 0)  # sem hora: meia-noite
    assert parse_data("") is None
    assert parse_data("sem data") is None


def test_parse_data_intervalo_vale_o_fim() -> None:
    assert parse_data("16/11/2026 até 15/12/2026 às 09h30") == datetime(2026, 12, 15, 9, 30)


def test_lista_p1_tem_20_linhas_e_a_primeira_confere() -> None:
    lista = ler_lista(LISTA_P1)
    assert lista.total == 57
    assert len(lista.linhas) == 20
    primeira = lista.linhas[0]
    assert primeira.numero == "352/26"
    assert primeira.caminho_detalhe == "/licitacao/detalhe/352/26"
    assert primeira.objeto.startswith("LOTE UNICO CONTRATACAO DE SERVICOS NO AMBITO")
    assert primeira.objeto.endswith("ESTACOES ELEVATORIAS DE AGUA (EEA).")
    assert primeira.modalidade == "Licitação Eletrônica"
    assert primeira.data_publicacao == datetime(2026, 11, 24)
    assert primeira.data_abertura == datetime(2026, 12, 18, 10, 30)
    assert primeira.situacao == "Abertura"


def test_lista_p2_nao_repete_ids_da_p1() -> None:
    ids1 = {linha.numero for linha in ler_lista(LISTA_P1).linhas}
    ids2 = {linha.numero for linha in ler_lista(LISTA_P2).linhas}
    assert ids2 and not ids1 & ids2


def test_lista_vazia_do_fim_da_paginacao_tem_zero_linhas() -> None:
    assert ler_lista(LISTA_VAZIA).linhas == []


def test_lista_sem_contador_nem_aviso_de_vazio_e_erro_de_layout() -> None:
    with pytest.raises(ErroFonte, match="layout: lista sem contador"):
        ler_lista("<html><body><table><tbody></tbody></table></body></html>")


def test_detalhe_345() -> None:
    detalhe = ler_detalhe(DETALHE_345)
    assert detalhe.titulo == "LICITACAO ELETRONICA No 345/26"
    assert detalhe.situacao == "Abertura"
    assert detalhe.objeto.startswith("EXECUCAO DE SERVICOS ESPECIALIZADOS DE PESQUISA E DETECCAO")
    assert detalhe.objeto.endswith("CONFORME DETALHADO NOS ANEXOS DO EDITAL.")
    assert detalhe.modalidade == "LICITACAO ELETRONICA"
    assert detalhe.data_publicacao == datetime(2026, 11, 16)
    assert detalhe.data_abertura == datetime(2026, 12, 15, 10, 30)
    assert detalhe.propostas == datetime(2026, 12, 15, 9, 30)
    assert detalhe.anexos == [
        {"nome": "EDITAL LE 345 2026", "id": "221507"},
        {"nome": "AVISO LE 345 2026", "id": "221508"},
        {"nome": "EDITAL_ELEMENTOS.ZIP", "id": "LC3452026Edital_Elementos.zip"},
    ]


def test_detalhe_349_propostas_e_abertura_distintas() -> None:
    detalhe = ler_detalhe(DETALHE_349)
    assert detalhe.propostas == datetime(2026, 12, 18, 14, 0)
    assert detalhe.data_abertura == datetime(2026, 12, 18, 15, 0)


def test_detalhe_sem_titulo_e_erro_de_layout() -> None:
    with pytest.raises(ErroFonte, match="layout: detalhe sem título"):
        ler_detalhe("<html><body><p>oi</p></body></html>")


def _paginas(detalhe: str = DETALHE_345) -> list[PaginaColetada]:
    # O detalhe 345/26 é ligado à linha 352/26 só para testar o casamento pela URL.
    return [
        PaginaColetada(f"{BASE}/licitacoes?Tipo=OE&Situacao=Abertura&page=0", "html", LISTA_P1),
        PaginaColetada(f"{BASE}/licitacao/detalhe/352/26", "html", detalhe),
    ]


def test_extrair_junta_lista_e_detalhe() -> None:
    normalizados = FonteSanepar().extrair(_paginas())
    assert len(normalizados) == 20
    por_id = {n["id_na_fonte"]: n for n in normalizados}
    assert all(n["fonte"] == "sanepar" for n in normalizados)

    completo = por_id["OE-352/26"]
    assert completo["objeto"].startswith("EXECUCAO DE SERVICOS ESPECIALIZADOS")  # do detalhe
    assert completo["modalidade_nome"] == "LICITACAO ELETRONICA"
    assert completo["situacao_original"] == "Abertura"
    assert completo["situacao_normalizada"] == "aberta"
    assert completo["uf"] == "PR"
    assert completo["orgao_nome"] == "SANEPAR"
    assert completo["orgao_cnpj"] == "76484013000145"
    assert completo["data_publicacao"] == datetime(2026, 11, 16)  # detalhe vence a lista
    assert completo["data_abertura_proposta"] == datetime(2026, 12, 15, 10, 30)
    assert completo["data_encerramento_proposta"] == datetime(2026, 12, 15, 9, 30)  # Propostas
    assert completo["link_origem"] == f"{BASE}/licitacao/detalhe/352/26"
    assert [a["id"] for a in completo["anexos"]][:2] == ["221507", "221508"]
    assert completo["bruto"]["lista"]["numero"] == "352/26"
    assert completo["bruto"]["detalhe"]["propostas"] == "2026-12-15T09:30:00"

    # sem detalhe, cai para a lista: encerramento = abertura
    sem_detalhe = por_id["OE-359/26"]
    assert sem_detalhe["data_encerramento_proposta"] == datetime(2026, 12, 16, 10, 15)
    assert sem_detalhe["anexos"] == []
    assert sem_detalhe["objeto"].startswith("EXECUCAO DE OBRA DE INTERLIGACAO")


def test_encerramento_cai_para_a_abertura_sem_propostas() -> None:
    sem_propostas = DETALHE_345.replace(
        '<span class="sli-info-label">Propostas</span>', '<span class="sli-info-label">X</span>'
    )
    por_id = {n["id_na_fonte"]: n for n in FonteSanepar().extrair(_paginas(sem_propostas))}
    # a abertura do detalhe (15/12 10h30) vale para o encerramento
    assert por_id["OE-352/26"]["data_encerramento_proposta"] == datetime(2026, 12, 15, 10, 30)


def test_situacao_julgamento_vira_em_julgamento() -> None:
    html = LISTA_P1.replace("edt-badge--abertura", "edt-badge--julgamento").replace(
        "Abertura\n", "Julgamento\n"
    )
    normalizados = FonteSanepar().extrair(
        [PaginaColetada(f"{BASE}/licitacoes?Tipo=OE&Situacao=Julgamento&page=0", "html", html)]
    )
    assert {n["situacao_normalizada"] for n in normalizados} == {"em_julgamento"}
    assert {n["situacao_original"] for n in normalizados} == {"Julgamento"}


def test_minimo_e_nome() -> None:
    fonte = FonteSanepar()
    assert fonte.nome == "sanepar"
    assert fonte.minimo_registros == 5


# --- coletar ---------------------------------------------------------------------------------


class _Resposta:
    def __init__(self, texto: str) -> None:
        self.text = texto


class _ClienteFalso:
    def __init__(self, respostas: dict[str, str | Exception]) -> None:
        self._respostas = respostas
        self.urls: list[str] = []

    def get(self, url: str, params: dict[str, Any] | None = None) -> _Resposta:
        assert params is None
        self.urls.append(url)
        resposta = self._respostas[url]
        if isinstance(resposta, Exception):
            raise resposta
        return _Resposta(resposta)


def _com_total(html: str, total: int) -> str:
    return html.replace("57 editais encontrados", f"{total} editais encontrados")


def _sem_linhas(html: str) -> str:
    """A mesma página com o contador, mas sem nenhuma linha na tabela."""
    ini = html.index("<tbody>") + len("<tbody>")
    return html[:ini] + html[html.index("</tbody>") :]


def _lista_url(situacao: str, pagina: int) -> str:
    return f"{BASE}/licitacoes?Tipo=OE&Situacao={situacao}&page={pagina}"


def _detalhes_de(html: str) -> dict[str, str | Exception]:
    return {f"{BASE}{linha.caminho_detalhe}": DETALHE_345 for linha in ler_lista(html).linhas}


def _julgamento(html: str) -> str:
    return html.replace("edt-badge--abertura", "edt-badge--julgamento").replace(
        "Abertura\n", "Julgamento\n"
    )


def test_coletar_pagina_ate_a_lista_vazia_e_busca_todos_os_detalhes() -> None:
    respostas = (
        {
            _lista_url("Abertura", 0): _com_total(LISTA_P1, 40),
            _lista_url("Abertura", 1): _com_total(LISTA_P2, 40),
            _lista_url("Abertura", 2): LISTA_VAZIA,
            _lista_url("Julgamento", 0): LISTA_VAZIA,
        }
        | _detalhes_de(LISTA_P1)
        | _detalhes_de(LISTA_P2)
    )
    cliente = _ClienteFalso(respostas)

    paginas = list(FonteSanepar().coletar(cliente))  # type: ignore[arg-type]

    ids = [linha.numero for linha in ler_lista(LISTA_P1).linhas + ler_lista(LISTA_P2).linhas]
    detalhes = [f"{BASE}/licitacao/detalhe/{n}" for n in ids]
    assert cliente.urls == [
        _lista_url("Abertura", 0),
        _lista_url("Abertura", 1),
        _lista_url("Abertura", 2),
        _lista_url("Julgamento", 0),
        *detalhes,
    ]
    assert [p.url for p in paginas] == cliente.urls
    assert all(p.formato == "html" for p in paginas)
    assert len(set(detalhes)) == 40


def test_coletar_para_se_a_pagina_repete_os_ids_da_anterior() -> None:
    respostas = {
        _lista_url("Abertura", 0): _com_total(LISTA_P1, 20),
        _lista_url("Abertura", 1): _com_total(LISTA_P1, 20),  # servidor ignorou `page`
        _lista_url("Julgamento", 0): LISTA_VAZIA,
    } | _detalhes_de(LISTA_P1)
    cliente = _ClienteFalso(respostas)

    paginas = list(FonteSanepar().coletar(cliente))  # type: ignore[arg-type]

    assert _lista_url("Abertura", 2) not in cliente.urls
    assert len([p for p in paginas if "/licitacoes?" in p.url]) == 2  # a repetida é descartada
    assert len([p for p in paginas if "/licitacao/detalhe/" in p.url]) == 20


def test_coletar_lista_sem_contador_para_com_erro() -> None:
    cliente = _ClienteFalso({_lista_url("Abertura", 0): "<html></html>"})
    with pytest.raises(ErroFonte, match="layout"):
        list(FonteSanepar().coletar(cliente))  # type: ignore[arg-type]


def test_coletar_contador_sem_linhas_na_pagina_1_nao_trunca() -> None:
    cliente = _ClienteFalso(
        {
            _lista_url("Abertura", 0): LISTA_P1,
            _lista_url("Abertura", 1): _sem_linhas(LISTA_P2),
        }
        | _detalhes_de(LISTA_P1)
    )
    with pytest.raises(ErroFonte, match="layout: contador sem linhas"):
        list(FonteSanepar().coletar(cliente))  # type: ignore[arg-type]


def test_coletar_lista_incompleta_falha() -> None:
    # Contador diz 57, mas a paginação acaba depois de 40 ids.
    cliente = _ClienteFalso(
        {
            _lista_url("Abertura", 0): LISTA_P1,
            _lista_url("Abertura", 1): LISTA_P2,
            _lista_url("Abertura", 2): LISTA_VAZIA,
        }
    )
    with pytest.raises(ErroFonte, match=r"layout: lista incompleta: 40 de 57"):
        list(FonteSanepar().coletar(cliente))  # type: ignore[arg-type]


def test_coletar_abertura_vazia_e_erro_de_layout() -> None:
    # Abertura nunca está vazia: vazia ali é sinal de site quebrado, não de lista sem editais.
    cliente = _ClienteFalso(
        {
            _lista_url("Abertura", 0): LISTA_VAZIA,
            _lista_url("Julgamento", 0): _com_total(_julgamento(LISTA_P1), 20),
        }
    )
    with pytest.raises(ErroFonte, match="layout: lista de Abertura vazia"):
        list(FonteSanepar().coletar(cliente))  # type: ignore[arg-type]


def test_coletar_abertura_com_contador_zero_e_erro_de_layout() -> None:
    cliente = _ClienteFalso({_lista_url("Abertura", 0): _com_total(_sem_linhas(LISTA_P1), 0)})
    with pytest.raises(ErroFonte, match="layout: lista de Abertura vazia"):
        list(FonteSanepar().coletar(cliente))  # type: ignore[arg-type]


def test_coletar_selo_diferente_da_situacao_pedida_e_erro_de_layout() -> None:
    # O site ignorou o filtro de situação: a lista de Julgamento veio com editais em Abertura.
    cliente = _ClienteFalso(
        {
            _lista_url("Abertura", 0): _com_total(LISTA_P1, 20),
            _lista_url("Abertura", 1): LISTA_VAZIA,
            _lista_url("Julgamento", 0): _com_total(LISTA_P1, 20),
        }
    )
    with pytest.raises(ErroFonte, match="layout: situação inesperada na lista"):
        list(FonteSanepar().coletar(cliente))  # type: ignore[arg-type]


def test_situacao_vem_da_lista_pedida_e_detalhe_divergente_so_loga(
    caplog: pytest.LogCaptureFixture,
) -> None:
    detalhe = DETALHE_345.replace(
        '<span class="sli-status-badge">', '<span class="sli-status-badge">Homologação ', 1
    )
    assert ler_detalhe(detalhe).situacao.startswith("Homologação")

    with caplog.at_level(logging.WARNING):
        por_id = {n["id_na_fonte"]: n for n in FonteSanepar().extrair(_paginas(detalhe))}

    assert por_id["OE-352/26"]["situacao_original"] == "Abertura"
    assert por_id["OE-352/26"]["situacao_normalizada"] == "aberta"
    divergentes = [r for r in caplog.records if r.getMessage() == "situacao_desconhecida"]
    assert [r.__dict__["id_na_fonte"] for r in divergentes] == ["OE-352/26"]


def test_coletar_detalhe_404_cai_para_a_linha_da_lista(caplog: pytest.LogCaptureFixture) -> None:
    detalhes = _detalhes_de(LISTA_P1)
    quebrado = f"{BASE}/licitacao/detalhe/352/26"
    detalhes[quebrado] = ErroFonte(f"HTTP 404 em {quebrado}", status=404)
    cliente = _ClienteFalso(
        {
            _lista_url("Abertura", 0): _com_total(LISTA_P1, 20),
            _lista_url("Abertura", 1): LISTA_VAZIA,
            _lista_url("Julgamento", 0): LISTA_VAZIA,
        }
        | detalhes
    )
    fonte = FonteSanepar()

    with caplog.at_level(logging.WARNING):
        paginas = list(fonte.coletar(cliente))  # type: ignore[arg-type]
    por_id = {n["id_na_fonte"]: n for n in fonte.extrair(paginas)}

    assert quebrado not in [p.url for p in paginas]
    assert len(por_id) == 20
    assert por_id["OE-352/26"]["anexos"] == []
    assert por_id["OE-352/26"]["objeto"].startswith("LOTE UNICO CONTRATACAO")  # da lista
    falhou = [r for r in caplog.records if r.getMessage() == "detalhe_falhou"]
    assert [(r.__dict__["url"], r.__dict__["status"]) for r in falhou] == [(quebrado, 404)]


@pytest.mark.parametrize("status", [403, 429, None])
def test_coletar_detalhe_bloqueado_para_o_retrato(status: int | None) -> None:
    detalhes = _detalhes_de(LISTA_P1)
    detalhes[f"{BASE}/licitacao/detalhe/352/26"] = ErroFonte("recusou", status=status)
    cliente = _ClienteFalso(
        {
            _lista_url("Abertura", 0): _com_total(LISTA_P1, 20),
            _lista_url("Abertura", 1): LISTA_VAZIA,
            _lista_url("Julgamento", 0): LISTA_VAZIA,
        }
        | detalhes
    )
    with pytest.raises(ErroFonte, match="recusou"):
        list(FonteSanepar().coletar(cliente))  # type: ignore[arg-type]


def test_coletar_muitos_detalhes_com_erro_para_o_retrato() -> None:
    # Mais de 20% dos detalhes falhando é o site fora do ar, não um edital quebrado.
    detalhes: dict[str, str | Exception] = {
        url: ErroFonte(f"HTTP 500 em {url}", status=500) for url in _detalhes_de(LISTA_P1)
    }
    cliente = _ClienteFalso(
        {
            _lista_url("Abertura", 0): _com_total(LISTA_P1, 20),
            _lista_url("Abertura", 1): LISTA_VAZIA,
            _lista_url("Julgamento", 0): LISTA_VAZIA,
        }
        | detalhes
    )
    with pytest.raises(ErroFonte, match="detalhes com erro"):
        list(FonteSanepar().coletar(cliente))  # type: ignore[arg-type]


def test_coletar_e_extrair_leem_cada_pagina_uma_vez(caplog: pytest.LogCaptureFixture) -> None:
    # Data de abertura sem hora loga `data_sem_hora`; com o parse único, loga uma vez só.
    lista = _com_total(LISTA_P1, 20).replace("18/12/2026 às 10h30", "18/12/2026", 1)
    cliente = _ClienteFalso(
        {
            _lista_url("Abertura", 0): lista,
            _lista_url("Abertura", 1): LISTA_VAZIA,
            _lista_url("Julgamento", 0): LISTA_VAZIA,
        }
        | _detalhes_de(LISTA_P1)
    )
    fonte = FonteSanepar()

    with caplog.at_level(logging.WARNING):
        fonte.extrair(list(fonte.coletar(cliente)))  # type: ignore[arg-type]

    sem_hora = [r for r in caplog.records if r.getMessage() == "data_sem_hora"]
    assert [r.__dict__["texto"] for r in sem_hora] == ["18/12/2026"]
