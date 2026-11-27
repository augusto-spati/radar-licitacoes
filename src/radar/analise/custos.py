"""Custo por resposta, gasto do mês e o teto próprio da IA."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Connection, Engine, func, select
from sqlalchemy.orm import Session

from radar.analise.modelos import RespostaLLM
from radar.analise.provedor import ConfigIA, Pedido, PrecoModelo, Resposta

logger = logging.getLogger(__name__)

MILHAO = Decimal(1_000_000)
CARACTERES_POR_TOKEN = 3


class TetoAtingido(Exception):
    pass


def custo(resposta: Resposta, preco: PrecoModelo) -> Decimal:
    return (
        resposta.tokens_entrada * preco.entrada_usd_mtok
        + resposta.tokens_saida * preco.saida_usd_mtok
    ) / MILHAO


def estimar(pedido: Pedido, preco: PrecoModelo) -> Decimal:
    """Pior caso do pedido: caracteres/3 tokens de entrada e `max_tokens` de saída."""
    entrada = Decimal(len(pedido.sistema) + len(pedido.texto)) / CARACTERES_POR_TOKEN
    return (entrada * preco.entrada_usd_mtok + pedido.max_tokens * preco.saida_usd_mtok) / MILHAO


def _inicio_do_mes(momento: datetime) -> datetime:
    utc = momento.astimezone(UTC)
    return utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def engine_da_sessao(sessao: Session) -> Engine:
    """O engine por trás da sessão, para transações curtas em conexão própria."""
    bind = sessao.get_bind()
    return bind if isinstance(bind, Engine) else bind.engine


def gasto_do_mes(sessao: Session | Connection, agora: datetime) -> Decimal:
    """Soma de `resposta_llm.custo_usd` no mês UTC de `agora`."""
    inicio = _inicio_do_mes(agora)
    fim = (inicio.replace(year=inicio.year + 1, month=1) if inicio.month == 12
           else inicio.replace(month=inicio.month + 1))  # fmt: skip
    total = sessao.scalar(
        select(func.coalesce(func.sum(RespostaLLM.custo_usd), 0)).where(
            RespostaLLM.criada_em >= inicio, RespostaLLM.criada_em < fim
        )
    )
    return Decimal(total or 0)


def guarda_teto(
    sessao: Session,
    config: ConfigIA,
    agora: Callable[[], datetime],
    gasto_rodada: Callable[[], Decimal],
) -> Callable[[Pedido], None]:
    """Gancho `antes_de_chamar`: mês + rodada + estimativa acima do teto → `TetoAtingido`.

    `gasto_rodada` é o que a rodada gastou e ainda não está somado no banco (com `CacheBanco`,
    cada resposta é commitada na hora e já entra em `gasto_do_mes`: a rodada passa zero).
    O gasto do mês é lido numa conexão própria e curta, fora da transação da sessão.
    """
    engine = engine_da_sessao(sessao)

    def verificar(pedido: Pedido) -> None:
        with engine.connect() as conexao:
            mes = gasto_do_mes(conexao, agora())
        rodada = gasto_rodada()
        estimativa = estimar(pedido, config.preco(pedido.modelo))
        if mes + rodada + estimativa > config.teto_mensal_usd:
            logger.error(
                "teto_ia_atingido",
                extra={
                    "gasto_mes_usd": str(mes),
                    "gasto_rodada_usd": str(rodada),
                    "estimativa_usd": str(estimativa),
                    "teto_usd": str(config.teto_mensal_usd),
                },
            )
            raise TetoAtingido(
                f"teto mensal da IA: US$ {mes + rodada:.4f} gastos + US$ {estimativa:.4f} "
                f"estimados > US$ {config.teto_mensal_usd}"
            )

    return verificar
