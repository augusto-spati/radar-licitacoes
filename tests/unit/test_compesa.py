import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from radar.coleta import ErroFonte, PaginaColetada
from radar.coleta.compesa import (
    URL_FORM,
    URL_GRADE,
    URL_OPENFORM,
    URL_PAGINA,
    URL_PROXIMA,
    Chamada,
    FonteCompesa,
    decodificar,
    ler_grade,
    ler_literal,
    parse_data,
    parse_valor,
)
from radar.editais.registro import EditalNormalizado

DADOS = Path(__file__).resolve().parent.parent / "dados" / "fontes"

# A p1 foi salva em UTF-8 pela captura; a p2 veio crua do servidor, em windows-1252
# (o header diz ISO-8859-1, e o travessão do objeto é o byte 0x96).
P1_BYTES = (DADOS / "compesa-p1-reduzida.js.txt").read_bytes()
P2_BYTES = (DADOS / "compesa-p2-reduzida.js.txt").read_bytes()
P1 = P1_BYTES.decode("utf-8")
P2 = decodificar(P2_BYTES, "ISO-8859-1")
LATIN1 = "text/html; charset=ISO-8859-1"


# --- literal JS ------------------------------------------------------------------------------


def test_ler_literal_aspas_escapes_numeros_e_null() -> None:
    fonte = (
        "[{'a':'x\\x27y', b: \"q\\\"r\", 'n': -1.5, 'z': null, 'v': true, 'f': false},"
        " 'barra \\\\ \\/ \\- \\u00e9 \\n', 42, [], {}]"
    )
    valor, fim = ler_literal(fonte)
    assert valor == [
        {"a": "x'y", "b": 'q"r', "n": -1.5, "z": None, "v": True, "f": False},
        "barra \\ / - é \n",
        42,
        [],
        {},
    ]
    assert fim == len(fonte)


def test_ler_literal_aspas_simples_escapada() -> None:
    assert ler_literal(r"'d\'Arc'")[0] == "d'Arc"


def test_ler_literal_chamada_vira_chamada_e_para_no_fim_do_valor() -> None:
    fonte = "[d.c_1.gridButton('cod', 7, null, {enabled: true}), new X(1)];resto = 1 == 1;"
    valor, fim = ler_literal(fonte)
    assert valor == [
        Chamada("d.c_1.gridButton", ["cod", 7, None, {"enabled": True}]),
        Chamada("new X", [1]),
    ]
    assert fonte[fim:] == ";resto = 1 == 1;"


@pytest.mark.parametrize("fonte", ["['sem fim", "[1, 2", "{a 1}", "[@]"])
def test_ler_literal_quebrado_e_erro_de_layout(fonte: str) -> None:
    with pytest.raises(ErroFonte, match="layout"):
        ler_literal(fonte)


# --- datas, valores, encoding ----------------------------------------------------------------


def test_parse_data_por_estilo() -> None:
    assert parse_data("05/11/2026", "13:00", "br") == datetime(2026, 11, 5, 13, 0)
    assert parse_data("05/11/2026", "13:00", "us") == datetime(2026, 5, 11, 13, 0)
    assert parse_data("04/09/2026 14:00:00", "", "us") == datetime(2026, 4, 9, 14, 0)
    assert parse_data("24/11/2026", "", "br") == datetime(2026, 11, 24)
    assert parse_data("", "09:00", "br") is None


def test_parse_data_impossivel_no_estilo_tenta_o_outro_e_loga(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        assert parse_data("03/13/2026", "09:00", "br") == datetime(2026, 3, 13, 9, 0)
    assert "data_formato_inconsistente" in caplog.messages


def test_parse_valor_por_estilo() -> None:
    assert parse_valor("2856395,49", "br") == Decimal("2856395.49")
    assert parse_valor("1.234.567,8", "br") == Decimal("1234567.8")
    assert parse_valor("223078.49", "us") == Decimal("223078.49")
    assert parse_valor("1,234.5", "us") == Decimal("1234.5")
    assert parse_valor("997200", "br") == Decimal("997200")
    assert parse_valor("", "br") is None
    assert parse_valor("abc", "br") is None


def test_decodificar_conforme_o_header() -> None:
    assert decodificar("ÇÃO – ok".encode("cp1252"), "ISO-8859-1") == "ÇÃO – ok"
    assert decodificar("ÇÃO".encode(), "utf-8") == "ÇÃO"
    assert decodificar("ÇÃO".encode(), None) == "ÇÃO"  # sem header: UTF-8 primeiro
    assert decodificar("ÇÃO".encode("cp1252"), None) == "ÇÃO"  # e depois windows-1252


# --- grade -----------------------------------------------------------------------------------


def test_grade_p1() -> None:
    grade = ler_grade(P1, "br")
    assert (grade.inicio, grade.fim, grade.tem_proxima) == (0, 100, True)
    assert len(grade.linhas) == 13
    primeira = grade.linhas[0]
    assert primeira.chave == "2026LC213CEL-1"
    assert primeira.numero == "LC 213/2026 CEL-1"
    assert primeira.objeto == "REGISTRO DE PREÇOS PARA AQUISIÇÃO DE VÁLVULAS DIVERSAS"
    assert primeira.abertura == datetime(2026, 11, 24, 13, 0)
    assert primeira.custo == Decimal("2856395.49")
    assert primeira.situacao == "Abertura"
    assert primeira.municipio is None  # vazio em todas as linhas das amostras
    assert primeira.campos["Órgão Solicitante"] == "GAL"
    assert primeira.campos["Número do Processo"] == "4791/2026"


def test_grade_p1_dia_ate_12_sai_dd_mm() -> None:
    por_chave = {linha.chave: linha for linha in ler_grade(P1, "br").linhas}
    assert por_chave["2026LC210CSL"].abertura == datetime(2026, 11, 12, 9, 0)  # 12/11


def test_grade_p2_data_mm_dd_sai_certa() -> None:
    """Review Focus 3: na p2 a data vem MM/dd; com dia <= 12 os dois formatos divergem."""
    grade = ler_grade(P2, "us")
    assert (grade.inicio, grade.fim, grade.tem_proxima) == (100, 200, True)
    por_chave = {linha.chave: linha for linha in grade.linhas}
    assert por_chave["2026LC067CPL"].abertura == datetime(2026, 4, 9, 14, 0)  # não 4/set
    assert por_chave["2026LC013CPL"].abertura == datetime(2026, 4, 9, 14, 0)  # "… 14:00:00"
    assert por_chave["2026LC034CSL"].abertura == datetime(2026, 3, 12, 9, 0)  # não 3/dez
    assert por_chave["2026LC119CPL"].abertura == datetime(2026, 6, 30, 14, 0)
    assert por_chave["2026LC119CPL"].custo == Decimal("223078.49")


def test_grade_acentos_nas_duas_paginas() -> None:
    p2 = {linha.chave: linha for linha in ler_grade(P2, "us").linhas}
    assert p2["2026LC119CPL"].objeto == (
        "IMPLANTAÇÃO DO SISTEMA DE ABASTECIMENTO CAJUEIRO 2 EM VITÓRIA DE SANTO ANTÃO"
    )
    assert p2["2026LC056CEL-2"].objeto.startswith(
        "CONTRATAÇÃO DE ENTIDADE SEM FINS LUCRATIVOS – ESFL"
    )
    assert ler_grade(P1, "br").linhas[2].situacao == "Habilitação"


def test_grade_custo_vazio_e_municipio_preenchido() -> None:
    # Nenhuma linha real tem custo vazio ou município: a variação é feita aqui, à vista.
    texto = P1.replace("\\x272856395,49\\x27", "\\x27\\x27", 1).replace(
        "\\x27\\x27); d.c_1926738125.field", "\\x27RECIFE\\x27); d.c_1926738125.field", 1
    )
    primeira = ler_grade(texto, "br").linhas[0]
    assert primeira.custo is None
    assert primeira.municipio == "RECIFE"


def test_grade_sem_data_e_erro_de_layout() -> None:
    with pytest.raises(ErroFonte, match="layout"):
        ler_grade("<html><title>Erro</title></html>", "br")


def test_grade_linha_sem_lc_chave_e_erro_de_layout() -> None:
    with pytest.raises(ErroFonte, match="layout: linha sem lc_chave"):
        ler_grade(P1.replace("field = \\x27lc_chave\\x27", "field = \\x27outro\\x27", 1), "br")


# --- extrair ---------------------------------------------------------------------------------


def _paginas() -> list[PaginaColetada]:
    return [PaginaColetada(URL_GRADE, "js", P1), PaginaColetada(URL_PAGINA, "js", P2)]


def test_extrair_normaliza_e_deduplica() -> None:
    normalizados = FonteCompesa().extrair(_paginas())
    # 13 + 11 linhas; a p1 tem uma linha repetida (o portal duplica linhas idênticas).
    assert len(normalizados) == 23
    por_id = {n["id_na_fonte"]: n for n in normalizados}
    assert len(por_id) == 23
    for n in normalizados:
        EditalNormalizado.model_validate(n)  # o formato comum aceita todos

    primeiro = por_id["2026LC213CEL-1"]
    assert primeiro["fonte"] == "compesa"
    assert primeiro["objeto"] == "REGISTRO DE PREÇOS PARA AQUISIÇÃO DE VÁLVULAS DIVERSAS"
    assert primeiro["orgao_nome"] == "COMPESA"
    assert primeiro["orgao_cnpj"] is None  # não vem nos dados
    assert primeiro["uf"] == "PE"
    assert primeiro["municipio"] is None
    assert primeiro["modalidade_nome"] == "Licitação"
    assert primeiro["situacao_original"] == "Abertura"
    assert primeiro["situacao_normalizada"] == "aberta"
    assert primeiro["valor_estimado"] == Decimal("2856395.49")
    assert primeiro["data_publicacao"] is None
    assert primeiro["data_abertura_proposta"] == datetime(2026, 11, 24, 13, 0)
    assert primeiro["data_encerramento_proposta"] == datetime(2026, 11, 24, 13, 0)
    assert primeiro["link_origem"] is None
    assert primeiro["anexos"] == []
    assert primeiro["bruto"]["numero"] == "LC 213/2026 CEL-1"
    assert primeiro["bruto"]["estilo"] == "br"
    assert primeiro["bruto"]["campos"]["Custo Estimado"] == "2856395,49"

    # a data da p2 segue o estilo da página de onde veio
    assert por_id["2026LC067CPL"]["data_abertura_proposta"] == datetime(2026, 4, 9, 14, 0)
    assert por_id["2026ECR001CEL-1"]["modalidade_nome"] == "Credenciamento"
    assert por_id["2026MI001CEL-2"]["modalidade_nome"] == "Compesa (MI)"  # não confirmado


@pytest.mark.parametrize(
    ("situacao", "esperada"),
    [
        ("Abertura", "aberta"),
        ("Habilitação", "em_julgamento"),
        ("Homologação", "encerrada"),
        ("Contrato", "encerrada"),
        ("Fracassada", "encerrada"),
        ("Deserta", "encerrada"),
        ("Revogada", "encerrada"),
        ("Anulada", "encerrada"),
        ("Suspensa", "outra"),
    ],
)
def test_mapa_de_situacoes(situacao: str, esperada: str) -> None:
    texto = P1.replace("'field817232':'Abertura',row:0}", f"'field817232':'{situacao}',row:0}}")
    normalizado = FonteCompesa().extrair([PaginaColetada(URL_GRADE, "js", texto)])[0]
    assert normalizado["situacao_original"] == situacao
    assert normalizado["situacao_normalizada"] == esperada


def test_situacoes_das_amostras_e_desconhecida_loga(caplog: pytest.LogCaptureFixture) -> None:
    # Nenhuma situação das amostras fica fora do mapa: a desconhecida é posta aqui, à vista.
    p1 = P1.replace("'field817232':'Abertura',row:0}", "'field817232':'Em Análise',row:0}")
    with caplog.at_level(logging.WARNING):
        normalizados = FonteCompesa().extrair(
            [PaginaColetada(URL_GRADE, "js", p1), PaginaColetada(URL_PAGINA, "js", P2)]
        )
    vistas = {(n["situacao_original"], n["situacao_normalizada"]) for n in normalizados}
    assert vistas == {
        ("Abertura", "aberta"),
        ("Habilitação", "em_julgamento"),
        ("Contrato", "encerrada"),
        ("Fracassada", "encerrada"),
        ("Deserta", "encerrada"),
        ("Homologação", "encerrada"),
        ("Revogada", "encerrada"),
        ("Suspensa", "outra"),
        ("Anulada", "encerrada"),  # só na p2
        ("Em Análise", "outra"),
    }
    desconhecidas = [r for r in caplog.records if r.message == "situacao_desconhecida"]
    assert [r.__dict__["situacao"] for r in desconhecidas] == ["Em Análise"]


def test_modalidade_desconhecida_loga_uma_vez_por_codigo(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Duas linhas com o código inventado "XYZ" (o MI de verdade também é desconhecido).
    p1 = P1.replace("'field817074':'LC 213\\/2026 CEL\\-1'", "'field817074':'XYZ 213\\/2026'")
    p1 = p1.replace("'field817074':'LC 217\\/2026 CSL'", "'field817074':'XYZ 217\\/2026'")
    with caplog.at_level(logging.WARNING):
        normalizados = FonteCompesa().extrair([PaginaColetada(URL_GRADE, "js", p1)])
    por_id = {n["id_na_fonte"]: n for n in normalizados}
    assert por_id["2026LC213CEL-1"]["modalidade_nome"] == "Compesa (XYZ)"
    assert por_id["2026LC217CSL"]["modalidade_nome"] == "Compesa (XYZ)"
    assert por_id["2026LC216CEL-2"]["modalidade_nome"] == "Licitação"  # conhecida
    codigos = [
        r.__dict__["codigo"] for r in caplog.records if r.message == "modalidade_desconhecida"
    ]
    assert sorted(codigos) == ["MI", "XYZ"]


def test_muitas_datas_impossiveis_no_estilo_da_pagina_e_erro() -> None:
    # A p2 (MM/dd) lida como se fosse dd/MM: 6 das 11 datas ficam impossíveis (dia > 12 no mês).
    with pytest.raises(ErroFonte, match="layout: estilo de data inconsistente"):
        ler_grade(P2, "br")


def test_uma_data_impossivel_abaixo_do_limite_so_loga(caplog: pytest.LogCaptureFixture) -> None:
    # 1 de 13 datas trocadas (< 20%): a linha é lida no outro estilo, com log.
    p1 = P1.replace("'field817072':'24\\/11\\/2026'", "'field817072':'11\\/24\\/2026'", 1)
    assert p1 != P1
    with caplog.at_level(logging.WARNING):
        grade = ler_grade(p1, "br")
    assert grade.linhas[0].abertura == datetime(2026, 11, 24, 13, 0)
    assert "data_formato_inconsistente" in caplog.messages


def test_minimo_nome_e_marcador() -> None:
    fonte = FonteCompesa()
    assert fonte.nome == "compesa"
    assert fonte.minimo_registros == 50
    assert fonte.marcador_bloqueio == "<title>Request Rejected</title>"


# --- coletar ---------------------------------------------------------------------------------


class _ClienteFalso:
    """Só tem `get`: se o coletor tentasse outro verbo, o teste quebraria."""

    def __init__(self, roteiro: dict[str, list[httpx.Response]]) -> None:
        self._roteiro = {url: list(respostas) for url, respostas in roteiro.items()}
        self.urls: list[str] = []

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        assert params is None
        self.urls.append(url)
        return self._roteiro[url].pop(0)


def _js(texto: str, charset: str = "UTF-8") -> httpx.Response:
    return httpx.Response(
        200,
        content=texto.encode(charset),
        headers={"content-type": f"text/html; charset={charset}"},
    )


def _p2_bruta() -> httpx.Response:
    return httpx.Response(200, content=P2_BYTES, headers={"content-type": LATIN1})


def _sem_proxima(texto: str) -> str:
    return texto.replace(
        "enableNavigation(true, true, true, true)", "enableNavigation(true, true, false, false)"
    )


def _deslocada(texto: str, inicio: int, fim: int) -> str:
    return texto.replace("setGridPageIni(100)", f"setGridPageIni({inicio})").replace(
        "setGridPageEnd(200)", f"setGridPageEnd({fim})"
    )


def _roteiro(*paginas: httpx.Response) -> dict[str, list[httpx.Response]]:
    seguintes = list(paginas[1:])
    return {
        URL_FORM: [httpx.Response(200, text="<html>shell</html>")],
        URL_OPENFORM: [httpx.Response(200, text="<html>form</html>")],
        URL_GRADE: [paginas[0]],
        URL_PROXIMA: [httpx.Response(200, text="") for _ in seguintes],
        URL_PAGINA: seguintes,
    }


def test_coletar_sequencia_de_urls_ate_acabar_a_navegacao() -> None:
    cliente = _ClienteFalso(
        _roteiro(
            _js(P1),
            httpx.Response(
                200, content=_sem_proxima(P2).encode("cp1252"), headers={"content-type": LATIN1}
            ),
        )
    )

    paginas = list(FonteCompesa().coletar(cliente))  # type: ignore[arg-type]

    assert cliente.urls == [URL_FORM, URL_OPENFORM, URL_GRADE, URL_PROXIMA, URL_PAGINA]
    assert [p.url for p in paginas] == [URL_GRADE, URL_PAGINA]
    assert all(p.formato == "js" for p in paginas)
    assert "LUCRATIVOS – ESFL" in paginas[1].texto  # decodificada pelo header
    assert "VITÓRIA DE SANTO ANTÃO" in paginas[1].texto


def test_coletar_para_quando_a_pagina_nao_traz_chave_nova() -> None:
    repetida = httpx.Response(
        200, content=_deslocada(P2, 200, 300).encode("cp1252"), headers={"content-type": LATIN1}
    )
    cliente = _ClienteFalso(_roteiro(_js(P1), _p2_bruta(), repetida))

    paginas = list(FonteCompesa().coletar(cliente))  # type: ignore[arg-type]

    assert cliente.urls == [
        URL_FORM,
        URL_OPENFORM,
        URL_GRADE,
        URL_PROXIMA,
        URL_PAGINA,
        URL_PROXIMA,
        URL_PAGINA,
    ]
    assert [p.url for p in paginas] == [URL_GRADE, URL_PAGINA]  # a repetida é descartada


def test_coletar_pagina_fora_de_ordem_falha_em_vez_de_truncar() -> None:
    # Sessão perdida: o refreshpage volta ao começo da lista (setGridPageIni(0)).
    cliente = _ClienteFalso(_roteiro(_js(P1), _js(_deslocada(P2, 0, 100), "cp1252")))
    with pytest.raises(ErroFonte, match=r"layout: página fora de ordem: início 0, esperado 100"):
        list(FonteCompesa().coletar(cliente))  # type: ignore[arg-type]


def test_coletar_resposta_sem_data_falha() -> None:
    cliente = _ClienteFalso(_roteiro(_js("<html>erro</html>")))
    with pytest.raises(ErroFonte, match="layout"):
        list(FonteCompesa().coletar(cliente))  # type: ignore[arg-type]


def test_coletar_trava_de_paginas(monkeypatch: pytest.MonkeyPatch) -> None:
    import radar.coleta.compesa as compesa

    monkeypatch.setattr(compesa, "MAX_PAGINAS", 2)
    cliente = _ClienteFalso(_roteiro(_js(P1), _p2_bruta()))
    with pytest.raises(ErroFonte, match="lista sem fim: mais de 2 páginas"):
        list(FonteCompesa().coletar(cliente))  # type: ignore[arg-type]
