import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from radar.comum import metricas as m
from radar.comum.config import Config
from telemetria_memoria import EmissorEmMemoria


@pytest.fixture(autouse=True)
def _limpo() -> Iterator[None]:
    m.usar_emissor(None)
    yield
    m.usar_emissor(None)


class _ClienteFalso:
    def __init__(self, falhar: bool = False) -> None:
        self.chamadas: list[dict[str, Any]] = []
        self.falhar = falhar

    def put_metric_data(self, **kwargs: Any) -> None:
        if self.falhar:
            raise RuntimeError("recusado")
        self.chamadas.append(kwargs)


def _linhas_emf(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [r.__dict__ for r in caplog.records if "_aws" in r.__dict__]


def test_metrica_fora_do_catalogo_levanta() -> None:
    with pytest.raises(ValueError):
        m.registrar("Inventada", 1.0)


def test_dimensao_invalida_levanta() -> None:
    with pytest.raises(ValueError):
        m.registrar("LatenciaRequisicao", 1.0, {"fonte": "x"})
    with pytest.raises(ValueError):
        m.registrar("LatenciaRequisicao", 1.0)  # dimensão obrigatória ausente
    with pytest.raises(ValueError):
        m.registrar("FrescorHoras", 1.0, {"fonte": "pncp"})


def test_catalogo_exato() -> None:
    assert set(m.CATALOGO) == {"FrescorHoras", "PaginaComErro", "LatenciaRequisicao", "ExecucaoOk"}
    assert m.CATALOGO["LatenciaRequisicao"].unidade == "Seconds"
    assert m.CATALOGO["LatenciaRequisicao"].valores_dimensao["fonte"] == {
        "pncp",
        "sanepar",
        "compesa",
    }
    assert m.NAMESPACE == "Radar"


def test_emf_valido(caplog: pytest.LogCaptureFixture) -> None:
    m.usar_emissor(m.EmissorEMF())
    with caplog.at_level(logging.INFO, logger="radar.metricas"):
        m.registrar("LatenciaRequisicao", 2.5, {"fonte": "pncp"})
        m.registrar("FrescorHoras", 3.0)
    latencia, frescor = _linhas_emf(caplog)
    bloco = latencia["_aws"]["CloudWatchMetrics"][0]
    assert bloco["Namespace"] == "Radar"
    assert bloco["Dimensions"] == [["fonte"]]
    assert bloco["Metrics"][0] == {"Name": "LatenciaRequisicao", "Unit": "Seconds"}
    assert latencia["LatenciaRequisicao"] == 2.5
    assert latencia["fonte"] == "pncp"
    assert isinstance(latencia["_aws"]["Timestamp"], int)
    assert latencia["_aws"]["Timestamp"] > 1_600_000_000_000
    assert frescor["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [[]]
    assert frescor["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Unit"] == "None"


def test_emf_sai_como_json_de_topo(capsys: pytest.CaptureFixture[str]) -> None:
    from radar.comum import configurar_logs

    configurar_logs("INFO")
    m.usar_emissor(m.EmissorEMF())
    m.registrar("PaginaComErro", 0.0)
    saida = capsys.readouterr()
    linha = json.loads([x for x in (saida.out + saida.err).splitlines() if x.strip()][-1])
    assert linha["evento"] == "metrica_registrada"
    assert linha["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Name"] == "PaginaComErro"
    assert linha["PaginaComErro"] == 0.0


def test_emf_propriedade_nao_vira_dimensao(caplog: pytest.LogCaptureFixture) -> None:
    m.usar_emissor(m.EmissorEMF())
    with caplog.at_level(logging.INFO, logger="radar.metricas"):
        m.registrar("FrescorHoras", 10.0, propriedades={"fonte": "pncp"})
    (linha,) = _linhas_emf(caplog)
    assert linha["fonte"] == "pncp"
    assert linha["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [[]]


def test_cloudwatch_agrega_valores_repetidos() -> None:
    cliente = _ClienteFalso()
    m.usar_emissor(m.EmissorCloudWatch(cliente=cliente))
    for valor in (1.0, 1.0, 2.0):
        m.registrar("LatenciaRequisicao", valor, {"fonte": "sanepar"})
    m.descarregar()
    (chamada,) = cliente.chamadas
    assert chamada["Namespace"] == "Radar"
    (datum,) = chamada["MetricData"]
    assert datum["MetricName"] == "LatenciaRequisicao"
    assert datum["Dimensions"] == [{"Name": "fonte", "Value": "sanepar"}]
    assert datum["Values"] == [1.0, 2.0]
    assert datum["Counts"] == [2, 1]
    assert datum["Unit"] == "Seconds"


def test_cloudwatch_limita_valores_por_datum() -> None:
    cliente = _ClienteFalso()
    m.usar_emissor(m.EmissorCloudWatch(cliente=cliente))
    for valor in range(400):
        m.registrar("FrescorHoras", float(valor))
    m.descarregar()
    datums = [d for c in cliente.chamadas for d in c["MetricData"]]
    assert all(len(d["Values"]) <= 150 for d in datums)
    assert sum(sum(d["Counts"]) for d in datums) == 400


def test_cloudwatch_descarregar_esvazia() -> None:
    cliente = _ClienteFalso()
    m.usar_emissor(m.EmissorCloudWatch(cliente=cliente))
    m.registrar("ExecucaoOk", 1.0)
    m.descarregar()
    m.descarregar()
    assert len(cliente.chamadas) == 1


def test_cloudwatch_erro_vira_log(caplog: pytest.LogCaptureFixture) -> None:
    m.usar_emissor(m.EmissorCloudWatch(cliente=_ClienteFalso(falhar=True)))
    m.registrar("ExecucaoOk", 1.0)
    with caplog.at_level(logging.WARNING):
        m.descarregar()  # não levanta
    assert any(r.getMessage() == "metricas_erro" for r in caplog.records)


def test_cloudwatch_loga_metrica_registrada_com_propriedades(
    caplog: pytest.LogCaptureFixture,
) -> None:
    m.usar_emissor(m.EmissorCloudWatch(cliente=_ClienteFalso()))
    with caplog.at_level(logging.INFO, logger="radar.metricas"):
        m.registrar("FrescorHoras", 5.0, propriedades={"fonte": "sanepar"})
    (registro,) = [r for r in caplog.records if r.getMessage() == "metrica_registrada"]
    assert registro.__dict__["fonte"] == "sanepar"
    assert registro.__dict__["valor"] == 5.0
    assert "_aws" not in registro.__dict__


def test_desligada_nao_emite(caplog: pytest.LogCaptureFixture) -> None:
    m.configurar_metricas(Config(metricas="desligada"))
    with caplog.at_level(logging.INFO, logger="radar.metricas"):
        m.registrar("ExecucaoOk", 1.0)
        m.descarregar()
    assert not _linhas_emf(caplog)


def test_configurar_escolhe_emissor() -> None:
    m.configurar_metricas(Config(metricas="emf"))
    assert isinstance(m.emissor_atual(), m.EmissorEMF)
    m.usar_emissor(None)
    m.configurar_metricas(Config(metricas="cloudwatch"))
    assert isinstance(m.emissor_atual(), m.EmissorCloudWatch)


def test_configurar_e_idempotente(metricas: EmissorEmMemoria) -> None:
    m.configurar_metricas(Config(metricas="emf"))
    assert m.emissor_atual() is metricas
    m.registrar("PaginaComErro", 1.0)
    assert [(r.nome, r.valor) for r in metricas.registros] == [("PaginaComErro", 1.0)]


def test_cloudwatch_cliente_com_timeouts_e_retentativa_curtos(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import boto3

    pedidos: list[dict[str, Any]] = []

    class Cliente:
        def put_metric_data(self, **kwargs: Any) -> None:
            raise RuntimeError("CloudWatch fora")

    def client(servico: str, **kwargs: Any) -> Cliente:
        pedidos.append({"servico": servico, **kwargs})
        return Cliente()

    monkeypatch.setattr(boto3, "client", client)
    m.usar_emissor(m.EmissorCloudWatch(regiao="us-east-2"))
    m.registrar("ExecucaoOk", 1)
    with caplog.at_level(logging.WARNING):
        m.descarregar()  # falha engolida e logada
    (pedido,) = pedidos
    assert pedido["servico"] == "cloudwatch" and pedido["region_name"] == "us-east-2"
    config = pedido["config"]
    assert (config.connect_timeout, config.read_timeout) == (2, 5)
    assert config.retries == {"max_attempts": 2, "mode": "standard"}
    assert any(r.getMessage() == "metricas_erro" for r in caplog.records)
