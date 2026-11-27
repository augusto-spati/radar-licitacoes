"""Relay: publica a outbox na fila, em lotes, com commit por lote."""

from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast, get_args

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

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
    tipos: Collection[TipoMensagem] | None = None,
) -> ResumoPublicacao:
    """Publica as pendentes (só as de `tipos`, se dado) percorrendo a outbox pelo id, uma vez.

    O cursor só avança: cada linha é tentada no máximo uma vez por chamada, e um lote inteiro
    recusado não para o percurso (recusas permanentes na cabeça não bloqueiam o resto).
    Linhas criadas durante a chamada (ids maiores, como a continuação que a fila local grava)
    entram no mesmo percurso. Duplicata é esperada: se a fila aceitou e o commit falhou, a
    mensagem sai de novo.

    Fila que consome no envio (`consome_no_envio`, a `FilaLocal`) pode levar horas por
    mensagem: aí nada fica travado. O relay lê o lote e solta a transação, entrega uma
    mensagem por vez e marca cada uma numa transação curta; a idempotência do consumidor
    cobre a duplicata de dois relays ao mesmo tempo. As demais usam `FOR UPDATE SKIP LOCKED`
    e um commit por lote.
    """
    relogio = agora or _agora
    if getattr(fila, "consome_no_envio", False):
        return _publicar_sem_trava(sessao, fila, lote, relogio, tipos)
    resumo = ResumoPublicacao(publicadas=0, recusadas=0)
    ultimo_id = 0
    while True:
        linhas = sessao.scalars(
            select(Outbox)
            .where(*_pendentes_depois(ultimo_id, tipos))
            .order_by(Outbox.id)
            .limit(lote)
            .with_for_update(skip_locked=True)
        ).all()
        if not linhas:
            sessao.commit()
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


def _publicar_sem_trava(
    sessao: Session,
    fila: Fila,
    lote: int,
    relogio: Callable[[], datetime],
    tipos: Collection[TipoMensagem] | None,
) -> ResumoPublicacao:
    resumo = ResumoPublicacao(publicadas=0, recusadas=0)
    ultimo_id = 0
    while True:
        linhas = sessao.execute(
            select(Outbox.id, Outbox.tipo, Outbox.carga)
            .where(*_pendentes_depois(ultimo_id, tipos))
            .order_by(Outbox.id)
            .limit(lote)
        ).all()
        sessao.commit()  # solta a transação (e a conexão) antes de consumir
        if not linhas:
            return resumo
        for outbox_id, tipo, carga in linhas:
            ultimo_id = outbox_id
            resultado = fila.enviar_lote([Mensagem(outbox_id, _cast_tipo(tipo), carga)])
            if outbox_id in resultado.aceitas:
                valores: dict[str, object] = {"publicada_em": relogio(), "erro": None}
                resumo.publicadas += 1
            else:
                valores = {
                    "tentativas": Outbox.tentativas + 1,
                    "erro": resultado.recusadas.get(outbox_id, "sem resposta da fila"),
                }
                resumo.recusadas += 1
            sessao.execute(
                update(Outbox)
                .where(Outbox.id == outbox_id, Outbox.publicada_em.is_(None))
                .values(**valores)
            )
            sessao.commit()


def _pendentes_depois(
    ultimo_id: int, tipos: Collection[TipoMensagem] | None
) -> list[ColumnElement[bool]]:
    condicoes = [Outbox.publicada_em.is_(None), Outbox.id > ultimo_id]
    if tipos is not None:
        condicoes.append(Outbox.tipo.in_(list(tipos)))
    return condicoes


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
