"""Fatia de janela: lease, fencing, checkpoint, continuação e completude entre fatias."""

import logging
import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fakes import VAZIA, Chave, ClienteFalso, como_cliente, pagina
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import Session

from radar.coleta import (
    ErroPNCP,
    ResultadoFatia,
    coletar_fatia,
    coletar_pendentes,
    enfileirar_elegiveis,
)
from radar.coleta.modelos import ColetaJanela, PaginaBruta
from radar.coleta.pncp import Pagina
from radar.comum import Outbox
from radar.comum.telemetria import tracer
from telemetria_memoria import EmissorEmMemoria

D1 = date(2026, 11, 1)
AGORA = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def janela(sessao: Session, **campos: object) -> ColetaJanela:
    base: dict[str, object] = {
        "endpoint": "publicacao",
        "data_ini": D1,
        "data_fim": D1,
        "modalidade": 6,
        "status": "pendente",
        "paginas": 0,
        "registros": 0,
    }
    obj = ColetaJanela(**{**base, **campos})
    sessao.add(obj)
    sessao.commit()
    return obj


def chave(n: int, ini: date = D1, fim: date = D1) -> Chave:
    return ("publicacao", ini, fim, 6, n)


class Cronometro:
    """Relógio monotônico falso: cada página pedida ao PNCP leva `passo` segundos."""

    def __init__(self, passo: float) -> None:
        self.t = 0.0
        self.passo = passo

    def __call__(self) -> float:
        return self.t

    def avancar(self, _chave: Chave) -> None:
        self.t += self.passo


def cliente_com_relogio(
    roteiro: dict[Chave, Pagina | BaseException], passo: float = 200.0
) -> tuple[ClienteFalso, Cronometro]:
    cronometro = Cronometro(passo)
    return ClienteFalso(roteiro, ao_buscar=cronometro.avancar), cronometro


def fatia(
    sessao: Session,
    falso: ClienteFalso,
    janela_id: int,
    lote: int | None,
    token: str = "a",
    orcamento_s: float | None = None,
    cronometro: Cronometro | None = None,
) -> ResultadoFatia:
    extra: dict[str, Any] = {"cronometro": cronometro} if cronometro else {}
    return coletar_fatia(
        sessao,
        como_cliente(falso),
        janela_id,
        lote=lote,
        token=token,
        orcamento_s=orcamento_s,
        **extra,
    )


def mensagens(sessao: Session, tipo: str = "coletar_janela") -> list[dict[str, Any]]:
    return list(sessao.scalars(select(Outbox.carga).where(Outbox.tipo == tipo).order_by(Outbox.id)))


def numeros_gravados(sessao: Session, janela_id: int) -> list[int]:
    return list(
        sessao.scalars(
            select(PaginaBruta.numero_pagina)
            .where(PaginaBruta.janela_id == janela_id)
            .order_by(PaginaBruta.numero_pagina)
        )
    )


def recarregar(sessao: Session, j: ColetaJanela) -> ColetaJanela:
    sessao.refresh(j)
    return j


def cinco_paginas() -> dict[Chave, Pagina | BaseException]:
    return {chave(n): pagina(n, 5) for n in range(1, 6)}


def test_orcamento_corta_e_enfileira_continuacao(sessao: Session) -> None:
    j = janela(sessao)
    falso, cronometro = cliente_com_relogio(cinco_paginas())

    r = fatia(sessao, falso, j.id, lote=0, orcamento_s=540, cronometro=cronometro)

    assert (r.desfecho, r.paginas, r.registros) == ("continua", 3, 6)
    assert numeros_gravados(sessao, j.id) == [1, 2, 3]
    recarregar(sessao, j)
    assert (j.status, j.proxima_pagina, j.lote, j.total_paginas, j.tentativas) == (
        "em_andamento",
        4,
        1,
        5,
        1,
    )
    assert (j.travada_por, j.travada_ate) == (None, None)
    assert mensagens(sessao) == [{"janela_id": j.id, "lote": 1}]
    assert len(mensagens(sessao, "pagina_coletada")) == 3
    continuacao = sessao.scalars(select(Outbox.id).where(Outbox.tipo == "coletar_janela")).all()
    assert r.continuacoes == tuple(continuacao)


def test_continuacao_comeca_no_checkpoint(sessao: Session) -> None:
    j = janela(sessao)
    falso, cronometro = cliente_com_relogio(cinco_paginas())
    fatia(sessao, falso, j.id, lote=0, orcamento_s=540, cronometro=cronometro)
    falso.chamadas.clear()

    r = fatia(sessao, falso, j.id, lote=1, token="b", orcamento_s=540, cronometro=Cronometro(200))

    assert falso.chamadas == [chave(4), chave(5)]
    assert (r.desfecho, r.paginas, r.registros) == ("concluida", 2, 4)
    recarregar(sessao, j)
    assert (j.status, j.paginas, j.registros, j.lote, j.tentativas) == ("concluida", 5, 10, 1, 2)
    assert (j.travada_por, j.travada_ate, j.erro) == (None, None, None)
    assert j.concluida_em is not None
    assert mensagens(sessao) == [{"janela_id": j.id, "lote": 1}]  # nenhuma continuação nova


@pytest.fixture
def banco_limpo(engine: Engine) -> Iterator[Engine]:
    """Para testes com sessões e conexões próprias (commits de verdade): limpa no fim."""
    yield engine
    with Session(engine) as limpeza:
        limpeza.execute(delete(Outbox))
        limpeza.execute(delete(PaginaBruta))
        limpeza.execute(delete(ColetaJanela))
        limpeza.commit()


def test_duas_sessoes_mesma_janela_so_uma_ganha(banco_limpo: Engine) -> None:
    with Session(banco_limpo) as a, Session(banco_limpo) as b:
        j = janela(a)
        resultados: list[ResultadoFatia] = []
        intrusa = ClienteFalso({})

        def duplicata(_chave: Chave) -> None:
            # Enquanto A coleta a página 1, a duplicata da mesma mensagem chega em B.
            resultados.append(fatia(b, intrusa, j.id, lote=0, token="b"))

        falso = ClienteFalso({chave(1): pagina(1, 1)}, ao_buscar=duplicata)
        r = fatia(a, falso, j.id, lote=0, token="a")

        assert r.desfecho == "concluida"
        assert [x.desfecho for x in resultados] == ["ocupada"]
        assert intrusa.chamadas == []


def test_duplicata_concorrente_e_ocupada(sessao: Session) -> None:
    viva = datetime.now(UTC) + timedelta(hours=1)
    j = janela(
        sessao,
        status="em_andamento",
        proxima_pagina=3,
        total_paginas=5,
        travada_por="outro",
        travada_ate=viva,
        tentativas=1,
    )
    falso = ClienteFalso({})

    r = fatia(sessao, falso, j.id, lote=0, token="b", orcamento_s=540)

    assert r.desfecho == "ocupada"
    assert falso.chamadas == []
    recarregar(sessao, j)
    assert (j.proxima_pagina, j.travada_por, j.tentativas, j.lote) == (3, "outro", 1, 0)
    assert mensagens(sessao) == []


class LambdaMorta(BaseException):
    """Timeout da Lambda: o processo some sem rodar nenhum tratamento de erro."""


def test_reentrega_retoma_do_checkpoint(sessao: Session) -> None:
    j = janela(sessao)
    roteiro = cinco_paginas()
    roteiro[chave(3)] = LambdaMorta()
    falso = ClienteFalso(roteiro)
    with pytest.raises(LambdaMorta):
        fatia(sessao, falso, j.id, lote=0, token="morta", orcamento_s=540)
    sessao.rollback()
    recarregar(sessao, j)
    assert (j.proxima_pagina, j.travada_por) == (3, "morta")
    # O lease vence antes de a mensagem reaparecer (visibility timeout de 5.400 s).
    sessao.execute(
        update(ColetaJanela)
        .where(ColetaJanela.id == j.id)
        .values(travada_ate=func.now() - timedelta(seconds=1))
    )
    sessao.commit()
    falso.roteiro[chave(3)] = pagina(3, 5)
    falso.chamadas.clear()

    r = fatia(sessao, falso, j.id, lote=0, token="reentrega", orcamento_s=540)

    assert falso.chamadas == [chave(3), chave(4), chave(5)]
    assert r.desfecho == "concluida"
    recarregar(sessao, j)
    assert (j.status, j.paginas, j.registros, j.tentativas) == ("concluida", 5, 10, 2)


def test_lote_diferente_e_obsoleta(sessao: Session) -> None:
    j = janela(sessao, status="em_andamento", lote=3, proxima_pagina=2, total_paginas=2)
    falso = ClienteFalso({})

    assert fatia(sessao, falso, j.id, lote=2).desfecho == "obsoleta"
    assert falso.chamadas == []
    assert recarregar(sessao, j).tentativas == 0


def test_janela_concluida_confirma(sessao: Session) -> None:
    j = janela(sessao, status="concluida", concluida_em=AGORA)
    falso = ClienteFalso({})

    assert fatia(sessao, falso, j.id, lote=0).desfecho == "concluida"
    assert fatia(sessao, falso, j.id, lote=None).desfecho == "concluida"
    assert falso.chamadas == []


def test_janela_inexistente_confirma(sessao: Session) -> None:
    falso = ClienteFalso({})

    assert fatia(sessao, falso, 987_654, lote=0).desfecho == "inexistente"
    assert falso.chamadas == []


def test_fencing_bloqueia_quem_perdeu_o_lease(sessao: Session) -> None:
    j = janela(sessao)

    def intruso(c: Chave) -> None:
        if c == chave(2):  # o lease da primeira venceu e outra invocação assumiu
            sessao.execute(
                update(ColetaJanela).where(ColetaJanela.id == j.id).values(travada_por="intruso")
            )
            sessao.commit()

    falso = ClienteFalso(cinco_paginas(), ao_buscar=intruso)

    r = fatia(sessao, falso, j.id, lote=0, token="a", orcamento_s=540)

    assert r.desfecho == "lease_perdida"
    assert falso.chamadas == [chave(1), chave(2)]
    assert numeros_gravados(sessao, j.id) == [1]
    recarregar(sessao, j)
    assert (j.proxima_pagina, j.travada_por, j.status) == (2, "intruso", "em_andamento")
    assert len(mensagens(sessao, "pagina_coletada")) == 1
    assert mensagens(sessao) == []


def _pagina_com(numero: int, total: int, ids: list[str], total_registros: int) -> Pagina:
    registros = [{"numeroControlePNCP": i} for i in ids]
    bruto = {
        "data": registros,
        "totalPaginas": total,
        "numeroPagina": numero,
        "totalRegistros": total_registros,
    }
    return Pagina(registros=registros, total_paginas=total, numero_pagina=numero, bruto=bruto)


def _tres_fatias(sessao: Session, total_registros: int) -> tuple[ColetaJanela, list[str]]:
    j = janela(sessao)
    roteiro: dict[Chave, Pagina | BaseException] = {
        chave(1): _pagina_com(1, 3, ["A", "B"], total_registros),
        chave(2): _pagina_com(2, 3, ["B", "C"], total_registros),  # B repetido entre páginas
        chave(3): _pagina_com(3, 3, ["C"], total_registros),
    }
    falso = ClienteFalso(roteiro)
    desfechos = []
    for lote in range(3):  # cada página leva 600 s: uma página por fatia
        cronometro = Cronometro(600)
        falso.ao_buscar = cronometro.avancar
        r = fatia(sessao, falso, j.id, lote, f"t{lote}", orcamento_s=540, cronometro=cronometro)
        desfechos.append(r.desfecho)
    return recarregar(sessao, j), desfechos


def test_completude_conta_distintos_entre_fatias(sessao: Session) -> None:
    j, desfechos = _tres_fatias(sessao, total_registros=4)

    assert desfechos == ["continua", "continua", "falha"]
    assert (j.status, j.erro) == ("falha", "incompleta: 3 de 4 registros")
    assert (j.proxima_pagina, j.total_paginas, j.esperados) == (1, None, None)
    assert (j.travada_por, j.travada_ate) == (None, None)


def test_completude_entre_fatias_conclui_quando_distintos_alcancam(sessao: Session) -> None:
    j, desfechos = _tres_fatias(sessao, total_registros=3)

    assert desfechos == ["continua", "continua", "concluida"]
    assert (j.status, j.paginas, j.registros, j.lote) == ("concluida", 3, 5, 2)


def test_total_paginas_muda_entre_fatias_falha_e_zera(sessao: Session) -> None:
    j = janela(sessao)
    roteiro = cinco_paginas()
    roteiro[chave(4)] = pagina(4, 6)
    falso, cronometro = cliente_com_relogio(roteiro)
    assert fatia(sessao, falso, j.id, lote=0, orcamento_s=540, cronometro=cronometro).desfecho == (
        "continua"
    )

    r = fatia(sessao, falso, j.id, lote=1, token="b", orcamento_s=540, cronometro=Cronometro(200))

    assert r.desfecho == "falha"
    recarregar(sessao, j)
    assert j.status == "falha"
    assert j.erro == "pagina 4 inconsistente: total_paginas=6, esperado 5"
    assert (j.proxima_pagina, j.total_paginas, j.esperados, j.travada_por) == (1, None, None, None)


def test_erro_pncp_mantem_checkpoint(sessao: Session) -> None:
    j = janela(sessao)
    roteiro = cinco_paginas()
    roteiro[chave(3)] = ErroPNCP("HTTP 500", 500)
    falso = ClienteFalso(roteiro)

    r = fatia(sessao, falso, j.id, lote=0, orcamento_s=540)

    assert (r.desfecho, r.erro, r.paginas) == ("falha", "HTTP 500", 2)
    recarregar(sessao, j)
    assert (j.status, j.erro, j.proxima_pagina, j.total_paginas) == ("falha", "HTTP 500", 3, 5)
    assert (j.travada_por, j.travada_ate, j.lote) == (None, None, 0)
    assert mensagens(sessao) == []  # quem tenta de novo é a própria mensagem, que volta

    falso.roteiro[chave(3)] = pagina(3, 5)
    falso.chamadas.clear()
    assert fatia(sessao, falso, j.id, lote=0, orcamento_s=540).desfecho == "concluida"
    assert falso.chamadas == [chave(3), chave(4), chave(5)]


def test_excecao_inesperada_marca_falha_solta_lease_e_propaga(sessao: Session) -> None:
    j = janela(sessao)
    falso = ClienteFalso({chave(1): RuntimeError("boom")})

    with pytest.raises(RuntimeError):
        fatia(sessao, falso, j.id, lote=0, orcamento_s=540)

    recarregar(sessao, j)
    assert (j.status, j.travada_por, j.travada_ate) == ("falha", None, None)
    assert "RuntimeError('boom')" in (j.erro or "")


def test_divisao_no_modo_mensagem_enfileira_metades(sessao: Session) -> None:
    fim = date(2026, 11, 4)
    j = janela(sessao, data_fim=fim)
    falso = ClienteFalso({chave(1, D1, fim): ErroPNCP("HTTP 500", 500)})

    r = fatia(sessao, falso, j.id, lote=0, orcamento_s=540)

    assert r.desfecho == "dividida"
    metades = sessao.execute(
        select(
            ColetaJanela.id, ColetaJanela.data_ini, ColetaJanela.data_fim, ColetaJanela.lote
        ).order_by(ColetaJanela.data_ini)
    ).all()
    assert [(ini.day, fim_.day, lote) for _, ini, fim_, lote in metades] == [(1, 2, 0), (3, 4, 0)]
    assert mensagens(sessao) == [{"janela_id": m.id, "lote": 0} for m in metades]
    ids = sessao.scalars(
        select(Outbox.id).where(Outbox.tipo == "coletar_janela").order_by(Outbox.id)
    ).all()
    assert r.continuacoes == tuple(ids)


def test_divisao_no_laco_nao_enfileira(sessao: Session) -> None:
    fim = date(2026, 11, 4)
    j = janela(sessao, data_fim=fim)
    falso = ClienteFalso({chave(1, D1, fim): ErroPNCP("HTTP 500", 500)})

    r = fatia(sessao, falso, j.id, lote=None)
    assert (r.desfecho, r.continuacoes) == ("dividida", ())
    assert mensagens(sessao) == []


def test_dia_sem_registros_conclui(sessao: Session) -> None:
    j = janela(sessao)
    falso = ClienteFalso({chave(1): VAZIA})

    r = fatia(sessao, falso, j.id, lote=0, orcamento_s=540)

    assert (r.desfecho, r.paginas) == ("concluida", 0)
    recarregar(sessao, j)
    assert (j.status, j.paginas, j.registros, j.total_paginas) == ("concluida", 0, 0, 0)
    assert numeros_gravados(sessao, j.id) == []


def test_laco_sem_lote_nao_aceita_orcamento(sessao: Session) -> None:
    j = janela(sessao)
    with pytest.raises(ValueError):
        fatia(sessao, ClienteFalso({}), j.id, lote=None, orcamento_s=540)


def test_dois_coletar_pendentes_nao_repetem_janela(banco_limpo: Engine) -> None:
    dias = [D1 + timedelta(days=n) for n in range(4)]
    with Session(banco_limpo) as preparo:
        for dia in dias:
            janela(preparo, data_ini=dia, data_fim=dia)
    roteiro: dict[Chave, Pagina | BaseException] = {chave(1, d, d): pagina(1, 1) for d in dias}
    barreira = threading.Barrier(2)
    erros: list[BaseException] = []

    def devagar(_chave: Chave) -> None:
        threading.Event().wait(0.05)  # dá tempo de o outro processo tentar a mesma janela

    falso = ClienteFalso(roteiro, ao_buscar=devagar)

    def processo() -> None:
        try:
            with Session(banco_limpo) as sessao:
                barreira.wait()
                coletar_pendentes(sessao, como_cliente(falso), AGORA)
        except BaseException as erro:
            erros.append(erro)

    threads = [threading.Thread(target=processo) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert erros == []
    assert sorted(falso.chamadas) == sorted(roteiro)
    with Session(banco_limpo) as leitura:
        assert set(leitura.scalars(select(ColetaJanela.status))) == {"concluida"}


def test_janela_longa_com_uma_falha_nao_e_abandonada(sessao: Session) -> None:
    j = janela(sessao)
    roteiro: dict[Chave, Pagina | BaseException] = {chave(n): pagina(n, 6) for n in range(1, 7)}
    roteiro[chave(6)] = ErroPNCP("HTTP 500", 500)
    falso, cronometro = cliente_com_relogio(roteiro)
    lote = 0
    for _ in range(5):  # uma página por fatia
        r = fatia(sessao, falso, j.id, lote=lote, orcamento_s=100, cronometro=cronometro)
        assert r.desfecho == "continua"
        lote += 1

    assert fatia(sessao, falso, j.id, lote=lote, orcamento_s=100).desfecho == "falha"

    recarregar(sessao, j)
    assert (j.status, j.tentativas, j.falhas) == ("falha", 6, 1)
    assert enfileirar_elegiveis(sessao, "publicacao") == 1  # não desiste
    falso.roteiro[chave(6)] = pagina(6, 6)
    assert fatia(sessao, falso, j.id, lote=lote, orcamento_s=100).desfecho == "concluida"
    recarregar(sessao, j)
    assert (j.status, j.tentativas, j.falhas) == ("concluida", 7, 0)


def test_cinco_falhas_seguidas_desiste(sessao: Session, caplog: pytest.LogCaptureFixture) -> None:
    j = janela(sessao)
    falso = ClienteFalso({chave(1): ErroPNCP("HTTP 500", 500)})
    for n in range(1, 6):
        assert fatia(sessao, falso, j.id, lote=0, orcamento_s=540).desfecho == "falha"
        recarregar(sessao, j)
        assert j.falhas == n

    with caplog.at_level(logging.INFO, logger="radar.coleta.janelas"):
        assert enfileirar_elegiveis(sessao, "publicacao") == 0

    assert [r.getMessage() for r in caplog.records if r.name == "radar.coleta.janelas"] == [
        "janela_desistida"
    ]


def test_excecao_inesperada_conta_falha(sessao: Session) -> None:
    j = janela(sessao)
    falso = ClienteFalso({chave(1): RuntimeError("boom")})

    with pytest.raises(RuntimeError):
        fatia(sessao, falso, j.id, lote=0, orcamento_s=540)

    recarregar(sessao, j)
    assert (j.status, j.falhas) == ("falha", 1)


# ---------- Telemetria: coleta.janela é raiz e as páginas herdam o contexto ----------

TP_MENSAGEM = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def _span(spans: InMemorySpanExporter, nome: str) -> ReadableSpan:
    (achado,) = [s for s in spans.get_finished_spans() if s.name == nome]
    return achado


def test_janela_e_raiz_e_pagina_herda(sessao: Session, spans: InMemorySpanExporter) -> None:
    j = janela(sessao)
    falso = ClienteFalso({chave(1): pagina(1, 2), chave(2): pagina(2, 2)})
    with tracer().start_as_current_span("de-fora"):  # a janela não é filha de quem chama
        r = fatia(sessao, falso, j.id, lote=None)
    assert r.desfecho == "concluida"
    span = _span(spans, "coleta.janela")
    assert span.parent is None
    assert span.attributes is not None
    assert span.attributes["radar.janela.id"] == j.id
    assert span.attributes["radar.janela.endpoint"] == "publicacao"
    assert span.attributes["radar.janela.modalidade"] == 6
    assert span.attributes["radar.janela.data_ini"] == "2026-11-01"
    assert span.attributes["radar.janela.pagina_inicial"] == 1
    assert span.attributes["radar.janela.paginas"] == 2
    assert span.attributes["radar.janela.registros"] == 4
    assert span.attributes["radar.desfecho"] == "concluida"
    contextos = sessao.scalars(
        select(Outbox.traceparent).where(Outbox.tipo == "pagina_coletada")
    ).all()
    assert len(contextos) == 2
    trace_id = f"{span.context.trace_id:032x}"
    assert all(tp is not None and tp.split("-")[1] == trace_id for tp in contextos)


def test_continuacao_liga_a_mensagem_e_marca_pagina_inicial(
    sessao: Session, spans: InMemorySpanExporter
) -> None:
    j = janela(sessao)
    falso, cronometro = cliente_com_relogio(cinco_paginas())
    fatia(sessao, falso, j.id, lote=0, orcamento_s=540, cronometro=cronometro)
    continuacao = sessao.scalars(
        select(Outbox.traceparent).where(Outbox.tipo == "coletar_janela")
    ).one()
    primeira = _span(spans, "coleta.janela")
    assert continuacao is not None and continuacao.split("-")[2] == (
        f"{primeira.context.span_id:016x}"
    )
    spans.clear()
    coletar_fatia(
        sessao,
        como_cliente(falso),
        j.id,
        lote=1,
        token="b",
        orcamento_s=10_000,
        cronometro=cronometro,
        traceparent=continuacao,
    )
    segunda = _span(spans, "coleta.janela")
    assert segunda.parent is None
    assert [link.context.span_id for link in segunda.links] == [primeira.context.span_id]
    assert segunda.attributes is not None
    assert segunda.attributes["radar.janela.pagina_inicial"] == 4
    assert segunda.attributes["radar.janela.lote"] == 1


def test_janela_com_falha_marca_erro(sessao: Session, spans: InMemorySpanExporter) -> None:
    j = janela(sessao)
    falso = ClienteFalso({chave(1): ErroPNCP("HTTP 503", 503)})
    r = fatia(sessao, falso, j.id, lote=None)
    assert r.desfecho == "falha"
    span = _span(spans, "coleta.janela")
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes is not None and span.attributes["radar.desfecho"] == "falha"


def test_janela_ocupada_nao_e_erro(sessao: Session, spans: InMemorySpanExporter) -> None:
    j = janela(sessao, status="concluida")
    r = fatia(sessao, ClienteFalso({}), j.id, lote=None)
    assert r.desfecho == "concluida"
    span = _span(spans, "coleta.janela")
    assert span.status.status_code != StatusCode.ERROR


def test_metricas_descarregadas_por_janela(sessao: Session, metricas: EmissorEmMemoria) -> None:
    for dia in (D1, D1 + timedelta(days=1)):
        janela(sessao, data_ini=dia, data_fim=dia)
    roteiro = {chave(1, dia, dia): pagina(1, 1) for dia in (D1, D1 + timedelta(days=1))}
    resumo = coletar_pendentes(sessao, como_cliente(ClienteFalso(roteiro)), AGORA)
    assert resumo.concluidas == 2
    assert metricas.descarregamentos == 2
