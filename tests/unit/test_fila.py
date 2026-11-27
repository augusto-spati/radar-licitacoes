"""Corpo da mensagem e FilaSQS (com cliente falso)."""

from typing import Any

import pytest

from radar.comum.fila import (
    FilaRoteada,
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


def test_coletar_janela_ida_e_volta() -> None:
    m = Mensagem(outbox_id=9, tipo="coletar_janela", carga={"janela_id": 3, "lote": 2})

    assert de_corpo(para_corpo(m)) == m


@pytest.mark.parametrize(
    "carga",
    [{"janela_id": 3}, {"lote": 0}, {"janela_id": "3", "lote": 0}, {"janela_id": 3, "lote": True}],
)
def test_de_corpo_coletar_janela_sem_lote_e_invalido(carga: dict[str, object]) -> None:
    import json

    corpo = json.dumps({"tipo": "coletar_janela", "carga": carga, "outbox_id": 1})

    with pytest.raises(ValueError):
        de_corpo(corpo)


# --- fila roteada por tipo ----------------------------------------------------------------


class FilaFalsa:
    def __init__(self, recusar: set[int] | None = None) -> None:
        self.recusar = recusar or set()
        self.recebidas: list[Mensagem] = []

    def enviar_lote(self, mensagens: list[Mensagem]) -> ResultadoEnvio:
        self.recebidas.extend(mensagens)
        return ResultadoEnvio(
            aceitas={m.outbox_id for m in mensagens if m.outbox_id not in self.recusar},
            recusadas={m.outbox_id: "não" for m in mensagens if m.outbox_id in self.recusar},
        )


def _misturadas() -> list[Mensagem]:
    return [
        Mensagem(1, "pagina_coletada", {"pagina_id": 1}),
        Mensagem(2, "coletar_janela", {"janela_id": 5, "lote": 0}),
        Mensagem(3, "retrato_coletado", {"retrato_id": 9}),
        Mensagem(4, "coletar_janela", {"janela_id": 6, "lote": 1}),
    ]


def test_fila_roteada_separa_por_tipo() -> None:
    janelas, editais = FilaFalsa(), FilaFalsa()
    fila = FilaRoteada({"coletar_janela": janelas}, editais)

    resultado = fila.enviar_lote(_misturadas())

    assert [m.outbox_id for m in janelas.recebidas] == [2, 4]
    assert [m.outbox_id for m in editais.recebidas] == [1, 3]
    assert resultado == ResultadoEnvio(aceitas={1, 2, 3, 4}, recusadas={})


def test_fila_roteada_recusa_de_um_destino_nao_afeta_o_outro() -> None:
    janelas, editais = FilaFalsa(recusar={2, 4}), FilaFalsa()
    fila = FilaRoteada({"coletar_janela": janelas}, editais)

    resultado = fila.enviar_lote(_misturadas())

    assert resultado == ResultadoEnvio(aceitas={1, 3}, recusadas={2: "não", 4: "não"})


def test_fila_roteada_nao_chama_destino_sem_mensagens() -> None:
    janelas, editais = FilaFalsa(), FilaFalsa()
    fila = FilaRoteada({"coletar_janela": janelas}, editais)

    fila.enviar_lote([Mensagem(1, "pagina_coletada", {"pagina_id": 1})])

    assert janelas.recebidas == []


TP = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def test_fila_sqs_manda_atributo_traceparent() -> None:
    cliente = ClienteFalso()
    fila = FilaSQS("https://sqs.us-east-2.amazonaws.com/1/radar-editais", cliente)
    fila.enviar_lote(
        [
            Mensagem(1, "pagina_coletada", {"pagina_id": 1}, TP),
            Mensagem(2, "pagina_coletada", {"pagina_id": 2}),
        ]
    )
    com, sem = cliente.lotes[0]
    assert com["MessageAttributes"] == {  # type: ignore[comparison-overlap]
        "traceparent": {"DataType": "String", "StringValue": TP}
    }
    assert "MessageAttributes" not in sem


def test_corpo_nao_leva_traceparent() -> None:
    com = Mensagem(1, "pagina_coletada", {"pagina_id": 7}, TP)
    sem = Mensagem(1, "pagina_coletada", {"pagina_id": 7})
    assert para_corpo(com) == para_corpo(sem)
    assert de_corpo(para_corpo(com)) == sem
