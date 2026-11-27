from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from radar.analise.cache import CacheBanco, ProvedorComCache
from radar.analise.custos import TetoAtingido, custo, estimar, gasto_do_mes, guarda_teto
from radar.analise.modelos import RespostaLLM
from radar.analise.provedor import ConfigIA, ErroProvedor, Pedido, PrecoModelo, Resposta

MODELO = "amazon.nova-lite-v1:0"
PRECO = PrecoModelo(
    modo_saida="ferramenta", entrada_usd_mtok=Decimal("0.06"), saida_usd_mtok=Decimal("0.24")
)
CONFIG = ConfigIA(
    regiao="us-east-2",
    modelo_padrao=MODELO,
    teto_mensal_usd=Decimal("2"),
    max_editais_por_rodada=10,
    caracteres_por_trecho=60000,
    max_trechos=15,
    max_tokens=4096,
    modelos={MODELO: PRECO},
)
AGORA = datetime(2026, 11, 20, 12, tzinfo=UTC)


def _linha(chave: str, custo_usd: str, criada_em: datetime) -> RespostaLLM:
    return RespostaLLM(
        chave=chave,
        modelo=MODELO,
        resposta="{}",
        tokens_entrada=1,
        tokens_saida=1,
        custo_usd=Decimal(custo_usd),
        latencia_ms=1,
        criada_em=criada_em,
    )


def test_custo_e_estimativa() -> None:
    resposta = Resposta(texto="", tokens_entrada=1_000_000, tokens_saida=500_000, latencia_ms=1)
    assert custo(resposta, PRECO) == Decimal("0.18")
    pedido = Pedido(modelo=MODELO, sistema="", texto="x" * 3_000_000, esquema={}, max_tokens=0)
    assert estimar(pedido, PRECO) == Decimal("0.06")


def test_gasto_do_mes_soma_so_o_mes(sessao: Session) -> None:
    sessao.add_all(
        [
            _linha("a", "0.5", datetime(2026, 10, 31, 23, 59, tzinfo=UTC)),
            _linha("b", "0.25", datetime(2026, 11, 1, 0, 0, tzinfo=UTC)),
            _linha("c", "0.125", datetime(2026, 11, 19, tzinfo=UTC)),
            _linha("d", "1", datetime(2026, 12, 1, tzinfo=UTC)),
        ]
    )
    sessao.flush()
    assert gasto_do_mes(sessao, AGORA) == Decimal("0.375")


def test_guarda_teto_levanta_antes(engine: Engine, sessao: Session) -> None:
    # A guarda lê o gasto numa conexão própria: só vê o que já foi commitado.
    with Session(engine) as outra:
        outra.add(_linha("a", "1.99", datetime(2026, 11, 2, tzinfo=UTC)))
        outra.commit()
    pedido = Pedido(modelo=MODELO, sistema="", texto="x" * 3, esquema={}, max_tokens=100_000)
    guarda = guarda_teto(sessao, CONFIG, lambda: AGORA, lambda: Decimal(0))
    with pytest.raises(TetoAtingido):
        guarda(pedido)  # estimativa: 0,024


def test_guarda_teto_deixa_passar(sessao: Session) -> None:
    pedido = Pedido(modelo=MODELO, sistema="", texto="x" * 3, esquema={}, max_tokens=4096)
    guarda_teto(sessao, CONFIG, lambda: AGORA, lambda: Decimal("0.5"))(pedido)


def test_guarda_teto_soma_a_rodada(sessao: Session) -> None:
    pedido = Pedido(modelo=MODELO, sistema="", texto="x" * 3, esquema={}, max_tokens=4096)
    with pytest.raises(TetoAtingido):
        guarda_teto(sessao, CONFIG, lambda: AGORA, lambda: Decimal("2"))(pedido)


def test_cache_banco_ida_e_volta(sessao: Session) -> None:
    cache = CacheBanco(sessao)
    resposta = Resposta(texto="{}", tokens_entrada=3, tokens_saida=4, latencia_ms=50)
    cache.gravar("k1", MODELO, resposta, Decimal("0.001"))
    assert cache.ler("k1") == resposta
    assert cache.ler("k2") is None


def test_cache_banco_grava_commitado_e_fora_da_transacao_do_chamador(
    engine: Engine, sessao: Session
) -> None:
    cache = CacheBanco(sessao)
    resposta = Resposta(texto="{}", tokens_entrada=3, tokens_saida=4, latencia_ms=50)
    cache.gravar("k-commit", MODELO, resposta, Decimal("0.5"))
    sessao.rollback()  # o rollback do chamador não apaga o que foi pago

    with Session(engine) as outra:
        assert outra.scalar(select(func.count()).select_from(RespostaLLM)) == 1
        assert gasto_do_mes(outra, datetime.now(UTC)) == Decimal("0.5")
    cache.gravar("k-commit", MODELO, resposta, Decimal("0.5"))  # regravar não duplica
    assert not sessao.in_transaction()


def test_cache_banco_gasto_invalido_conta_no_mes_e_nao_e_reusado(
    engine: Engine, sessao: Session
) -> None:
    class ProvedorSemTexto:
        def __init__(self) -> None:
            self.chamadas = 0

        def gerar(self, pedido: Pedido) -> Resposta:
            self.chamadas += 1
            usada = Resposta(texto="", tokens_entrada=1_000_000, tokens_saida=0, latencia_ms=5)
            raise ErroProvedor("resposta sem texto nem ferramenta", resposta=usada)

    interno = ProvedorSemTexto()
    provedor = ProvedorComCache(interno, CacheBanco(sessao), "gravar", CONFIG)
    pedido = Pedido(modelo=MODELO, sistema="s", texto="t", esquema={}, max_tokens=10)
    for _ in range(2):
        with pytest.raises(ErroProvedor):
            provedor.gerar(pedido)
    assert interno.chamadas == 2  # a segunda rodada chama o provedor de novo

    with Session(engine) as outra:
        linhas = outra.scalars(select(RespostaLLM)).all()
        assert [linha.invalida for linha in linhas] == [True, True]
        assert gasto_do_mes(outra, datetime.now(UTC)) == Decimal("0.12")
