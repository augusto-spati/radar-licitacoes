"""Cache das respostas do provedor: produção no banco, evals em arquivos commitados."""

import hashlib
import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from radar.analise.custos import custo, engine_da_sessao
from radar.analise.modelos import RespostaLLM
from radar.analise.provedor import (
    ConfigIA,
    ErroProvedor,
    Pedido,
    PrecoModelo,
    Provedor,
    Resposta,
)


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _json(dados: Any) -> str:
    return json.dumps(dados, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def chave_cache(pedido: Pedido, modo_saida: str) -> str:
    """Muda se mudar modelo, modo de saída, prompt, esquema, `max_tokens` ou o trecho."""
    return _sha256(
        _json(
            {
                "modelo": pedido.modelo,
                "modo_saida": modo_saida,
                "sistema": pedido.sistema,
                "esquema": pedido.esquema,
                "max_tokens": pedido.max_tokens,
                "texto": _sha256(pedido.texto),
            }
        )
    )


def versao_prompt(sistema: str, esquema: dict[str, Any]) -> str:
    return _sha256(sistema + _json(esquema))[:12]


class Cache(Protocol):
    def ler(self, chave: str) -> Resposta | None: ...

    def gravar(self, chave: str, modelo: str, resposta: Resposta, custo_usd: Decimal) -> None: ...


def pasta_do_modelo(modelo: str) -> str:
    return modelo.replace(":", "_").replace(".", "_")


class CacheArquivos:
    """`<raiz>/<modelo>/<chave>.json`, JSON ordenado e indentado (diff legível no git)."""

    def __init__(self, raiz: Path) -> None:
        self.raiz = raiz

    def _caminho(self, chave: str, modelo: str | None = None) -> Path | None:
        if modelo is not None:
            return self.raiz / pasta_do_modelo(modelo) / f"{chave}.json"
        achados = list(self.raiz.glob(f"*/{chave}.json"))
        return achados[0] if achados else None

    def ler(self, chave: str) -> Resposta | None:
        caminho = self._caminho(chave)
        if caminho is None:
            return None
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        return Resposta(
            texto=dados["resposta"],
            tokens_entrada=dados["tokens_entrada"],
            tokens_saida=dados["tokens_saida"],
            latencia_ms=dados["latencia_ms"],
        )

    def gravar(self, chave: str, modelo: str, resposta: Resposta, custo_usd: Decimal) -> None:
        caminho = self._caminho(chave, modelo)
        assert caminho is not None
        caminho.parent.mkdir(parents=True, exist_ok=True)
        dados = {
            "chave": chave,
            "modelo": modelo,
            "resposta": resposta.texto,
            "tokens_entrada": resposta.tokens_entrada,
            "tokens_saida": resposta.tokens_saida,
            "latencia_ms": resposta.latencia_ms,
            "custo_usd": str(custo_usd),
        }
        texto = json.dumps(dados, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        caminho.write_text(texto, encoding="utf-8", newline="\n")


class CacheBanco:
    """Tabela `resposta_llm`, em conexão própria do engine (padrão de `coleta.trava`).

    Cada leitura e cada gravação é uma transação curta, fora da sessão de quem chama: a
    resposta paga fica commitada logo após a chamada, e o rollback de um edital que falhou
    não apaga o gasto (que o teto do mês soma) nem o cache. Nenhuma conexão fica presa entre
    uma chamada e outra (o Neon derruba transação ociosa).
    """

    def __init__(self, origem: Session | Engine) -> None:
        self._engine = origem if isinstance(origem, Engine) else engine_da_sessao(origem)

    def ler(self, chave: str) -> Resposta | None:
        with self._engine.connect() as conexao:
            linha = conexao.execute(
                select(
                    RespostaLLM.resposta,
                    RespostaLLM.tokens_entrada,
                    RespostaLLM.tokens_saida,
                    RespostaLLM.latencia_ms,
                ).where(RespostaLLM.chave == chave)
            ).first()
        if linha is None:
            return None
        return Resposta(
            texto=linha.resposta,
            tokens_entrada=linha.tokens_entrada,
            tokens_saida=linha.tokens_saida,
            latencia_ms=linha.latencia_ms,
        )

    def gravar(self, chave: str, modelo: str, resposta: Resposta, custo_usd: Decimal) -> None:
        comando = (
            insert(RespostaLLM)
            .values(
                chave=chave,
                modelo=modelo,
                resposta=resposta.texto,
                tokens_entrada=resposta.tokens_entrada,
                tokens_saida=resposta.tokens_saida,
                custo_usd=custo_usd,
                latencia_ms=resposta.latencia_ms,
            )
            .on_conflict_do_nothing(index_elements=[RespostaLLM.chave])
        )
        with self._engine.begin() as conexao:
            conexao.execute(comando)


class RespostaAusente(Exception):
    def __init__(self, chave: str) -> None:
        super().__init__(f"resposta ausente no cache: {chave}")
        self.chave = chave


class ProvedorComCache:
    """`gravar`: acerto devolve do cache, falta chama e grava. `replay`: nunca chama."""

    def __init__(
        self,
        interno: Provedor | None,
        cache: Cache,
        modo: Literal["gravar", "replay"],
        config: ConfigIA,
        antes_de_chamar: Callable[[Pedido], None] | None = None,
    ) -> None:
        if modo == "gravar" and interno is None:
            raise ValueError("modo gravar exige um provedor")
        self._interno = interno
        self._cache = cache
        self._modo = modo
        self._config = config
        self._antes_de_chamar = antes_de_chamar
        self.custo_usd = Decimal(0)
        self.chamadas_reais = 0

    def gerar(self, pedido: Pedido) -> Resposta:
        preco = self._config.preco(pedido.modelo)
        chave = chave_cache(pedido, preco.modo_saida)
        guardada = self._cache.ler(chave)
        if guardada is not None:
            return guardada
        if self._modo == "replay" or self._interno is None:
            raise RespostaAusente(chave)
        if self._antes_de_chamar is not None:
            self._antes_de_chamar(pedido)
        try:
            resposta = self._interno.gerar(pedido)
        except ErroProvedor as erro:
            if erro.resposta is not None:  # já cobrada: o gasto conta e a resposta vazia fica
                self._registrar(chave, pedido.modelo, erro.resposta, preco)
            raise
        self._registrar(chave, pedido.modelo, resposta, preco)
        return resposta

    def _registrar(self, chave: str, modelo: str, resposta: Resposta, preco: PrecoModelo) -> None:
        custo_usd = custo(resposta, preco)
        self._cache.gravar(chave, modelo, resposta, custo_usd)
        self.custo_usd += custo_usd
        self.chamadas_reais += 1
