"""FilaLocal e criar_fila: despacho de coletar_janela e exigência das duas URLs no SQS."""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from radar.comum import Config, FilaRoteada, FilaSQS, Mensagem
from radar.processador import FilaLocal, criar_fila

_URL = "https://sqs.us-east-2.amazonaws.com/123456789012/radar-editais"
_URL_JANELAS = "https://sqs.us-east-2.amazonaws.com/123456789012/radar-janelas"


class SessaoFalsa:
    def __enter__(self) -> "SessaoFalsa":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _fabrica() -> Session:
    return SessaoFalsa()  # type: ignore[return-value]


def _agora() -> datetime:
    return datetime(2026, 11, 27, tzinfo=UTC)


_JANELA = Mensagem(7, "coletar_janela", {"janela_id": 3, "lote": 1})


def test_fila_local_sem_coletora_recusa_coletar_janela() -> None:
    fila = FilaLocal(_fabrica, {}, _agora)

    resultado = fila.enviar_lote([_JANELA])

    assert resultado.aceitas == set()
    assert resultado.recusadas == {7: "coletar_janela sem coletora local"}
    assert fila.falhas == 1


def test_fila_local_com_coletora_despacha() -> None:
    chamadas: list[tuple[Any, Mensagem]] = []

    def coletora(sessao: Session, mensagem: Mensagem) -> str:
        chamadas.append((sessao, mensagem))
        return "continua"

    fila = FilaLocal(_fabrica, {}, _agora, coletora=coletora)

    resultado = fila.enviar_lote([_JANELA])

    assert resultado.aceitas == {7}
    assert [m for _, m in chamadas] == [_JANELA]
    assert isinstance(chamadas[0][0], SessaoFalsa)
    assert fila.falhas == 0


def test_fila_local_coletora_com_falha_conta_e_confirma() -> None:
    fila = FilaLocal(_fabrica, {}, _agora, coletora=lambda s, m: "falha")

    resultado = fila.enviar_lote([_JANELA])

    assert resultado.aceitas == {7}
    assert fila.falhas == 1


def test_fila_local_coletora_com_excecao_recusa() -> None:
    def coletora(sessao: Session, mensagem: Mensagem) -> str:
        raise RuntimeError("boom")

    fila = FilaLocal(_fabrica, {}, _agora, coletora=coletora)

    resultado = fila.enviar_lote([_JANELA])

    assert resultado.recusadas == {7: "RuntimeError('boom')"}
    assert fila.falhas == 1


def test_criar_fila_sqs_sem_url_de_janelas_falha() -> None:
    config = Config(fila="sqs", fila_url=_URL)

    with pytest.raises(ValueError, match="RADAR_FILA_JANELAS_URL"):
        criar_fila(config, _fabrica, {}, _agora, cliente_sqs=object())


def test_criar_fila_sqs_roteia_coletar_janela() -> None:
    config = Config(fila="sqs", fila_url=_URL, fila_janelas_url=_URL_JANELAS)

    fila = criar_fila(config, _fabrica, {}, _agora, cliente_sqs=object())

    assert isinstance(fila, FilaRoteada)
    assert isinstance(fila.padrao, FilaSQS)
    assert fila.padrao.url == _URL
    janelas = fila.rotas["coletar_janela"]
    assert isinstance(janelas, FilaSQS)
    assert janelas.url == _URL_JANELAS
