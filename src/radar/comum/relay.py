"""Relay: publica a outbox na fila, em lotes, com commit por lote."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast, get_args

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from radar.comum.fila import Fila, Mensagem
from radar.comum.outbox import Outbox, TipoMensagem


@dataclass
class ResumoPublicacao:
    publicadas: int
    recusadas: int


def _agora() -> datetime:
    return datetime.now(UTC)


def publicar_outbox(
    sessao: Session,
    fila: Fila,
    lote: int = 10,
    agora: Callable[[], datetime] | None = None,
) -> ResumoPublicacao:
    """Publica pendentes até esvaziar ou até um lote inteiro ser recusado.

    Duplicata é esperada: se a fila aceitou e o commit falhou, a mensagem sai de novo.
    """
    relogio = agora or _agora
    resumo = ResumoPublicacao(publicadas=0, recusadas=0)
    ultimo_id = 0
    while True:
        linhas = sessao.scalars(
            select(Outbox)
            .where(Outbox.publicada_em.is_(None), Outbox.id > ultimo_id)
            .order_by(Outbox.id)
            .limit(lote)
            .with_for_update(skip_locked=True)
        ).all()
        if not linhas:
            return resumo
        mensagens = [Mensagem(o.id, _cast_tipo(o.tipo), o.carga) for o in linhas]
        resultado = fila.enviar_lote(mensagens)
        ultimo_id = linhas[-1].id
        aceitas = 0
        for o in linhas:
            if o.id in resultado.aceitas:
                o.publicada_em = relogio()
                o.erro = None
                aceitas += 1
            else:
                o.tentativas += 1
                o.erro = resultado.recusadas.get(o.id, "sem resposta da fila")
        sessao.commit()
        resumo.publicadas += aceitas
        resumo.recusadas += len(linhas) - aceitas
        if aceitas == 0:
            return resumo


def _cast_tipo(tipo: str) -> TipoMensagem:
    if tipo not in get_args(TipoMensagem):
        raise ValueError(f"tipo de outbox desconhecido: {tipo!r}")
    return cast(TipoMensagem, tipo)


def limpar_outbox(sessao: Session, agora: datetime, dias: int = 7) -> int:
    corte = agora - timedelta(days=dias)
    resultado = sessao.execute(
        delete(Outbox).where(Outbox.publicada_em.is_not(None), Outbox.publicada_em < corte)
    )
    sessao.commit()
    return int(resultado.rowcount)  # type: ignore[attr-defined]
