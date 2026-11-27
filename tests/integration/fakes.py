from collections.abc import Callable
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
    """Responde por roteiro; um item BaseException é levantado; ausência é erro do teste."""

    def __init__(
        self,
        roteiro: dict[Chave, Pagina | BaseException],
        ao_buscar: Callable[[Chave], None] | None = None,
    ) -> None:
        self.roteiro = roteiro
        self.chamadas: list[Chave] = []
        self.ao_buscar = ao_buscar  # roda antes da resposta (relógio falso, intruso no banco)

    def buscar(
        self, endpoint: str, data_ini: date, data_fim: date, modalidade: int, pagina: int
    ) -> Pagina:
        chave = (endpoint, data_ini, data_fim, modalidade, pagina)
        self.chamadas.append(chave)
        if self.ao_buscar is not None:
            self.ao_buscar(chave)
        resposta = self.roteiro[chave]
        if isinstance(resposta, BaseException):
            raise resposta
        return resposta


def como_cliente(falso: ClienteFalso) -> ClientePNCP:
    return falso  # type: ignore[return-value]
