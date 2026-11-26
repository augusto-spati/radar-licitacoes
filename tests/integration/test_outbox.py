"""Outbox transacional: a mensagem nasce na mesma transação da coleta."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from radar.comum.outbox import Outbox, enfileirar


def test_enfileirar_sem_commit_some_no_rollback(sessao: Session) -> None:
    enfileirar(sessao, "pagina_coletada", {"pagina_id": 7})

    sessao.rollback()

    assert sessao.scalar(select(func.count()).select_from(Outbox)) == 0


def test_enfileirar_grava_tipo_e_carga(sessao: Session) -> None:
    enfileirar(sessao, "pagina_coletada", {"pagina_id": 7})
    sessao.flush()

    linha = sessao.scalars(select(Outbox)).one()

    assert linha.tipo == "pagina_coletada"
    assert linha.carga == {"pagina_id": 7}
    assert linha.publicada_em is None
    assert linha.tentativas == 0
    assert linha.erro is None
    assert linha.criada_em is not None


def test_tipo_invalido_viola_check(sessao: Session) -> None:
    sessao.add(Outbox(tipo="x", carga={}))

    with pytest.raises(IntegrityError):
        sessao.flush()
