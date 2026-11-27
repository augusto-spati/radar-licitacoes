"""Trava da fonte por lease em tabela: funciona através do pooler e sem transação aberta."""

import uuid
from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from radar.coleta import trava_fonte
from radar.coleta.trava import obter_trava


@pytest.fixture(autouse=True)
def _sem_leases(engine: Engine) -> Iterator[None]:
    # A trava commita na própria conexão: o rollback da sessão de teste não a desfaz.
    yield
    with engine.begin() as conexao:
        conexao.execute(text("DELETE FROM trava_fonte"))


def _dono(engine: Engine, fonte: str) -> uuid.UUID | None:
    with engine.connect() as conexao:
        dono: uuid.UUID | None = conexao.scalar(
            text("SELECT dono FROM trava_fonte WHERE fonte = :f"), {"f": fonte}
        )
        return dono


def _expirar(engine: Engine, fonte: str) -> None:
    with engine.begin() as conexao:
        conexao.execute(
            text("UPDATE trava_fonte SET expira_em = now() - interval '1 minute' WHERE fonte = :f"),
            {"f": fonte},
        )


def test_obtida_e_liberada(sessao: Session, engine: Engine) -> None:
    with trava_fonte(sessao, "sanepar") as obtida:
        assert obtida
        assert _dono(engine, "sanepar") is not None
    assert _dono(engine, "sanepar") is None


def test_segunda_tentativa_enquanto_viva_falha(sessao: Session, engine: Engine) -> None:
    with trava_fonte(sessao, "sanepar") as primeira:
        dono = _dono(engine, "sanepar")
        with trava_fonte(sessao, "sanepar") as segunda:
            assert primeira and not segunda
        assert _dono(engine, "sanepar") == dono  # quem não obteve não libera


def test_fontes_diferentes_nao_se_bloqueiam(sessao: Session) -> None:
    with trava_fonte(sessao, "sanepar") as a, trava_fonte(sessao, "compesa") as b:
        assert a and b


def test_lease_expirado_e_tomado_por_outro(sessao: Session, engine: Engine) -> None:
    dono_antigo = uuid.uuid4()
    with engine.begin() as conexao:
        conexao.execute(
            text(
                "INSERT INTO trava_fonte (fonte, dono, expira_em, obtida_em)"
                " VALUES ('sanepar', :d, now() - interval '1 minute',"
                " now() - interval '31 minutes')"
            ),
            {"d": dono_antigo},
        )

    with trava_fonte(sessao, "sanepar") as obtida:
        assert obtida
        assert _dono(engine, "sanepar") not in (None, dono_antigo)


def test_liberacao_do_dono_antigo_nao_apaga_o_novo(sessao: Session, engine: Engine) -> None:
    antiga = trava_fonte(sessao, "sanepar")
    assert antiga.__enter__()
    _expirar(engine, "sanepar")  # o primeiro passou do prazo; outro processo toma o lease
    nova = trava_fonte(sessao, "sanepar")
    assert nova.__enter__()
    dono_novo = _dono(engine, "sanepar")

    antiga.__exit__(None, None, None)
    assert _dono(engine, "sanepar") == dono_novo

    nova.__exit__(None, None, None)
    assert _dono(engine, "sanepar") is None


def test_duracao_define_o_prazo(sessao: Session, engine: Engine) -> None:
    with trava_fonte(sessao, "sanepar", duracao=timedelta(minutes=5)):
        with engine.connect() as conexao:
            prazo = conexao.scalar(
                text("SELECT expira_em - obtida_em FROM trava_fonte WHERE fonte = 'sanepar'")
            )
    assert prazo == timedelta(minutes=5)


def test_renovar_estende_so_para_o_dono(sessao: Session, engine: Engine) -> None:
    dono = uuid.uuid4()
    assert obter_trava(sessao, "pncp", dono, timedelta(minutes=1))
    assert obter_trava(sessao, "pncp", dono, timedelta(hours=1))  # renovação
    assert not obter_trava(sessao, "pncp", uuid.uuid4(), timedelta(hours=1))
    with engine.connect() as conexao:
        restante = conexao.scalar(
            text("SELECT expira_em - now() FROM trava_fonte WHERE fonte = 'pncp'")
        )
    assert restante > timedelta(minutes=50)


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


def test_nenhuma_transacao_fica_aberta_durante_a_trava(sessao: Session, engine: Engine) -> None:
    sessao.rollback()
    antes = _em_transacao(engine)  # a conexão da fixture de teste já está em transação
    em_uso = engine.pool.checkedout()  # type: ignore[attr-defined]
    with trava_fonte(sessao, "sanepar") as obtida:
        assert obtida
        assert _em_transacao(engine) == antes
        assert engine.pool.checkedout() == em_uso  # type: ignore[attr-defined]
