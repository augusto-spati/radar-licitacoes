from datetime import date
from typing import Any

from radar.coleta import ClientePNCP
from radar.coleta.pncp import Pagina

Chave = tuple[str, date, date, int, int]


def pagina(numero: int, total: int, n: int = 2, total_registros: int | None = None) -> Pagina:
    registros = [{"numeroControlePNCP": f"{numero}-{i}"} for i in range(n)]
    bruto: dict[str, Any] = {"data": registros, "totalPaginas": total, "numeroPagina": numero}
    if total_registros is not None:
        bruto["totalRegistros"] = total_registros
    return Pagina(registros=registros, total_paginas=total, numero_pagina=numero, bruto=bruto)


VAZIA = Pagina(registros=[], total_paginas=0, numero_pagina=1, bruto={})


class ClienteFalso:
    """Responde por roteiro; um item Exception é levantado, qualquer ausência é erro do teste."""

    def __init__(self, roteiro: dict[Chave, Pagina | Exception]) -> None:
        self.roteiro = roteiro
        self.chamadas: list[Chave] = []

    def buscar(
        self, endpoint: str, data_ini: date, data_fim: date, modalidade: int, pagina: int
    ) -> Pagina:
        chave = (endpoint, data_ini, data_fim, modalidade, pagina)
        self.chamadas.append(chave)
        resposta = self.roteiro[chave]
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


def como_cliente(falso: ClienteFalso) -> ClientePNCP:
    return falso  # type: ignore[return-value]
