from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from radar.analise.cache import (
    CacheArquivos,
    ProvedorComCache,
    RespostaAusente,
    chave_cache,
    versao_prompt,
)
from radar.analise.provedor import ConfigIA, ErroProvedor, Pedido, PrecoModelo, Resposta

MODELO = "amazon.nova-lite-v1:0"
CONFIG = ConfigIA(
    regiao="us-east-2",
    modelo_padrao=MODELO,
    teto_mensal_usd=Decimal("2"),
    max_editais_por_rodada=10,
    caracteres_por_trecho=60000,
    max_trechos=15,
    max_tokens=4096,
    modelos={
        MODELO: PrecoModelo(
            modo_saida="ferramenta",
            entrada_usd_mtok=Decimal("0.06"),
            saida_usd_mtok=Decimal("0.24"),
        )
    },
)
PEDIDO = Pedido(modelo=MODELO, sistema="s", texto="t", esquema={"type": "object"}, max_tokens=4096)


class ProvedorContado:
    def __init__(self) -> None:
        self.chamadas = 0

    def gerar(self, pedido: Pedido) -> Resposta:
        self.chamadas += 1
        return Resposta(
            texto='{"achados": []}', tokens_entrada=1_000_000, tokens_saida=0, latencia_ms=5
        )


def test_chave_muda_com_sistema_esquema_modelo_texto() -> None:
    variacoes = [
        replace(PEDIDO, sistema="outro"),
        replace(PEDIDO, esquema={"type": "array"}),
        replace(PEDIDO, modelo="qwen.qwen3-32b-v1:0"),
        replace(PEDIDO, texto="outro"),
        replace(PEDIDO, max_tokens=10),
    ]
    chaves = {chave_cache(p, "ferramenta") for p in variacoes}
    assert len(chaves) == 5
    assert chave_cache(PEDIDO, "ferramenta") not in chaves
    assert chave_cache(PEDIDO, "ferramenta") == chave_cache(replace(PEDIDO), "ferramenta")
    assert chave_cache(PEDIDO, "ferramenta") != chave_cache(PEDIDO, "json_schema")


def test_versao_prompt() -> None:
    versao = versao_prompt("s", {"type": "object"})
    assert len(versao) == 12
    assert versao != versao_prompt("s2", {"type": "object"})
    assert versao != versao_prompt("s", {"type": "array"})


def test_replay_sem_entrada_levanta(tmp_path: Path) -> None:
    interno = ProvedorContado()
    provedor = ProvedorComCache(interno, CacheArquivos(tmp_path), "replay", CONFIG)
    with pytest.raises(RespostaAusente) as erro:
        provedor.gerar(PEDIDO)
    assert erro.value.chave == chave_cache(PEDIDO, "ferramenta")
    assert interno.chamadas == 0


def test_gravar_chama_uma_vez_e_depois_acerta(tmp_path: Path) -> None:
    interno = ProvedorContado()
    provedor = ProvedorComCache(interno, CacheArquivos(tmp_path), "gravar", CONFIG)
    primeira = provedor.gerar(PEDIDO)
    segunda = provedor.gerar(PEDIDO)
    assert interno.chamadas == 1
    assert provedor.chamadas_reais == 1
    assert provedor.custo_usd == Decimal("0.06")
    assert primeira == segunda


def test_replay_le_o_que_gravar_gravou(tmp_path: Path) -> None:
    ProvedorComCache(ProvedorContado(), CacheArquivos(tmp_path), "gravar", CONFIG).gerar(PEDIDO)
    replay = ProvedorComCache(None, CacheArquivos(tmp_path), "replay", CONFIG)
    assert replay.gerar(PEDIDO).texto == '{"achados": []}'
    assert replay.custo_usd == 0


def test_antes_de_chamar_so_em_chamada_real(tmp_path: Path) -> None:
    vistos: list[Pedido] = []
    provedor = ProvedorComCache(
        ProvedorContado(), CacheArquivos(tmp_path), "gravar", CONFIG, antes_de_chamar=vistos.append
    )
    provedor.gerar(PEDIDO)
    provedor.gerar(PEDIDO)
    assert vistos == [PEDIDO]


def test_cache_arquivos_ida_e_volta(tmp_path: Path) -> None:
    cache = CacheArquivos(tmp_path)
    resposta = Resposta(texto="{}", tokens_entrada=3, tokens_saida=4, latencia_ms=50)
    cache.gravar("abc", MODELO, resposta, Decimal("0.001"))
    assert cache.ler("abc") == resposta
    assert (tmp_path / "amazon_nova-lite-v1_0" / "abc.json").is_file()
    assert cache.ler("nada") is None


def test_erro_do_provedor_depois_de_pago_conta_o_gasto(tmp_path: Path) -> None:
    class ProvedorSemTexto:
        def gerar(self, pedido: Pedido) -> Resposta:
            usada = Resposta(texto="", tokens_entrada=1_000_000, tokens_saida=0, latencia_ms=5)
            raise ErroProvedor("resposta sem texto nem ferramenta", resposta=usada)

    cache = CacheArquivos(tmp_path)
    provedor = ProvedorComCache(ProvedorSemTexto(), cache, "gravar", CONFIG)
    with pytest.raises(ErroProvedor):
        provedor.gerar(PEDIDO)
    assert provedor.custo_usd == Decimal("0.06")
    assert provedor.chamadas_reais == 1
    # gravada: a próxima rodada lê a resposta vazia (trecho inválido) em vez de pagar de novo
    guardada = cache.ler(chave_cache(PEDIDO, "ferramenta"))
    assert guardada is not None
    assert guardada.texto == ""
