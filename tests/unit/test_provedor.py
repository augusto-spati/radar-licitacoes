import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from radar.analise.exigencias import RespostaExtracao, esquema_resposta
from radar.analise.provedor import (
    ConfigIA,
    ErroProvedor,
    Pedido,
    PrecoModelo,
    ProvedorBedrock,
    carregar_config_ia,
    validar_modelo,
)

RAIZ = Path(__file__).resolve().parents[2]
DADOS = RAIZ / "tests" / "dados" / "ia"


class BedrockFalso:
    def __init__(self, resposta: dict[str, Any] | None = None) -> None:
        self.resposta = resposta
        self.chamadas: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.chamadas.append(kwargs)
        assert self.resposta is not None
        return self.resposta


def _fixture(nome: str) -> dict[str, Any]:
    dados: dict[str, Any] = json.loads((DADOS / nome).read_text(encoding="utf-8"))
    return dados


def _config(modo: str = "ferramenta", modelo: str = "amazon.nova-lite-v1:0") -> ConfigIA:
    return ConfigIA(
        regiao="us-east-2",
        modelo_padrao=modelo,
        teto_mensal_usd=Decimal("2"),
        max_editais_por_rodada=10,
        caracteres_por_trecho=60000,
        max_trechos=15,
        max_tokens=4096,
        modelos={
            modelo: PrecoModelo(
                modo_saida=modo,  # type: ignore[arg-type]
                entrada_usd_mtok=Decimal("0.06"),
                saida_usd_mtok=Decimal("0.24"),
            )
        },
    )


def _pedido(modelo: str = "amazon.nova-lite-v1:0") -> Pedido:
    return Pedido(
        modelo=modelo,
        sistema="extraia",
        texto="[[pagina 1]]\ncapital",
        esquema=esquema_resposta(),
        max_tokens=4096,
    )


@pytest.mark.parametrize("modelo", ["us.amazon.nova-lite-v1:0", "global.x", "eu.y", "apac.z"])
def test_prefixo_cross_region_recusado(modelo: str) -> None:
    falso = BedrockFalso()
    provedor = ProvedorBedrock(_config(modelo=modelo), cliente=falso)
    with pytest.raises(ValueError):
        provedor.gerar(_pedido(modelo))
    assert falso.chamadas == []


def test_validar_modelo_regiao_errada() -> None:
    with pytest.raises(ValueError):
        validar_modelo("amazon.nova-lite-v1:0", "us-east-1")
    with pytest.raises(ValueError):
        validar_modelo(
            "arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-lite-v1:0", "us-east-2"
        )
    validar_modelo("amazon.nova-lite-v1:0", "us-east-2")


def test_converse_json_schema_le_fixture() -> None:
    falso = BedrockFalso(_fixture("converse_json_schema.json"))
    resposta = ProvedorBedrock(_config("json_schema"), cliente=falso).gerar(_pedido())
    achados = RespostaExtracao.model_validate_json(resposta.texto).achados
    assert achados[0].campo == "capital_social_minimo"
    assert (resposta.tokens_entrada, resposta.tokens_saida) == (130, 159)
    formato = falso.chamadas[0]["outputConfig"]["textFormat"]
    assert formato["type"] == "json_schema"
    assert json.loads(formato["structure"]["jsonSchema"]["schema"]) == esquema_resposta()
    assert "toolConfig" not in falso.chamadas[0]


def test_converse_ferramenta_le_fixture() -> None:
    falso = BedrockFalso(_fixture("converse_ferramenta.json"))
    resposta = ProvedorBedrock(_config("ferramenta"), cliente=falso).gerar(_pedido())
    achados = RespostaExtracao.model_validate_json(resposta.texto).achados
    assert [a.campo for a in achados] == ["capital_social_minimo", "visita_tecnica"]
    chamada = falso.chamadas[0]
    ferramenta = chamada["toolConfig"]["tools"][0]["toolSpec"]
    assert ferramenta["inputSchema"]["json"] == esquema_resposta()
    assert chamada["toolConfig"]["toolChoice"] == {"tool": {"name": ferramenta["name"]}}
    assert "outputConfig" not in chamada


def test_bloco_de_raciocinio_ignorado() -> None:
    falso = BedrockFalso(_fixture("converse_ferramenta_raciocinio.json"))
    resposta = ProvedorBedrock(_config("ferramenta"), cliente=falso).gerar(_pedido())
    assert "reasoning" not in resposta.texto
    assert RespostaExtracao.model_validate_json(resposta.texto).achados


def test_raciocinio_ignorado_no_modo_texto() -> None:
    falso = BedrockFalso(
        {
            "output": {
                "message": {
                    "content": [
                        {"reasoningContent": {"reasoningText": {"text": "pensando..."}}},
                        {"text": '{"achados": []}'},
                    ]
                }
            },
            "usage": {"inputTokens": 1, "outputTokens": 2},
        }
    )
    resposta = ProvedorBedrock(_config("json_schema"), cliente=falso).gerar(_pedido())
    assert resposta.texto == '{"achados": []}'


def test_resposta_sem_texto_nem_ferramenta() -> None:
    falso = BedrockFalso(
        {"output": {"message": {"content": []}}, "usage": {"inputTokens": 1, "outputTokens": 0}}
    )
    with pytest.raises(ErroProvedor):
        ProvedorBedrock(_config("json_schema"), cliente=falso).gerar(_pedido())


def test_pedido_usa_temperatura_zero_e_max_tokens() -> None:
    falso = BedrockFalso(_fixture("converse_ferramenta.json"))
    ProvedorBedrock(_config(), cliente=falso).gerar(_pedido())
    chamada = falso.chamadas[0]
    assert chamada["modelId"] == "amazon.nova-lite-v1:0"
    assert chamada["inferenceConfig"] == {"temperature": 0, "maxTokens": 4096}
    assert chamada["system"] == [{"text": "extraia"}]
    assert chamada["messages"][0]["content"][0]["text"] == "[[pagina 1]]\ncapital"


def test_modelo_fora_da_config() -> None:
    falso = BedrockFalso()
    with pytest.raises(ValueError):
        ProvedorBedrock(_config(), cliente=falso).gerar(_pedido("qwen.qwen3-32b-v1:0"))
    assert falso.chamadas == []


def test_carregar_config_ia_do_repositorio() -> None:
    config = carregar_config_ia(RAIZ / "config" / "ia.toml")
    assert config.regiao == "us-east-2"
    assert config.modelo_padrao in config.modelos
    assert config.teto_mensal_usd == Decimal("2.0")
    assert config.max_trechos == 15
    assert config.modelos["amazon.nova-lite-v1:0"].modo_saida == "ferramenta"
    assert config.modelos["amazon.nova-lite-v1:0"].entrada_usd_mtok == Decimal("0.06")


def test_carregar_config_ia_recusa_perfil(tmp_path: Path) -> None:
    texto = (RAIZ / "config" / "ia.toml").read_text(encoding="utf-8")
    arquivo = tmp_path / "ia.toml"
    arquivo.write_text(
        texto.replace('[modelos."amazon.nova-lite-v1:0"]', '[modelos."us.amazon.nova-lite-v1:0"]'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        carregar_config_ia(arquivo)
    arquivo.write_text(texto.replace('regiao = "us-east-2"', 'regiao = "us-east-1"'), "utf-8")
    with pytest.raises(ValueError):
        carregar_config_ia(arquivo)
