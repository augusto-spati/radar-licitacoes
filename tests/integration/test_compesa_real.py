import pytest

from radar.coleta import ClienteEducado, criar_http_educado
from radar.coleta.compesa import (
    IDIOMA,
    URL_FORM,
    URL_GRADE,
    URL_OPENFORM,
    FonteCompesa,
    decodificar,
    ler_grade,
)


@pytest.mark.rede
def test_compesa_real_so_a_pagina_1() -> None:
    """Só a página 1 (form.jsp → openform.do → gridEdit.do), 3 s entre requisições, sem banco."""
    fonte = FonteCompesa()
    with ClienteEducado(criar_http_educado(), marcador_bloqueio=fonte.marcador_bloqueio) as c:
        c.get(URL_FORM, headers=IDIOMA)
        c.get(URL_OPENFORM, headers=IDIOMA)
        resposta = c.get(URL_GRADE, headers=IDIOMA)
    # ler_grade falha se a página não vier em dd/MM (trava de 20% de datas impossíveis)
    grade = ler_grade(decodificar(resposta.content, resposta.charset_encoding))
    assert grade.inicio == 0
    assert len(grade.linhas) >= fonte.minimo_registros
    primeira = grade.linhas[0]
    assert primeira.chave and primeira.numero and primeira.objeto
    assert primeira.abertura is not None
    custos = [linha.campos.get("Custo Estimado", "") for linha in grade.linhas]
    assert any("," in custo for custo in custos)  # vírgula decimal: veio em pt-BR
    assert not any("." in custo for custo in custos)
    assert not any("\ufffd" in linha.objeto for linha in grade.linhas)  # encoding certo
