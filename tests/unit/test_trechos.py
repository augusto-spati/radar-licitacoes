from radar.analise.trechos import Trecho, dividir


def test_dividir_sobrepoe_uma_pagina() -> None:
    paginas = [str(n) * 25_000 for n in range(1, 6)]
    trechos = dividir(paginas)
    assert [(t.pagina_inicial, t.pagina_final) for t in trechos] == [
        (1, 2),
        (2, 3),
        (3, 4),
        (4, 5),
    ]
    for trecho in trechos:
        assert trecho.texto.startswith(f"[[pagina {trecho.pagina_inicial}]]\n")
        assert f"[[pagina {trecho.pagina_final}]]\n" in trecho.texto


def test_paginas_pequenas_num_trecho_so() -> None:
    esperado = Trecho(
        texto="[[pagina 1]]\na\n[[pagina 2]]\nb\n[[pagina 3]]\nc",
        pagina_inicial=1,
        pagina_final=3,
    )
    assert dividir(["a", "b", "c"]) == [esperado]


def test_dividir_pagina_gigante() -> None:
    trechos = dividir(["x" * 130_000])
    marcador = len("[[pagina 1]]\n")
    assert len(trechos) == 3
    assert all(len(t.texto) <= 60_000 + marcador for t in trechos)
    assert all(t.texto.startswith("[[pagina 1]]\n") for t in trechos)
    assert sum(t.texto.count("x") for t in trechos) == 130_000


def test_dividir_vazio() -> None:
    assert dividir([]) == []
