"""Outbox transacional: a mensagem nasce na mesma transação da coleta."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine, func, select, text
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


RAIZ = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def banco_migracao(engine: Engine) -> Iterator[tuple[AlembicConfig, Engine]]:
    """Banco vazio no mesmo servidor do teste, para subir e descer migrações à mão."""
    nome = f"migracao_{uuid.uuid4().hex[:8]}"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conexao:
        conexao.execute(text(f"CREATE DATABASE {nome}"))
    url = engine.url.set(database=nome)
    config = AlembicConfig(str(RAIZ / "alembic.ini"))
    config.set_main_option("script_location", str(RAIZ / "migrations"))
    config.attributes["url"] = url.render_as_string(hide_password=False)
    alvo = create_engine(url)
    try:
        yield config, alvo
    finally:
        alvo.dispose()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conexao:
            conexao.execute(text(f"DROP DATABASE {nome}"))


def test_migracao_enfileira_paginas_pncp_pendentes(
    banco_migracao: tuple[AlembicConfig, Engine],
) -> None:
    # Sem isto, as páginas que o fluxo síncrono deixou pendentes ficariam órfãs: o laço
    # `ler_paginas_nao_processadas` saiu da cli e a auditoria só olha o que tem outbox.
    config, alvo = banco_migracao
    command.upgrade(config, "head")
    command.downgrade(config, "0004")
    with alvo.begin() as conexao:
        janela = conexao.scalar(
            text(
                "INSERT INTO coleta_janela (endpoint, data_ini, data_fim, modalidade, status,"
                " paginas, registros) VALUES ('publicacao', '2026-11-25', '2026-11-25', 6,"
                " 'concluida', 3, 3) RETURNING id"
            )
        )
        retrato = conexao.scalar(
            text(
                "INSERT INTO coleta_retrato (fonte, status, iniciado_em)"
                " VALUES ('sanepar', 'concluido', now()) RETURNING id"
            )
        )
        paginas = {}
        for numero, processada in ((1, "now()"), (2, "NULL"), (3, "NULL")):
            paginas[numero] = conexao.scalar(
                text(
                    "INSERT INTO pagina_bruta (janela_id, numero_pagina, conteudo, coletada_em,"
                    f" processada_em) VALUES (:janela, :numero, '{{}}', now(), {processada})"
                    " RETURNING id"
                ),
                {"janela": janela, "numero": numero},
            )
        conexao.execute(
            text(
                "INSERT INTO pagina_bruta (retrato_id, numero_pagina, formato, conteudo_texto,"
                " coletada_em) VALUES (:retrato, 1, 'html', '<a/>', now())"
            ),
            {"retrato": retrato},
        )

    command.upgrade(config, "head")

    with alvo.connect() as conexao:
        linhas = conexao.execute(
            text("SELECT tipo, carga, publicada_em FROM outbox ORDER BY id")
        ).all()
    assert [tuple(linha) for linha in linhas] == [
        ("pagina_coletada", {"pagina_id": paginas[2]}, None),
        ("pagina_coletada", {"pagina_id": paginas[3]}, None),
    ]
