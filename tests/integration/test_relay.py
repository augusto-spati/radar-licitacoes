"""Relay da outbox: publica em lotes, tolera recusa, trava com SKIP LOCKED."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from radar.comum.fila import Mensagem, ResultadoEnvio
from radar.comum.outbox import Outbox, enfileirar
from radar.comum.relay import limpar_outbox, publicar_outbox


class FilaFalsa:
    def __init__(self, recusar: set[int] | None = None) -> None:
        self.recusar = recusar or set()
        self.recebidas: list[int] = []

    def enviar_lote(self, mensagens: list[Mensagem]) -> ResultadoEnvio:
        r = ResultadoEnvio(aceitas=set(), recusadas={})
        for m in mensagens:
            if m.outbox_id in self.recusar:
                r.recusadas[m.outbox_id] = "Erro: recusada"
            else:
                r.aceitas.add(m.outbox_id)
                self.recebidas.append(m.outbox_id)
        return r


def _pendentes(sessao: Session, n: int) -> None:
    for i in range(n):
        enfileirar(sessao, "pagina_coletada", {"pagina_id": i})
    sessao.flush()


def test_publica_e_marca(sessao: Session) -> None:
    _pendentes(sessao, 3)
    fila = FilaFalsa()

    resumo = publicar_outbox(sessao, fila)

    assert (resumo.publicadas, resumo.recusadas) == (3, 0)
    assert len(fila.recebidas) == 3
    assert all(o.publicada_em is not None for o in sessao.scalars(select(Outbox)))


def test_recusada_fica_pendente_com_erro(sessao: Session) -> None:
    _pendentes(sessao, 2)
    primeiro = sessao.scalars(select(Outbox).order_by(Outbox.id)).first()
    assert primeiro is not None
    fila = FilaFalsa(recusar={primeiro.id})

    resumo = publicar_outbox(sessao, fila)

    sessao.refresh(primeiro)
    assert (resumo.publicadas, resumo.recusadas) == (1, 1)
    assert primeiro.tentativas == 1
    assert primeiro.erro == "Erro: recusada"
    assert primeiro.publicada_em is None


def test_lote_inteiro_recusado_encerra(sessao: Session) -> None:
    _pendentes(sessao, 3)
    ids = set(sessao.scalars(select(Outbox.id)))

    resumo = publicar_outbox(sessao, FilaFalsa(recusar=ids), lote=2)

    assert (resumo.publicadas, resumo.recusadas) == (0, 2)


@pytest.fixture
def limpa_outbox(engine: Engine) -> Iterator[None]:
    yield
    with engine.begin() as conexao:
        conexao.execute(delete(Outbox))


@pytest.mark.usefixtures("limpa_outbox")
def test_skip_locked_duas_sessoes(engine: Engine) -> None:
    with Session(engine) as inicial:
        _pendentes(inicial, 3)
        inicial.commit()

    with engine.connect() as con_a, engine.connect() as con_b:
        with Session(con_a) as a, Session(con_b) as b:
            travadas = a.scalars(
                select(Outbox.id)
                .where(Outbox.publicada_em.is_(None))
                .order_by(Outbox.id)
                .limit(2)
                .with_for_update(skip_locked=True)
            ).all()
            assert len(travadas) == 2
            fila = FilaFalsa()

            resumo = publicar_outbox(b, fila)

            assert resumo.publicadas == 1
            assert not set(fila.recebidas) & set(travadas)
            a.rollback()


class FalhaDeCommit(Exception):
    pass


def test_envio_ok_commit_falha_reenvia(sessao: Session) -> None:
    _pendentes(sessao, 3)
    sessao.commit()
    ids = sorted(sessao.scalars(select(Outbox.id)))
    fila = FilaFalsa()
    commit_real = sessao.commit

    def commit_que_falha() -> None:
        sessao.commit = commit_real  # type: ignore[method-assign]
        raise FalhaDeCommit

    sessao.commit = commit_que_falha  # type: ignore[method-assign]
    with pytest.raises(FalhaDeCommit):
        publicar_outbox(sessao, fila)
    sessao.rollback()
    assert sorted(fila.recebidas) == ids

    publicar_outbox(sessao, fila)

    assert sorted(fila.recebidas) == sorted(ids + ids)
    assert all(o.publicada_em is not None for o in sessao.scalars(select(Outbox)))


def test_limpar_outbox_apaga_so_publicadas_antigas(sessao: Session) -> None:
    agora = datetime(2026, 11, 26, tzinfo=UTC)
    antiga = Outbox(tipo="pagina_coletada", carga={}, publicada_em=agora - timedelta(days=8))
    recente = Outbox(tipo="pagina_coletada", carga={}, publicada_em=agora - timedelta(days=2))
    pendente = Outbox(tipo="pagina_coletada", carga={})
    sessao.add_all([antiga, recente, pendente])
    sessao.flush()
    id_antiga = antiga.id

    apagadas = limpar_outbox(sessao, agora)

    assert apagadas == 1
    restantes = set(sessao.scalars(select(Outbox.id)))
    assert id_antiga not in restantes
    assert {recente.id, pendente.id} <= restantes


class FilaQueOmite(FilaFalsa):
    def __init__(self, omitir: int) -> None:
        super().__init__()
        self.omitir = omitir

    def enviar_lote(self, mensagens: list[Mensagem]) -> ResultadoEnvio:
        return super().enviar_lote([m for m in mensagens if m.outbox_id != self.omitir])


def test_id_sem_resposta_conta_como_recusada(sessao: Session) -> None:
    _pendentes(sessao, 3)
    omitida = sessao.scalars(select(Outbox).order_by(Outbox.id)).first()
    assert omitida is not None

    resumo = publicar_outbox(sessao, FilaQueOmite(omitir=omitida.id))

    sessao.refresh(omitida)
    assert (resumo.publicadas, resumo.recusadas) == (2, 1)
    assert omitida.tentativas == 1
    assert omitida.erro == "sem resposta da fila"
    assert omitida.publicada_em is None
