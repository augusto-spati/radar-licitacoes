"""Trava de uma fonte por lease em tabela (`trava_fonte`): exclusão mútua sem sessão presa.

Substitui o advisory lock. O lock de sessão exigia uma conexão dedicada que ficava com
transação aberta e ociosa durante o trabalho inteiro: o Neon a encerrava por
`idle_in_transaction_session_timeout` (a exclusão mútua sumia no meio da coleta e o unlock
estourava no fim), e pelo pooler do Neon (PgBouncer em modo transação) um lock de sessão
nem é confiável. O lease é uma linha com dono e prazo, obtida e liberada em transações
curtas, cada uma commitada na hora, numa conexão do engine separada da sessão do chamador
(não mistura com a transação dele). Entre a obtenção e a liberação nenhuma conexão fica
presa.

Limite: o lease vale `duracao` (30 min por padrão) desde a última renovação
(`obter_trava` com o mesmo dono). O retrato e o coletor do PNCP renovam a cada página e
param se o perderem (outro processo o tomou depois de vencer), então o prazo só precisa
cobrir uma página. Um processo morto deixa a fonte presa até o prazo vencer.
"""

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import Engine, case, delete, func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from radar.coleta.modelos import TravaFonte

logger = logging.getLogger(__name__)

DURACAO_PADRAO = timedelta(minutes=30)


def _engine(sessao: Session) -> Engine:
    bind = sessao.get_bind()
    return bind if isinstance(bind, Engine) else bind.engine


def obter_trava(sessao: Session, fonte: str, dono: uuid.UUID, duracao: timedelta) -> bool:
    """Obtém (ou renova, se `dono` já a tem) o lease da fonte; False se outro o tem vivo."""
    agora = func.now()
    novo = pg_insert(TravaFonte).values(
        fonte=fonte, dono=dono, expira_em=agora + duracao, obtida_em=agora
    )
    comando = novo.on_conflict_do_update(
        index_elements=[TravaFonte.fonte],
        set_={
            "dono": novo.excluded.dono,
            "expira_em": novo.excluded.expira_em,
            # Renovar (mesmo dono) mantém quando foi obtida; tomar de outro reinicia.
            "obtida_em": case(
                (TravaFonte.dono == novo.excluded.dono, TravaFonte.obtida_em),
                else_=novo.excluded.obtida_em,
            ),
        },
        where=or_(TravaFonte.expira_em < agora, TravaFonte.dono == novo.excluded.dono),
    ).returning(TravaFonte.dono)
    with _engine(sessao).begin() as conexao:
        # Sem linha devolvida: o lease existe, está vivo e é de outro dono.
        vencedor: uuid.UUID | None = conexao.scalar(comando)
    return vencedor == dono


def liberar_trava(sessao: Session, fonte: str, dono: uuid.UUID) -> None:
    """Apaga o lease só se ainda for de `dono` (se expirou e outro tomou, fica o do outro).

    Uma falha aqui só é registrada: o trabalho já terminou e o lease vence sozinho.
    """
    try:
        with _engine(sessao).begin() as conexao:
            conexao.execute(
                delete(TravaFonte).where(TravaFonte.fonte == fonte, TravaFonte.dono == dono)
            )
    except SQLAlchemyError:
        logger.exception("trava_liberacao_falhou", extra={"fonte": fonte})


@contextmanager
def trava_fonte(sessao: Session, fonte: str, duracao: timedelta = DURACAO_PADRAO) -> Iterator[bool]:
    """Lease da fonte; rende True se obtido. Não espera: False = outro processo a tem.

    Vale `duracao` (veja o limite no docstring do módulo).
    """
    dono = uuid.uuid4()
    obtida = obter_trava(sessao, fonte, dono, duracao)
    try:
        yield obtida
    finally:
        if obtida:
            liberar_trava(sessao, fonte, dono)
