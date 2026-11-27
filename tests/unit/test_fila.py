"""Corpo da mensagem e FilaSQS (com cliente falso)."""

from typing import Any

import pytest

from radar.comum.fila import (
    FilaSQS,
    Mensagem,
    ResultadoEnvio,
    de_corpo,
    para_corpo,
    regiao_da_url,
)


def test_ida_e_volta() -> None:
    m = Mensagem(outbox_id=5, tipo="pagina_coletada", carga={"pagina_id": 7})

    assert de_corpo(para_corpo(m)) == m


def test_corpo_invalido_levanta_value_error() -> None:
    with pytest.raises(ValueError):
        de_corpo("x")
    with pytest.raises(ValueError):
        de_corpo('{"tipo": "outro", "carga": {}, "outbox_id": 1}')


class ClienteFalso:
    def __init__(self, recusar: set[str] | None = None) -> None:
        self.recusar = recusar or set()
        self.lotes: list[list[dict[str, str]]] = []
        self.movimentos: list[dict[str, str]] = []

    def send_message_batch(self, QueueUrl: str, Entries: list[dict[str, str]]) -> dict[str, Any]:
        self.lotes.append(Entries)
        return {
            "Successful": [{"Id": e["Id"]} for e in Entries if e["Id"] not in self.recusar],
            "Failed": [
                {"Id": e["Id"], "Code": "InternalError", "Message": "falhou", "SenderFault": False}
                for e in Entries
                if e["Id"] in self.recusar
            ],
        }

    def start_message_move_task(self, **kwargs: str) -> dict[str, str]:
        self.movimentos.append(kwargs)
        return {"TaskHandle": "tarefa-1"}


def _mensagens(n: int) -> list[Mensagem]:
    return [Mensagem(i, "pagina_coletada", {"pagina_id": i}) for i in range(1, n + 1)]


def test_recusada_vai_para_recusadas() -> None:
    cliente = ClienteFalso(recusar={"2"})
    fila = FilaSQS("https://sqs/fila", cliente=cliente)

    resultado = fila.enviar_lote(_mensagens(3))

    assert resultado == ResultadoEnvio(aceitas={1, 3}, recusadas={2: "InternalError: falhou"})
    assert [e["Id"] for e in cliente.lotes[0]] == ["1", "2", "3"]


def test_lote_tem_no_maximo_10_entradas() -> None:
    cliente = ClienteFalso()
    fila = FilaSQS("https://sqs/fila", cliente=cliente)

    resultado = fila.enviar_lote(_mensagens(25))

    assert all(len(lote) <= 10 for lote in cliente.lotes)
    assert resultado.aceitas == set(range(1, 26))


def test_mover_falhas_devolve_handle() -> None:
    cliente = ClienteFalso()
    fila = FilaSQS("https://sqs/fila", cliente=cliente)

    assert fila.mover_falhas("arn:origem", "arn:destino") == "tarefa-1"
    assert cliente.movimentos == [{"SourceArn": "arn:origem", "DestinationArn": "arn:destino"}]


def test_regiao_da_url() -> None:
    assert regiao_da_url("https://sqs.sa-east-1.amazonaws.com/123456789012/fila") == "sa-east-1"
    assert regiao_da_url("https://sqs.us-east-1.amazonaws.com/1/f.fifo") == "us-east-1"
    with pytest.raises(ValueError):
        regiao_da_url("http://localhost:9324/queue/fila")
