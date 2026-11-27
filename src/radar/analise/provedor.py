"""Provedor de modelo trocável. Produção: Bedrock Converse em us-east-2, só foundation models."""

import json
import time
import tomllib
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol

REGIAO = "us-east-2"
PREFIXOS_PROIBIDOS = ("us.", "global.", "eu.", "apac.")
NOME_FERRAMENTA = "registrar_achados"
TIMEOUT_LEITURA_S = 300
TENTATIVAS = 5

ModoSaida = Literal["json_schema", "ferramenta"]


@dataclass(frozen=True)
class Pedido:
    modelo: str
    sistema: str
    texto: str
    esquema: dict[str, Any]
    max_tokens: int
    # Saída cortada em `max_tokens` vira `ErroProvedor` (paga, nunca cache). A planilha (7b.2)
    # pede: uma lista de linhas cortada no meio parece válida. Fora da chave do cache.
    recusar_truncada: bool = False


@dataclass(frozen=True)
class Resposta:
    texto: str
    tokens_entrada: int
    tokens_saida: int
    latencia_ms: int


class Provedor(Protocol):
    def gerar(self, pedido: Pedido) -> Resposta: ...


@dataclass(frozen=True)
class PrecoModelo:
    modo_saida: ModoSaida
    entrada_usd_mtok: Decimal
    saida_usd_mtok: Decimal


@dataclass(frozen=True)
class ConfigIA:
    regiao: str
    modelo_padrao: str
    teto_mensal_usd: Decimal
    max_editais_por_rodada: int
    caracteres_por_trecho: int
    max_trechos: int
    max_tokens: int
    modelos: dict[str, PrecoModelo]

    def preco(self, modelo: str) -> PrecoModelo:
        if modelo not in self.modelos:
            raise ValueError(f"modelo {modelo!r} não está em config/ia.toml")
        return self.modelos[modelo]


class ErroProvedor(Exception):
    """Resposta inutilizável. `resposta` traz os tokens quando a chamada já foi cobrada."""

    def __init__(self, mensagem: str, resposta: "Resposta | None" = None) -> None:
        super().__init__(mensagem)
        self.resposta = resposta


def validar_modelo(modelo: str, regiao: str) -> None:
    """Recusa perfis de inferência cross-region e qualquer região que não seja us-east-2."""
    if regiao != REGIAO:
        raise ValueError(f"região {regiao!r} recusada: só {REGIAO}")
    if modelo.startswith(PREFIXOS_PROIBIDOS):
        raise ValueError(f"modelo {modelo!r} recusado: perfil de inferência cross-region")
    if modelo.startswith("arn:") and f":{REGIAO}::foundation-model/" not in modelo:
        raise ValueError(f"modelo {modelo!r} recusado: só foundation-model de {REGIAO}")


def _decimal(valor: object) -> Decimal:
    return Decimal(str(valor))


def carregar_config_ia(caminho: Path) -> ConfigIA:
    with caminho.open("rb") as arquivo:
        dados = tomllib.load(arquivo)
    regiao = str(dados["regiao"])
    modelos: dict[str, PrecoModelo] = {}
    for modelo, valores in dados.get("modelos", {}).items():
        validar_modelo(modelo, regiao)
        modo = valores["modo_saida"]
        if modo not in ("json_schema", "ferramenta"):
            raise ValueError(f"modo_saida inválido para {modelo}: {modo!r}")
        modelos[modelo] = PrecoModelo(
            modo_saida=modo,
            entrada_usd_mtok=_decimal(valores["entrada_usd_mtok"]),
            saida_usd_mtok=_decimal(valores["saida_usd_mtok"]),
        )
    config = ConfigIA(
        regiao=regiao,
        modelo_padrao=str(dados["modelo_padrao"]),
        teto_mensal_usd=_decimal(dados["teto_mensal_usd"]),
        max_editais_por_rodada=int(dados["max_editais_por_rodada"]),
        caracteres_por_trecho=int(dados["caracteres_por_trecho"]),
        max_trechos=int(dados["max_trechos"]),
        max_tokens=int(dados["max_tokens"]),
        modelos=modelos,
    )
    validar_modelo(config.modelo_padrao, regiao)
    config.preco(config.modelo_padrao)
    return config


def _criar_cliente(regiao: str) -> Any:
    import boto3  # type: ignore[import-untyped]
    from botocore.config import Config  # type: ignore[import-untyped]

    return boto3.client(
        "bedrock-runtime",
        region_name=regiao,
        config=Config(
            read_timeout=TIMEOUT_LEITURA_S,
            retries={"mode": "adaptive", "max_attempts": TENTATIVAS},
        ),
    )


class ProvedorBedrock:
    """Converse com saída estruturada: esquema JSON ou ferramenta única forçada (por modelo)."""

    def __init__(self, config: ConfigIA, cliente: Any | None = None) -> None:
        self._config = config
        self._cliente = cliente

    def gerar(self, pedido: Pedido) -> Resposta:
        validar_modelo(pedido.modelo, self._config.regiao)
        modo = self._config.preco(pedido.modelo).modo_saida
        if self._cliente is None:
            self._cliente = _criar_cliente(self._config.regiao)
        argumentos: dict[str, Any] = {
            "modelId": pedido.modelo,
            "system": [{"text": pedido.sistema}],
            "messages": [{"role": "user", "content": [{"text": pedido.texto}]}],
            "inferenceConfig": {"temperature": 0, "maxTokens": pedido.max_tokens},
        }
        if modo == "json_schema":
            argumentos["outputConfig"] = {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "schema": json.dumps(pedido.esquema),
                            "name": "extracao",
                            "description": "Achados do trecho do edital",
                        }
                    },
                }
            }
        else:
            argumentos["toolConfig"] = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": NOME_FERRAMENTA,
                            "description": "Registra os achados do trecho do edital",
                            "inputSchema": {"json": pedido.esquema},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": NOME_FERRAMENTA}},
            }
        inicio = time.monotonic()
        bruto = self._cliente.converse(**argumentos)
        latencia_ms = int((time.monotonic() - inicio) * 1000)
        usada = Resposta(
            texto="",
            tokens_entrada=int(bruto.get("usage", {}).get("inputTokens", 0)),
            tokens_saida=int(bruto.get("usage", {}).get("outputTokens", 0)),
            latencia_ms=latencia_ms,
        )
        if pedido.recusar_truncada and bruto.get("stopReason") == "max_tokens":
            raise ErroProvedor("resposta cortada em max_tokens", resposta=usada)
        try:
            texto = _texto_da_resposta(bruto, modo)
        except ErroProvedor as erro:
            raise ErroProvedor(str(erro), resposta=usada) from erro
        return replace(usada, texto=texto)


def _texto_da_resposta(bruto: dict[str, Any], modo: ModoSaida) -> str:
    """Lê só o texto ou o `toolUse.input`; blocos de raciocínio (gpt-oss, qwen) ficam de fora."""
    blocos = bruto.get("output", {}).get("message", {}).get("content", [])
    if modo == "ferramenta":
        for bloco in blocos:
            if "toolUse" in bloco:
                return json.dumps(bloco["toolUse"].get("input", {}), ensure_ascii=False)
    textos = [bloco["text"] for bloco in blocos if "text" in bloco]
    if textos:
        return "".join(textos)
    raise ErroProvedor(f"resposta sem texto nem ferramenta (stopReason={bruto.get('stopReason')})")
