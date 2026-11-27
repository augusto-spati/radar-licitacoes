"""Outbox transacional: a mensagem nasce na mesma transação da coleta."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanContext
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from radar.comum.outbox import Outbox, enfileirar
from radar.comum.telemetria import tracer


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


def test_coletar_janela_aceito_pelo_check(sessao: Session) -> None:
    enfileirar(sessao, "coletar_janela", {"janela_id": 1, "lote": 0})
    sessao.flush()

    assert sessao.scalars(select(Outbox.tipo)).one() == "coletar_janela"


def test_migracao_devolve_em_andamento_a_pendente(
    banco_migracao: tuple[AlembicConfig, Engine],
) -> None:
    # Na 4a, uma janela em_andamento só podia ser sobra de execução interrompida.
    config, alvo = banco_migracao
    command.upgrade(config, "0006")
    with alvo.begin() as conexao:
        for dia, status in (("2026-11-24", "em_andamento"), ("2026-11-25", "concluida")):
            conexao.execute(
                text(
                    "INSERT INTO coleta_janela (endpoint, data_ini, data_fim, modalidade, status,"
                    " paginas, registros) VALUES ('atualizacao', :dia, :dia, 6, :status, 0, 0)"
                ),
                {"dia": dia, "status": status},
            )

    command.upgrade(config, "head")

    with alvo.connect() as conexao:
        linhas = conexao.execute(
            text(
                "SELECT status, proxima_pagina, lote, total_paginas, esperados, travada_por,"
                " travada_ate FROM coleta_janela ORDER BY data_ini"
            )
        ).all()
    assert [tuple(linha) for linha in linhas] == [
        ("pendente", 1, 0, None, None, None, None),
        ("concluida", 1, 0, None, None, None, None),
    ]


def test_downgrade_da_0007_apaga_coletar_janela(
    banco_migracao: tuple[AlembicConfig, Engine],
) -> None:
    config, alvo = banco_migracao
    command.upgrade(config, "head")
    with alvo.begin() as conexao:
        conexao.execute(
            text(
                "INSERT INTO outbox (tipo, carga) VALUES"
                " ('coletar_janela', '{\"janela_id\": 1, \"lote\": 0}'),"
                " ('pagina_coletada', '{\"pagina_id\": 1}')"
            )
        )

    command.downgrade(config, "0006")

    with alvo.connect() as conexao:
        tipos = conexao.scalars(text("SELECT tipo FROM outbox")).all()
    assert tipos == ["pagina_coletada"]


def test_enfileirar_grava_traceparent_do_span(sessao: Session, spans: InMemorySpanExporter) -> None:
    with tracer().start_as_current_span("x") as span:
        linha = enfileirar(sessao, "pagina_coletada", {"pagina_id": 7})
    sessao.flush()
    gravado = sessao.scalar(select(Outbox.traceparent).where(Outbox.id == linha.id))
    assert gravado == traceparent_de(span.get_span_context())


def test_enfileirar_sem_span_grava_nulo(sessao: Session, spans: InMemorySpanExporter) -> None:
    linha = enfileirar(sessao, "pagina_coletada", {"pagina_id": 7})
    sessao.flush()
    assert sessao.scalar(select(Outbox.traceparent).where(Outbox.id == linha.id)) is None


def traceparent_de(contexto: SpanContext) -> str:
    return f"00-{contexto.trace_id:032x}-{contexto.span_id:016x}-01"


def test_migracao_0010_cria_e_remove_tabelas_do_historico(
    banco_migracao: tuple[AlembicConfig, Engine],
) -> None:
    config, alvo = banco_migracao
    tabelas = ("historico_carga", "resultado_item", "resultado_busca")
    indices = ("ix_edital_orgao_cnpj", "ix_resultado_busca_situacao")

    def existentes() -> set[str]:
        with alvo.connect() as conexao:
            nomes = conexao.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
            ).all()
            idx = conexao.scalars(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()")
            ).all()
        return {n for n in nomes if n in tabelas} | {i for i in idx if i in indices}

    command.upgrade(config, "head")
    assert existentes() == {*tabelas, *indices}

    command.downgrade(config, "0009")
    assert existentes() == set()


def test_migracao_0011_cria_e_remove_preco_item(
    banco_migracao: tuple[AlembicConfig, Engine],
) -> None:
    config, alvo = banco_migracao

    def existentes() -> set[str]:
        with alvo.connect() as conexao:
            nomes = conexao.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
            ).all()
            idx = conexao.scalars(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()")
            ).all()
        return {n for n in nomes if n == "preco_item"} | {
            i for i in idx if i == "ix_preco_item_servico_uf"
        }

    command.upgrade(config, "head")
    assert existentes() == {"preco_item", "ix_preco_item_servico_uf"}
    with alvo.begin() as conexao:
        conexao.execute(
            text(
                "INSERT INTO preco_item (origem, chave_origem, uf, orgao_nome, processo, descricao,"
                " unidade, servico, unidade_canonica, fator_unidade)"
                " VALUES ('tce_pe', 'k', 'PE', 'Compesa', '1/2026', 'x', 'm', 'rede_agua', 'm', 1)"
            )
        )
        linha = conexao.execute(
            text("SELECT bdi_desconhecido, suspeito, gravado_em FROM preco_item")
        ).one()
    assert linha.bdi_desconhecido is False
    assert linha.suspeito is False
    assert linha.gravado_em is not None

    command.downgrade(config, "0010")
    assert existentes() == set()
    with alvo.connect() as conexao:
        assert conexao.scalar(text("SELECT to_regclass('resultado_item')")) is not None
