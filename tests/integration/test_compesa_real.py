import pytest

from radar.coleta import ClienteEducado, criar_http_educado
from radar.coleta.compesa import (
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
        c.get(URL_FORM)
        c.get(URL_OPENFORM)
        resposta = c.get(URL_GRADE)
    grade = ler_grade(decodificar(resposta.content, resposta.charset_encoding), "br")
    assert grade.inicio == 0
    assert len(grade.linhas) >= fonte.minimo_registros
    primeira = grade.linhas[0]
    assert primeira.chave and primeira.numero and primeira.objeto
    assert primeira.abertura is not None
    assert any(linha.custo is not None for linha in grade.linhas)
    assert not any("�" in linha.objeto for linha in grade.linhas)  # encoding certo
