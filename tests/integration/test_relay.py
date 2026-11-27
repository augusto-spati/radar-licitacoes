"""Relay da outbox: publica em lotes, tolera recusa, trava com SKIP LOCKED."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from sqlalchemy import Engine, delete, func, select, text
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


def test_lote_inteiro_recusado_nao_bloqueia_o_resto(sessao: Session) -> None:
    # Recusas permanentes na cabeça da outbox: o relay segue pelo cursor até o fim.
    _pendentes(sessao, 5)
    ids = sorted(sessao.scalars(select(Outbox.id)))
    fila = FilaFalsa(recusar=set(ids[:4]))

    resumo = publicar_outbox(sessao, fila, lote=2)

    assert (resumo.publicadas, resumo.recusadas) == (1, 4)
    assert fila.recebidas == [ids[4]]


def test_cada_linha_tentada_uma_vez_por_chamada(sessao: Session) -> None:
    _pendentes(sessao, 3)
    ids = set(sessao.scalars(select(Outbox.id)))

    resumo = publicar_outbox(sessao, FilaFalsa(recusar=ids), lote=2)

    assert (resumo.publicadas, resumo.recusadas) == (0, 3)
    assert {o.tentativas for o in sessao.scalars(select(Outbox))} == {1}


def test_filtro_por_tipos_nao_toca_os_outros(sessao: Session) -> None:
    _pendentes(sessao, 2)
    enfileirar(sessao, "coletar_janela", {"janela_id": 1, "lote": 0})
    sessao.flush()
    fila = FilaFalsa()

    resumo = publicar_outbox(sessao, fila, tipos={"pagina_coletada"})

    assert (resumo.publicadas, resumo.recusadas) == (2, 0)
    janela = sessao.scalars(select(Outbox).where(Outbox.tipo == "coletar_janela")).one()
    assert (janela.publicada_em, janela.tentativas, janela.erro) == (None, 0, None)


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


def _em_transacao(engine: Engine) -> int:
    with engine.connect() as conexao:
        total: int = conexao.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database() AND pid <> pg_backend_pid()"
                " AND state LIKE 'idle in transaction%'"
            )
        )
        return total


class FilaQueConsome(FilaFalsa):
    """Como a FilaLocal: consome no envio (pode levar horas) e grava continuações."""

    consome_no_envio = True

    def __init__(self, engine: Engine, recusar: set[int] | None = None) -> None:
        super().__init__(recusar)
        self.engine = engine
        self.durante: list[tuple[int, int, int]] = []  # (checkedout, em transação, publicadas)
        self.gerar = 2

    def enviar_lote(self, mensagens: list[Mensagem]) -> ResultadoEnvio:
        with self.engine.connect() as conexao:
            publicadas = conexao.scalar(
                select(func.count()).where(Outbox.publicada_em.is_not(None))
            )
        self.durante.append(
            (self.engine.pool.checkedout(), _em_transacao(self.engine), int(publicadas or 0))  # type: ignore[attr-defined]
        )
        if self.gerar:  # a continuação nasce durante o consumo e sai na mesma chamada
            self.gerar -= 1
            with Session(self.engine) as outra:
                enfileirar(outra, "pagina_coletada", {"pagina_id": 99})
                outra.commit()
        return super().enviar_lote(mensagens)


@pytest.mark.usefixtures("limpa_outbox")
def test_fila_que_consome_nao_segura_transacao(engine: Engine) -> None:
    with Session(engine) as inicial:
        _pendentes(inicial, 3)
        inicial.commit()
        recusada = min(inicial.scalars(select(Outbox.id)))
    fila = FilaQueConsome(engine, recusar={recusada})

    with Session(engine) as sessao:
        resumo = publicar_outbox(sessao, fila, lote=2)

    assert (resumo.publicadas, resumo.recusadas) == (4, 1)
    assert len(fila.durante) == 5  # uma mensagem por envio, cada uma uma vez
    # Durante o consumo: a sessão do relay devolveu a conexão e nenhuma transação ficou aberta.
    assert {(checkedout, abertas) for checkedout, abertas, _ in fila.durante} == {(0, 0)}
    # Cada mensagem é marcada publicada numa transação curta, antes da próxima.
    assert [publicadas for *_, publicadas in fila.durante] == [0, 0, 1, 2, 3]
    with Session(engine) as sessao:
        linha = sessao.get(Outbox, recusada)
        assert linha is not None
        assert (linha.publicada_em, linha.tentativas) == (None, 1)


class FilaQueGuarda(FilaFalsa):
    def __init__(self) -> None:
        super().__init__()
        self.mensagens: list[Mensagem] = []

    def enviar_lote(self, mensagens: list[Mensagem]) -> ResultadoEnvio:
        self.mensagens.extend(mensagens)
        return super().enviar_lote(mensagens)


TP1 = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
TP2 = "00-1af7651916cd43dd8448eb211c80319c-c7ad6b7169203331-00"


def _com_contexto(sessao: Session) -> None:
    for i, tp in enumerate((TP1, TP2, None)):
        linha = enfileirar(sessao, "pagina_coletada", {"pagina_id": i})
        linha.traceparent = tp
    sessao.flush()


def test_relay_copia_traceparent_para_mensagem(sessao: Session) -> None:
    _com_contexto(sessao)
    fila = FilaQueGuarda()
    publicar_outbox(sessao, fila)
    assert [m.traceparent for m in fila.mensagens] == [TP1, TP2, None]


def test_relay_sem_trava_copia_traceparent(sessao: Session) -> None:
    _com_contexto(sessao)

    class Local(FilaQueGuarda):
        consome_no_envio = True

    fila = Local()
    publicar_outbox(sessao, fila)
    assert [m.traceparent for m in fila.mensagens] == [TP1, TP2, None]


def test_relay_lote_tem_links(sessao: Session, spans: InMemorySpanExporter) -> None:
    _com_contexto(sessao)
    publicar_outbox(sessao, FilaQueGuarda())
    (lote,) = [s for s in spans.get_finished_spans() if s.name == "relay.lote"]
    assert lote.kind == SpanKind.PRODUCER
    assert lote.parent is None
    assert len(lote.links) == 2
    assert {f"{link.context.span_id:016x}" for link in lote.links} == {
        "b7ad6b7169203331",
        "c7ad6b7169203331",
    }
    assert lote.attributes is not None
    assert lote.attributes["messaging.system"] == "aws_sqs"
    assert lote.attributes["messaging.batch.message_count"] == 3
    assert lote.attributes["radar.recusadas"] == 0
