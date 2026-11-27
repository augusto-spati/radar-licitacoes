"""Sem transação aberta durante rede e Bedrock; o gasto pago sobrevive a qualquer rollback.

Usa sessões reais (o `sessao` dos outros testes segura uma transação externa de propósito).
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from test_pipeline_analise import (
    ALVO,
    CONFIG,
    MODELO,
    PDF_TEXTO,
    PERFIL,
    Rede,
    _resposta_padrao,
)

from radar.analise import (
    AnaliseEdital,
    ArmazemLocal,
    CacheBanco,
    Dependencias,
    DocumentoEdital,
    Pedido,
    ProvedorComCache,
    Resposta,
    analisar_edital,
    guarda_teto,
)
from radar.analise.custos import gasto_do_mes
from radar.analise.modelos import RespostaLLM
from radar.comum import Base, fabrica_sessao

AGORA = datetime(2026, 11, 27, 12, tzinfo=UTC)
# Trechos pequenos: o PDF de 2 páginas vira vários pedidos.
CONFIG_PICADO = replace(CONFIG, caracteres_por_trecho=300)


@pytest.fixture
def fabrica(engine: Engine) -> Iterator[sessionmaker[Session]]:
    yield fabrica_sessao(engine)
    tabelas = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conexao:
        conexao.execute(text(f"TRUNCATE {tabelas} RESTART IDENTITY CASCADE"))


def _ociosas_em_transacao(espiao: Engine) -> int:
    with espiao.connect() as conexao:
        return int(
            conexao.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()"
                )
            )
            or 0
        )


class ProvedorEspiao:
    """Confere, a cada chamada paga, que nada do banco está preso; falha na `falhar_em`."""

    def __init__(self, engine: Engine, sessao: Session, falhar_em: int | None = None) -> None:
        self.engine = engine
        self.sessao = sessao
        self.falhar_em = falhar_em
        self.espiao = create_engine(engine.url)
        self.chamadas = 0
        self.conferencias: list[tuple[int, bool, int]] = []

    def gerar(self, pedido: Pedido) -> Resposta:
        self.chamadas += 1
        self.conferencias.append(
            (
                self.engine.pool.checkedout(),  # type: ignore[attr-defined]
                self.sessao.in_transaction(),
                _ociosas_em_transacao(self.espiao),
            )
        )
        if self.chamadas == self.falhar_em:
            raise RuntimeError("Bedrock caiu no meio do edital")
        return Resposta(
            texto=_resposta_padrao(pedido), tokens_entrada=1000, tokens_saida=100, latencia_ms=1
        )


def _dep(sessao: Session, provedor: ProvedorEspiao, tmp_path: Path) -> Dependencias:
    com_cache = ProvedorComCache(
        provedor,
        CacheBanco(sessao),
        "gravar",
        CONFIG_PICADO,
        guarda_teto(sessao, CONFIG_PICADO, lambda: AGORA, lambda: Decimal(0)),
    )
    return Dependencias(
        cliente_arquivos=Rede({1: PDF_TEXTO}).cliente(),
        armazem=ArmazemLocal(tmp_path),
        provedor=com_cache,
        config=CONFIG_PICADO,
        perfil=PERFIL,
        modelo=MODELO,
    )


def test_nenhuma_transacao_aberta_durante_a_chamada_ao_provedor(
    engine: Engine, fabrica: sessionmaker[Session], tmp_path: Path
) -> None:
    with fabrica() as sessao:
        provedor = ProvedorEspiao(engine, sessao)
        analisar_edital(sessao, ALVO, _dep(sessao, provedor, tmp_path), AGORA)
        assert not sessao.in_transaction()

    assert provedor.chamadas >= 2
    assert provedor.conferencias == [(0, False, 0)] * provedor.chamadas


def test_falha_no_meio_do_edital_mantem_respostas_pagas_e_gasto(
    engine: Engine, fabrica: sessionmaker[Session], tmp_path: Path
) -> None:
    with fabrica() as sessao:
        provedor = ProvedorEspiao(engine, sessao, falhar_em=2)
        with pytest.raises(RuntimeError):
            analisar_edital(sessao, ALVO, _dep(sessao, provedor, tmp_path), AGORA)
        sessao.rollback()  # o que a cli faz com um edital que falhou

    with fabrica() as sessao:
        assert sessao.scalar(select(func.count()).select_from(RespostaLLM)) == 1
        assert gasto_do_mes(sessao, AGORA) == Decimal("0.000084")
        assert sessao.scalar(select(func.count()).select_from(AnaliseEdital)) == 0
        [documento] = sessao.scalars(select(DocumentoEdital)).all()
        assert documento.status == "extraido"
        assert documento.pagina_inicial == 1

    # A segunda rodada não paga de novo o trecho já respondido.
    with fabrica() as sessao:
        provedor = ProvedorEspiao(engine, sessao)
        analisar_edital(sessao, ALVO, _dep(sessao, provedor, tmp_path), AGORA)
    with fabrica() as sessao:
        total = sessao.scalar(select(func.count()).select_from(RespostaLLM))
    assert total == provedor.chamadas + 1
