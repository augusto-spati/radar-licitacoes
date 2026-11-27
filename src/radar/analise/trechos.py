"""Divisão do texto em trechos com marcadores de página e 1 página de sobreposição."""

from dataclasses import dataclass

CARACTERES_POR_TRECHO = 60_000


@dataclass(frozen=True)
class Trecho:
    texto: str
    pagina_inicial: int
    pagina_final: int


def _marcador(pagina: int) -> str:
    return f"[[pagina {pagina}]]\n"


def _unidades(paginas: list[str], caracteres: int) -> list[tuple[int, str]]:
    """Cada página com seu marcador; página maior que o limite vira pedaços, cada um marcado."""
    unidades: list[tuple[int, str]] = []
    for numero, texto in enumerate(paginas, start=1):
        marcador = _marcador(numero)
        if len(marcador) + len(texto) <= caracteres:
            unidades.append((numero, marcador + texto))
            continue
        tamanho = max(1, caracteres - len(marcador))
        for inicio in range(0, len(texto), tamanho):
            unidades.append((numero, marcador + texto[inicio : inicio + tamanho]))
    return unidades


def dividir(paginas: list[str], caracteres: int = CARACTERES_POR_TRECHO) -> list[Trecho]:
    """Trechos de até `caracteres`, cortados em fim de página, com 1 página de sobreposição.

    O trecho seguinte começa na última página do anterior (a junção absorve o achado repetido).
    """
    unidades = _unidades(paginas, caracteres)
    trechos: list[Trecho] = []
    inicio = 0
    sobreposto = False  # `inicio` é a última unidade do trecho anterior
    while inicio < len(unidades):
        fim = inicio
        tamanho = len(unidades[inicio][1])
        while fim + 1 < len(unidades) and tamanho + 1 + len(unidades[fim + 1][1]) <= caracteres:
            fim += 1
            tamanho += 1 + len(unidades[fim][1])
        if sobreposto and fim == inicio:
            # Só caberia a unidade já enviada inteira no trecho anterior: não repete.
            inicio, sobreposto = inicio + 1, False
            continue
        janela = unidades[inicio : fim + 1]
        trechos.append(
            Trecho(
                texto="\n".join(texto for _, texto in janela),
                pagina_inicial=janela[0][0],
                pagina_final=janela[-1][0],
            )
        )
        if fim == len(unidades) - 1:
            break
        inicio, sobreposto = (fim, True) if fim > inicio else (fim + 1, False)
    return trechos
