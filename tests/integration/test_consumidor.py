"""Consumidor idempotente: página do PNCP e retrato inteiro, com as defesas do spec."""

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.orm import Session

from radar.coleta import FonteRetrato, PaginaColetada, trava_fonte
from radar.coleta.modelos import ColetaJanela, ColetaRetrato, PaginaBruta
from radar.coleta.sanepar import FonteSanepar
from radar.comum import Base, Mensagem
from radar.editais import reconciliar_retrato, registrar_normalizados
from radar.editais.modelos import Edital
from radar.processador import (
    MensagemAdiada,
    fontes_retrato,
    processar_com_span,
    processar_mensagem,
)

FUSO = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 11, 26, 12, 0, tzinfo=FUSO)
DADOS = Path(__file__).resolve().parent.parent / "dados"
BASE = "https://www.sanepar.com.br"


# --- montagem -------------------------------------------------------------------------


def _registro_pncp() -> dict[str, Any]:
    dados: dict[str, Any] = json.loads((DADOS / "registro_pregao.json").read_text(encoding="utf-8"))
    return dados


def _pagina_pncp(sessao: Session) -> int:
    janela = ColetaJanela(
        endpoint="publicacao", data_ini=AGORA.date(), data_fim=AGORA.date(), modalidade=6,
        status="concluida", paginas=1, registros=1,
    )  # fmt: skip
    sessao.add(janela)
    sessao.flush()
    pagina = PaginaBruta(
        janela_id=janela.id,
        numero_pagina=1,
        formato="json",
        conteudo={"data": [_registro_pncp()]},
        coletada_em=AGORA,
    )
    sessao.add(pagina)
    sessao.commit()
    return pagina.id


def _lista(nome: str) -> str:
    return (DADOS / "fontes" / nome).read_text(encoding="utf-8")


def _paginas_sanepar(lista: str) -> list[PaginaColetada]:
    detalhe = _lista("detalhe-345-26-vazamentos.html")
    return [
        PaginaColetada(f"{BASE}/licitacoes?Tipo=OE&Situacao=Abertura&page=0", "html", lista),
        PaginaColetada(f"{BASE}/licitacao/detalhe/352/26", "html", detalhe),
    ]


def _retrato(
    sessao: Session,
    paginas: list[PaginaColetada],
    iniciado_em: datetime = AGORA,
    status: str = "concluido",
) -> int:
    retrato = ColetaRetrato(
        fonte="sanepar",
        status=status,
        iniciado_em=iniciado_em,
        concluido_em=iniciado_em,
        paginas=len(paginas),
        registros=0,
    )
    sessao.add(retrato)
    sessao.flush()
    for numero, pagina in enumerate(paginas, start=1):
        sessao.add(
            PaginaBruta(
                retrato_id=retrato.id,
                numero_pagina=numero,
                formato=pagina.formato,
                conteudo_texto=pagina.texto,
                url=pagina.url,
                coletada_em=iniciado_em,
            )
        )
    sessao.commit()
    return retrato.id


def _msg_retrato(retrato_id: int) -> Mensagem:
    return Mensagem(outbox_id=retrato_id, tipo="retrato_coletado", carga={"retrato_id": retrato_id})


def _msg_pagina(pagina_id: int) -> Mensagem:
    return Mensagem(outbox_id=pagina_id, tipo="pagina_coletada", carga={"pagina_id": pagina_id})


def _editais(sessao: Session, fonte: str) -> dict[str, tuple[str, str | None]]:
    linhas = sessao.execute(
        select(Edital.id_na_fonte, Edital.situacao_normalizada, Edital.situacao_original).where(
            Edital.fonte == fonte
        )
    ).all()
    return {i: (s, o) for i, s, o in linhas}


def _ja_abertos(sessao: Session, *ids: str) -> None:
    normalizados = FonteSanepar().extrair(_paginas_sanepar(_lista("lista-oe-abertura-p1.html")))
    modelo = normalizados[0]
    registrar_normalizados(
        sessao,
        [modelo | {"id_na_fonte": i, "bruto": {"id": i}} for i in ids],
    )
    sessao.commit()


def _estado(sessao: Session, retrato_id: int) -> ColetaRetrato:
    sessao.expire_all()
    return sessao.get_one(ColetaRetrato, retrato_id)


FONTES = fontes_retrato()


# --- pagina_coletada ------------------------------------------------------------------


def test_pagina_processa_e_repetida_e_ok(sessao: Session) -> None:
    pagina_id = _pagina_pncp(sessao)

    assert processar_mensagem(sessao, _msg_pagina(pagina_id), FONTES, AGORA) == "ok"
    antes = sessao.execute(
        select(Edital.id_na_fonte, Edital.hash_conteudo, Edital.data_atualizacao)
    ).all()
    assert len(antes) == 1
    assert sessao.get_one(PaginaBruta, pagina_id).processada_em is not None

    assert processar_mensagem(sessao, _msg_pagina(pagina_id), FONTES, AGORA) == "ok"
    sessao.expire_all()
    depois = sessao.execute(
        select(Edital.id_na_fonte, Edital.hash_conteudo, Edital.data_atualizacao)
    ).all()
    assert depois == antes


def test_pagina_inexistente_e_ok(sessao: Session, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        assert processar_mensagem(sessao, _msg_pagina(999_999), FONTES, AGORA) == "ok"
    assert "pagina_inexistente" in [r.getMessage() for r in caplog.records]


# --- retrato_coletado -----------------------------------------------------------------


def test_retrato_grava_e_reconcilia(sessao: Session, caplog: pytest.LogCaptureFixture) -> None:
    _ja_abertos(sessao, "OE-1/26", "OE-9/26")
    paginas = _paginas_sanepar(_lista("lista-oe-abertura-p1.html"))
    retrato_id = _retrato(sessao, paginas)

    # O fluxo síncrono antigo (cli._rodar_retrato) para as mesmas páginas, desfeito depois.
    fonte = FonteSanepar()
    with sessao.begin_nested() as antigo:
        normalizados = fonte.extrair(paginas)
        esperado = registrar_normalizados(sessao, normalizados)
        vistos = {str(n["id_na_fonte"]) for n in normalizados}
        esperado_encerrados = reconciliar_retrato(
            sessao, "sanepar", vistos, AGORA, rotulo=fonte.rotulo_saida
        )
        esperado_editais = _editais(sessao, "sanepar")
        antigo.rollback()

    with caplog.at_level(logging.INFO):
        assert processar_mensagem(sessao, _msg_retrato(retrato_id), FONTES, AGORA) == "ok"

    registro = next(r for r in caplog.records if r.getMessage() == "retrato_registrado")
    assert (registro.inseridos, registro.encerrados) == (esperado.inseridos, esperado_encerrados)
    assert (esperado.inseridos, esperado_encerrados) == (20, 2)
    assert _editais(sessao, "sanepar") == esperado_editais
    estado = _estado(sessao, retrato_id)
    assert estado.processado_em == AGORA
    nao_marcadas = sessao.scalar(
        select(func.count()).where(
            PaginaBruta.retrato_id == retrato_id, PaginaBruta.processada_em.is_(None)
        )
    )
    assert nao_marcadas == 0


def test_retrato_repetido_e_ok(sessao: Session) -> None:
    retrato_id = _retrato(sessao, _paginas_sanepar(_lista("lista-oe-abertura-p1.html")))
    assert processar_mensagem(sessao, _msg_retrato(retrato_id), FONTES, AGORA) == "ok"
    antes = _editais(sessao, "sanepar")
    vistos_antes = sessao.execute(select(Edital.id_na_fonte, Edital.visto_em)).all()

    mais_tarde = AGORA + timedelta(hours=1)
    assert processar_mensagem(sessao, _msg_retrato(retrato_id), FONTES, mais_tarde) == "ok"

    sessao.expire_all()
    assert _editais(sessao, "sanepar") == antes
    assert sessao.execute(select(Edital.id_na_fonte, Edital.visto_em)).all() == vistos_antes
    assert _estado(sessao, retrato_id).processado_em == AGORA


def test_retrato_obsoleto_nao_reconcilia(sessao: Session, caplog: pytest.LogCaptureFixture) -> None:
    velho = _retrato(sessao, _paginas_sanepar(_lista("lista-oe-abertura-p1.html")), AGORA)
    novo = _retrato(
        sessao,
        _paginas_sanepar(_lista("lista-oe-abertura-p2.html")),
        AGORA + timedelta(hours=1),
    )

    assert processar_mensagem(sessao, _msg_retrato(novo), FONTES, AGORA) == "ok"
    depois_do_novo = _editais(sessao, "sanepar")
    with caplog.at_level(logging.INFO):
        assert processar_mensagem(sessao, _msg_retrato(velho), FONTES, AGORA) == "ok"

    assert "retrato_obsoleto" in [r.getMessage() for r in caplog.records]
    assert _editais(sessao, "sanepar") == depois_do_novo
    assert _estado(sessao, velho).processado_em is not None
    nao_marcadas = sessao.scalar(
        select(func.count()).where(
            PaginaBruta.retrato_id == velho, PaginaBruta.processada_em.is_(None)
        )
    )
    assert nao_marcadas == 0


class SaneparComInvalidos:
    """A Sanepar real, com a uf trocada em todos os editais: o parser 'lendo errado'."""

    def __init__(self) -> None:
        self._real = FonteSanepar()
        self.nome = self._real.nome
        self.minimo_registros = self._real.minimo_registros
        self.marcador_bloqueio = self._real.marcador_bloqueio
        self.rotulo_saida = self._real.rotulo_saida

    def coletar(self, cliente: Any) -> Any:
        raise AssertionError("o consumidor não coleta")

    def extrair(self, paginas: list[PaginaColetada]) -> list[dict[str, Any]]:
        return [n | {"uf": "PRX"} for n in self._real.extrair(paginas)]


def test_retrato_muitos_invalidos_vira_falha_definitiva(
    sessao: Session, caplog: pytest.LogCaptureFixture
) -> None:
    _ja_abertos(sessao, "OE-1/26")
    antes = _editais(sessao, "sanepar")
    retrato_id = _retrato(sessao, _paginas_sanepar(_lista("lista-oe-abertura-p1.html")))
    fontes: dict[str, FonteRetrato] = {"sanepar": SaneparComInvalidos()}

    with caplog.at_level(logging.INFO):
        assert processar_mensagem(sessao, _msg_retrato(retrato_id), fontes, AGORA) == (
            "falha_definitiva"
        )

    assert _editais(sessao, "sanepar") == antes  # nada gravado, nada reconciliado
    estado = _estado(sessao, retrato_id)
    assert estado.status == "falha"
    assert estado.processado_em is None
    falha = next(r for r in caplog.records if r.getMessage() == "processamento_falhou")
    assert (falha.fonte, falha.retrato_id) == ("sanepar", retrato_id)
    assert "inválidos" in falha.erro


def test_retrato_sem_paginas_e_falha_definitiva(
    sessao: Session, caplog: pytest.LogCaptureFixture
) -> None:
    _ja_abertos(sessao, "OE-1/26")
    antes = _editais(sessao, "sanepar")
    retrato_id = _retrato(sessao, [])

    with caplog.at_level(logging.INFO):
        assert processar_mensagem(sessao, _msg_retrato(retrato_id), FONTES, AGORA) == (
            "falha_definitiva"
        )

    assert _editais(sessao, "sanepar") == antes
    assert _estado(sessao, retrato_id).status == "falha"
    assert "processamento_falhou" in [r.getMessage() for r in caplog.records]


def test_retrato_com_paginas_incompletas_e_falha_definitiva(
    sessao: Session, caplog: pytest.LogCaptureFixture
) -> None:
    _ja_abertos(sessao, "OE-1/26")
    antes = _editais(sessao, "sanepar")
    retrato_id = _retrato(sessao, _paginas_sanepar(_lista("lista-oe-abertura-p1.html")))
    sessao.execute(update(ColetaRetrato).where(ColetaRetrato.id == retrato_id).values(paginas=3))
    sessao.commit()

    with caplog.at_level(logging.INFO):
        assert processar_mensagem(sessao, _msg_retrato(retrato_id), FONTES, AGORA) == (
            "falha_definitiva"
        )

    assert _editais(sessao, "sanepar") == antes
    estado = _estado(sessao, retrato_id)
    assert estado.status == "falha"
    assert estado.erro == "páginas incompletas: 2 de 3"
    assert "processamento_falhou" in [r.getMessage() for r in caplog.records]


def test_retrato_abaixo_do_minimo_no_consumidor_e_falha_definitiva(
    sessao: Session, caplog: pytest.LogCaptureFixture
) -> None:
    _ja_abertos(sessao, "OE-1/26")
    antes = _editais(sessao, "sanepar")
    retrato_id = _retrato(sessao, _paginas_sanepar(_lista("lista-oe-abertura-p1.html")))
    fonte = FonteSanepar()
    minimo = len(fonte.extrair(_paginas_sanepar(_lista("lista-oe-abertura-p1.html")))) + 1
    fonte.minimo_registros = minimo
    fontes: dict[str, FonteRetrato] = {"sanepar": fonte}

    with caplog.at_level(logging.INFO):
        assert processar_mensagem(sessao, _msg_retrato(retrato_id), fontes, AGORA) == (
            "falha_definitiva"
        )

    assert _editais(sessao, "sanepar") == antes
    estado = _estado(sessao, retrato_id)
    assert estado.status == "falha"
    assert estado.erro is not None and estado.erro.startswith("abaixo do minimo no consumidor:")
    assert "processamento_falhou" in [r.getMessage() for r in caplog.records]


def test_retrato_rebaixado_e_descartado(sessao: Session, caplog: pytest.LogCaptureFixture) -> None:
    _ja_abertos(sessao, "OE-1/26")
    antes = _editais(sessao, "sanepar")
    retrato_id = _retrato(
        sessao, _paginas_sanepar(_lista("lista-oe-abertura-p1.html")), status="falha"
    )

    with caplog.at_level(logging.INFO):
        assert processar_mensagem(sessao, _msg_retrato(retrato_id), FONTES, AGORA) == "ok"

    assert _editais(sessao, "sanepar") == antes
    assert _estado(sessao, retrato_id).processado_em is None
    descarte = next(r for r in caplog.records if r.getMessage() == "retrato_descartado")
    assert descarte.status == "falha"


def test_trava_ocupada_adia(sessao: Session) -> None:
    retrato_id = _retrato(sessao, _paginas_sanepar(_lista("lista-oe-abertura-p1.html")))
    with trava_fonte(sessao, "sanepar") as obtida:  # outro processo com o lease vivo
        assert obtida
        with pytest.raises(MensagemAdiada):
            processar_mensagem(sessao, _msg_retrato(retrato_id), FONTES, AGORA)

    assert _estado(sessao, retrato_id).processado_em is None
    assert _editais(sessao, "sanepar") == {}


def test_fontes_retrato() -> None:
    assert list(fontes_retrato()) == ["sanepar", "compesa", "dae_jundiai"]


class SaneparParserQuebrado(SaneparComInvalidos):
    def extrair(self, paginas: list[PaginaColetada]) -> list[dict[str, Any]]:
        raise ValueError("layout novo")


def test_retrato_parser_quebrado_e_falha_definitiva(
    sessao: Session, caplog: pytest.LogCaptureFixture
) -> None:
    _ja_abertos(sessao, "OE-1/26")
    antes = _editais(sessao, "sanepar")
    retrato_id = _retrato(sessao, _paginas_sanepar(_lista("lista-oe-abertura-p1.html")))
    fontes: dict[str, FonteRetrato] = {"sanepar": SaneparParserQuebrado()}

    with caplog.at_level(logging.INFO):
        assert processar_mensagem(sessao, _msg_retrato(retrato_id), fontes, AGORA) == (
            "falha_definitiva"
        )

    assert _editais(sessao, "sanepar") == antes
    estado = _estado(sessao, retrato_id)
    assert (estado.status, estado.processado_em) == ("falha", None)
    assert estado.erro == "extrair: ValueError('layout novo')"
    falha = next(r for r in caplog.records if r.getMessage() == "processamento_falhou")
    assert falha.erro == "extrair: ValueError('layout novo')"


def test_retrato_inexistente_e_ok_com_erro_no_log(
    sessao: Session, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        assert processar_mensagem(sessao, _msg_retrato(999_999), FONTES, AGORA) == "ok"
    registro = next(r for r in caplog.records if r.getMessage() == "retrato_inexistente")
    assert registro.levelno == logging.ERROR


@pytest.fixture
def limpar_tudo(engine: Engine) -> Any:
    yield
    tabelas = ", ".join(tabela.name for tabela in Base.metadata.sorted_tables)
    with engine.begin() as conexao:
        conexao.execute(text(f"TRUNCATE {tabelas} RESTART IDENTITY CASCADE"))


def test_pagina_em_paralelo_espera_e_nao_registra_de_novo(
    engine: Engine, limpar_tudo: None
) -> None:
    # Review Focus 2: a mesma pagina_coletada em duas Lambdas ao mesmo tempo.
    with Session(engine) as preparo:
        pagina_id = _pagina_pncp(preparo)
    resultado: list[Any] = []

    def consumir() -> None:
        with Session(engine) as sessao:
            sessao.execute(text("SET lock_timeout = '10s'"))  # não trava a suíte
            try:
                resultado.append(processar_mensagem(sessao, _msg_pagina(pagina_id), FONTES, AGORA))
            except Exception as erro:
                resultado.append(erro)

    with engine.connect() as outra:
        outra.execute(select(PaginaBruta.id).where(PaginaBruta.id == pagina_id).with_for_update())
        consumidor = threading.Thread(target=consumir)
        consumidor.start()
        time.sleep(0.5)
        assert consumidor.is_alive()  # esperando a trava da linha
        outra.execute(
            update(PaginaBruta).where(PaginaBruta.id == pagina_id).values(processada_em=func.now())
        )
        outra.commit()  # a "primeira Lambda" terminou
        consumidor.join(timeout=10)

    assert not consumidor.is_alive()
    assert resultado == ["ok"]
    with Session(engine) as conferencia:
        assert conferencia.scalar(select(func.count()).select_from(Edital)) == 0


# --- telemetria: span do consumidor, ligado ao produtor por link ---------------------

TP_PRODUTOR = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def _span(spans: InMemorySpanExporter, nome: str) -> ReadableSpan:
    (achado,) = [s for s in spans.get_finished_spans() if s.name == nome]
    return achado


def test_span_do_consumidor_e_raiz_com_link(sessao: Session, spans: InMemorySpanExporter) -> None:
    pagina_id = _pagina_pncp(sessao)
    mensagem = Mensagem(9, "pagina_coletada", {"pagina_id": pagina_id}, TP_PRODUTOR)
    assert processar_com_span(sessao, mensagem, FONTES, AGORA, message_id="m-1") == "ok"
    span = _span(spans, "processar mensagem")
    assert span.parent is None
    assert span.kind == SpanKind.CONSUMER
    assert f"{span.context.trace_id:032x}" != TP_PRODUTOR.split("-")[1]
    assert [f"{link.context.span_id:016x}" for link in span.links] == ["b7ad6b7169203331"]
    assert span.attributes is not None
    assert span.attributes["messaging.message.id"] == "m-1"
    assert span.attributes["radar.outbox_id"] == 9
    assert span.attributes["radar.tipo"] == "pagina_coletada"
    assert span.attributes["radar.desfecho"] == "ok"
    registrar = _span(spans, "editais.registrar")
    assert registrar.parent is not None and registrar.parent.span_id == span.context.span_id


@pytest.mark.parametrize("traceparent", [None, "lixo"])
def test_sem_contexto_ou_malformado_processa_sem_link(
    sessao: Session, spans: InMemorySpanExporter, traceparent: str | None
) -> None:
    pagina_id = _pagina_pncp(sessao)
    mensagem = Mensagem(9, "pagina_coletada", {"pagina_id": pagina_id}, traceparent)
    assert processar_com_span(sessao, mensagem, FONTES, AGORA) == "ok"
    assert not _span(spans, "processar mensagem").links


def test_adiada_nao_e_erro_no_span(sessao: Session, spans: InMemorySpanExporter) -> None:
    retrato_id = _retrato(sessao, _paginas_sanepar(_lista("lista-oe-abertura-p1.html")))
    with trava_fonte(sessao, "sanepar") as obtida:
        assert obtida
        with pytest.raises(MensagemAdiada):
            processar_com_span(sessao, _msg_retrato(retrato_id), FONTES, AGORA)
    span = _span(spans, "processar mensagem")
    assert span.status.status_code != StatusCode.ERROR
    assert span.attributes is not None and span.attributes["radar.desfecho"] == "adiada"


def test_erro_inesperado_marca_o_span(sessao: Session, spans: InMemorySpanExporter) -> None:
    retrato_id = _retrato(sessao, _paginas_sanepar(_lista("lista-oe-abertura-p1.html")))
    with pytest.raises(KeyError):  # fonte desconhecida: erro de deploy, nova tentativa
        processar_com_span(sessao, _msg_retrato(retrato_id), {}, AGORA)
    span = _span(spans, "processar mensagem")
    assert span.status.status_code == StatusCode.ERROR
    # Só o tipo: a mensagem (ou o repr) pode trazer texto do edital.
    assert span.status.description == "KeyError"
    assert span.events == ()


def test_registrar_e_reconciliar_viram_spans_filhos(
    sessao: Session, spans: InMemorySpanExporter
) -> None:
    retrato_id = _retrato(sessao, _paginas_sanepar(_lista("lista-oe-abertura-p1.html")))
    assert processar_com_span(sessao, _msg_retrato(retrato_id), FONTES, AGORA) == "ok"
    raiz = _span(spans, "processar mensagem")
    filhos = {
        s.name: s
        for s in spans.get_finished_spans()
        if s.parent is not None and s.parent.span_id == raiz.context.span_id
    }
    assert set(filhos) == {"editais.registrar", "editais.reconciliar"}
    registrar = filhos["editais.registrar"].attributes or {}
    assert registrar["radar.inseridos"] == 20
    assert {"radar.atualizados", "radar.invalidos"} <= set(registrar)
    assert not {"inseridos", "atualizados", "invalidos"} & set(registrar)
    assert (filhos["editais.reconciliar"].attributes or {})["radar.encerrados"] == 0
