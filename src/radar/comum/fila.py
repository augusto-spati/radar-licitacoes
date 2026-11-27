"""Fila de mensagens: formato do corpo, interface e implementação SQS."""

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol, cast, get_args

from radar.comum.outbox import TipoMensagem

TAMANHO_MAXIMO_LOTE = 10


@dataclass(frozen=True)
class Mensagem:
    outbox_id: int
    tipo: TipoMensagem
    carga: dict[str, int]


@dataclass
class ResultadoEnvio:
    aceitas: set[int]
    recusadas: dict[int, str]


def para_corpo(m: Mensagem) -> str:
    return json.dumps({"tipo": m.tipo, "carga": m.carga, "outbox_id": m.outbox_id})


def de_corpo(corpo: str) -> Mensagem:
    try:
        dados = json.loads(corpo)
        tipo = dados["tipo"]
        carga = dados["carga"]
        outbox_id = dados["outbox_id"]
    except (ValueError, KeyError, TypeError) as erro:
        raise ValueError(f"corpo de mensagem inválido: {corpo!r}") from erro
    if tipo not in get_args(TipoMensagem):
        raise ValueError(f"tipo de mensagem desconhecido: {tipo!r}")
    if not isinstance(carga, dict) or not isinstance(outbox_id, int):
        raise ValueError(f"corpo de mensagem inválido: {corpo!r}")
    return Mensagem(outbox_id=outbox_id, tipo=tipo, carga=carga)


def regiao_da_url(url: str) -> str:
    """Região da fila, tirada de `https://sqs.<regiao>.amazonaws.com/...`."""
    achado = re.match(r"https://sqs\.([a-z0-9-]+)\.amazonaws\.com/", url)
    if achado is None:
        raise ValueError(f"URL de fila SQS sem região reconhecível: {url!r}")
    return achado.group(1)


class Fila(Protocol):
    def enviar_lote(self, mensagens: list[Mensagem]) -> ResultadoEnvio: ...


class FilaSQS:
    def __init__(self, url: str, cliente: Any | None = None) -> None:
        if cliente is None:
            import boto3  # type: ignore[import-untyped]

            cliente = boto3.client("sqs", region_name=regiao_da_url(url))
        self._url = url
        self._cliente: Any = cliente

    def enviar_lote(self, mensagens: list[Mensagem]) -> ResultadoEnvio:
        resultado = ResultadoEnvio(aceitas=set(), recusadas={})
        for inicio in range(0, len(mensagens), TAMANHO_MAXIMO_LOTE):
            lote = mensagens[inicio : inicio + TAMANHO_MAXIMO_LOTE]
            entradas = [{"Id": str(m.outbox_id), "MessageBody": para_corpo(m)} for m in lote]
            resposta = self._cliente.send_message_batch(QueueUrl=self._url, Entries=entradas)
            resultado.aceitas.update(int(e["Id"]) for e in resposta.get("Successful", []))
            for falha in resposta.get("Failed", []):
                resultado.recusadas[int(falha["Id"])] = f"{falha['Code']}: {falha['Message']}"
        return resultado

    def mover_falhas(self, origem_arn: str, destino_arn: str) -> str:
        resposta = self._cliente.start_message_move_task(
            SourceArn=origem_arn, DestinationArn=destino_arn
        )
        return cast(str, resposta["TaskHandle"])
