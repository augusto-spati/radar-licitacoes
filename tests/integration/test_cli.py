import copy
import json
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fakes import ClienteFalso, como_cliente
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from radar import cli
from radar.coleta import (
    ErroFonte,
    coletar_pendentes,
    ler_paginas_nao_processadas,
    marcar_processada,
    planejar_janelas,
)
from radar.coleta.modelos import ColetaJanela, ColetaRetrato, PaginaBruta
from radar.coleta.pncp import Pagina
from radar.comum import Base, Outbox, enfileirar, fabrica_sessao
from radar.editais import registrar
from radar.editais.modelos import Edital
from radar.processador import consumidor
from telemetria_memoria import EmissorEmMemoria

RAIZ = Path(__file__).resolve().parent.parent.parent
AMOSTRA: dict[str, Any] = json.loads(
    (RAIZ / "tests" / "dados" / "registro_pregao.json").read_text(encoding="utf-8")
)
AGORA = datetime(2026, 11, 26, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
HOJE = AGORA.date()

runner = CliRunner()
_ARGS_AUDITAR = ["auditar", "--endpoint", "publicacao", "--dias", "2"]


def _truncar(engine: Engine) -> None:
    tabelas = ", ".join(tabela.name for tabela in Base.metadata.sorted_tables)
    with engine.begin() as conexao:
        conexao.execute(text(f"TRUNCATE {tabelas} RESTART IDENTITY CASCADE"))


@pytest.fixture
def fabrica(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    """O CLI faz commit de verdade, então limpa as tabelas ao fim em vez de usar savepoint."""
    fabrica = fabrica_sessao(engine)
    monkeypatch.setattr(cli, "_criar_sessao", lambda: fabrica())
    monkeypatch.setattr(cli, "_agora", lambda: AGORA)
    for variavel in (
        "RADAR_FILA",
        "RADAR_FILA_URL",
        "RADAR_FILA_FALHAS_URL",
        "RADAR_FILA_JANELAS_URL",
        "RADAR_FILA_JANELAS_FALHAS_URL",
    ):
        monkeypatch.delenv(variavel, raising=False)
    yield fabrica
    _truncar(engine)


@pytest.fixture
def config_filtro(tmp_path: Path) -> Path:
    caminho = tmp_path / "filtro.toml"
    caminho.write_text(
        'ufs = ["SP"]\nmodalidades = [5, 6]\n\n[linhas]\nperdas = ["hidrometro"]\n',
        encoding="utf-8",
    )
    return caminho


def _registro(
    numero: str, objeto: str = "Leitura de hidrômetros", uf: str = "SP"
) -> dict[str, Any]:
    registro = copy.deepcopy(AMOSTRA)
    registro["numeroControlePNCP"] = numero
    registro["objetoCompra"] = objeto
    registro["unidadeOrgao"]["ufSigla"] = uf
    return registro


def _pagina(numero: int, registro: dict[str, Any]) -> Pagina:
    return Pagina(
        registros=[registro],
        total_paginas=2,
        numero_pagina=numero,
        bruto={"data": [registro], "totalPaginas": 2, "numeroPagina": numero},
    )


def _contar(fabrica: sessionmaker[Session], modelo: type) -> int:
    with fabrica() as sessao:
        return sessao.scalar(select(func.count()).select_from(modelo)) or 0


def test_carga_inicial_ponta_a_ponta(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roteiro: dict[tuple[str, date, date, int, int], Pagina | Exception] = {}
    for dias_atras in (1, 2):
        dia = HOJE - timedelta(days=dias_atras)
        for modalidade in (5, 6):
            for pagina in (1, 2):
                numero = f"{dia:%Y%m%d}-{modalidade}-{pagina}/2026"
                roteiro[("publicacao", dia, dia, modalidade, pagina)] = _pagina(
                    pagina, _registro(numero)
                )
    falso = ClienteFalso(roteiro)
    monkeypatch.setattr(cli, "_criar_cliente", lambda: como_cliente(falso))
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})  # este teste é só do PNCP

    resultado = runner.invoke(
        cli.app, ["carga-inicial", "--dias", "2", "--config", str(config_filtro)]
    )

    assert resultado.exit_code == 0, resultado.output
    with fabrica() as sessao:
        status = sessao.scalars(select(ColetaJanela.status)).all()
        processadas = sessao.scalar(
            select(func.count())
            .select_from(PaginaBruta)
            .where(PaginaBruta.processada_em.is_not(None))
        )
    assert status == ["concluida"] * 4
    assert processadas == 8
    assert _contar(fabrica, Edital) == 8

    auditoria = runner.invoke(
        cli.app,
        _ARGS_AUDITAR + ["--config", str(config_filtro)],
    )
    assert auditoria.exit_code == 0, auditoria.output


def test_auditar_sai_com_1_quando_falta(
    fabrica: sessionmaker[Session], config_filtro: Path
) -> None:
    resultado = runner.invoke(
        cli.app,
        _ARGS_AUDITAR + ["--config", str(config_filtro)],
    )

    assert resultado.exit_code == 1
    assert "faltando" in resultado.output
    # O alarme radar-auditoria-falhou conta este evento no log.
    assert '"evento": "auditoria_falhou"' in resultado.output


def test_filtrar_imprime_edital_aberto(fabrica: sessionmaker[Session], config_filtro: Path) -> None:
    with fabrica() as sessao:
        registrar(sessao, [_registro("1/2026"), _registro("2/2026", "Obra de ponte", "SP")])
        sessao.commit()

    resultado = runner.invoke(cli.app, ["filtrar", "--config", str(config_filtro)])

    assert resultado.exit_code == 0, resultado.output
    assert "11/12 10:00" in resultado.output
    assert "perdas" in resultado.output
    assert "Leitura de hidr" in resultado.output
    assert "ponte" not in resultado.output


def test_processar_isola_pagina_com_erro_e_sai_com_1(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with fabrica() as sessao:
        sessao.add(
            ColetaJanela(
                endpoint="publicacao",
                data_ini=HOJE,
                data_fim=HOJE,
                modalidade=6,
                status="concluida",
                paginas=3,
                registros=3,
            )
        )
        sessao.flush()
        janela_id = sessao.scalars(select(ColetaJanela.id)).one()
        for numero in (1, 2, 3):
            pagina = PaginaBruta(
                janela_id=janela_id,
                numero_pagina=numero,
                conteudo={"data": [_registro(f"{numero}/2026")]},
                coletada_em=AGORA,
            )
            sessao.add(pagina)
            sessao.flush()
            enfileirar(sessao, "pagina_coletada", {"pagina_id": pagina.id})
        sessao.commit()
    original = consumidor.registrar

    def registrar_com_erro(sessao: Session, registros: list[dict[str, Any]]) -> Any:
        if registros[0]["numeroControlePNCP"] == "2/2026":
            raise RuntimeError("boom")
        return original(sessao, registros)

    monkeypatch.setattr(consumidor, "registrar", registrar_com_erro)
    monkeypatch.setattr(cli, "_criar_cliente", lambda: como_cliente(ClienteFalso({})))

    resultado = runner.invoke(cli.app, ["processar"])

    assert resultado.exit_code == 1, resultado.output
    with fabrica() as sessao:
        pendentes = sessao.scalars(
            select(PaginaBruta.numero_pagina).where(PaginaBruta.processada_em.is_(None))
        ).all()
        outbox = sessao.execute(
            select(Outbox.publicada_em.is_(None), Outbox.erro).order_by(Outbox.id)
        ).all()
    assert pendentes == [2]
    assert _contar(fabrica, Edital) == 2
    # A recusada fica pendente na outbox, com o erro, para a próxima execução.
    assert [tuple(linha) for linha in outbox] == [
        (False, None),
        (True, "RuntimeError('boom')"),
        (False, None),
    ]


def test_auditar_rejeita_endpoint_invalido(
    fabrica: sessionmaker[Session], config_filtro: Path
) -> None:
    resultado = runner.invoke(
        cli.app, ["auditar", "--endpoint", "xpto", "--config", str(config_filtro)]
    )
    assert resultado.exit_code == 2


def test_auditar_atualizacao_usa_10_dias_por_padrao(
    fabrica: sessionmaker[Session], config_filtro: Path
) -> None:
    resultado = runner.invoke(
        cli.app, ["auditar", "--endpoint", "atualizacao", "--config", str(config_filtro)]
    )
    assert resultado.exit_code == 1
    assert "esperadas: 22" in resultado.output  # 11 dias (hoje e 10 antes) x 2 modalidades


# --- fila: relay no começo e no fim, falhas, auditoria, reprocessar ---------------------


def _roteiro_atualizacao() -> dict[tuple[str, date, date, int, int], Pagina | Exception]:
    """Dois dias x duas modalidades; um edital repetido entre páginas e outro fora do filtro."""
    roteiro: dict[tuple[str, date, date, int, int], Pagina | Exception] = {}
    for dias_atras in (0, 1):
        dia = HOJE - timedelta(days=dias_atras)
        for modalidade in (5, 6):
            for numero in (1, 2):
                registro = _registro(f"{dia:%Y%m%d}-{modalidade}-{numero}/2026")
                if numero == 2 and modalidade == 6:
                    registro = _registro("repetido/2026", objeto=f"Hidrômetros {dia:%d}")
                if numero == 2 and modalidade == 5:
                    registro = _registro(f"{dia:%Y%m%d}-ponte/2026", "Obra de ponte", "RJ")
                roteiro[("atualizacao", dia, dia, modalidade, numero)] = _pagina(numero, registro)
    return roteiro


def _sincrono_antigo(fabrica: sessionmaker[Session]) -> None:
    """O fluxo de antes da fila: coleta e, em seguida, registra as páginas não processadas."""
    with fabrica() as sessao:
        planejar_janelas(sessao, "atualizacao", HOJE - timedelta(days=1), HOJE, [5, 6])
        coletar_pendentes(sessao, como_cliente(ClienteFalso(_roteiro_atualizacao())), AGORA)
        while lote := ler_paginas_nao_processadas(sessao):
            for pagina_id, registros in lote:
                registrar(sessao, registros)
                marcar_processada(sessao, pagina_id)


def _resumo_editais(fabrica: sessionmaker[Session]) -> tuple[int, int, str]:
    with fabrica() as sessao:
        total = sessao.scalar(select(func.count()).select_from(Edital)) or 0
        abertos = sessao.scalar(
            select(func.count()).select_from(Edital).where(Edital.situacao_normalizada == "aberta")
        )
        objetos = ",".join(sessao.scalars(select(Edital.objeto).order_by(Edital.id_na_fonte)))
    return total, abertos or 0, objetos


def test_atualizar_local_equivale_ao_sincrono(
    engine: Engine,
    fabrica: sessionmaker[Session],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})
    _sincrono_antigo(fabrica)
    esperado = _resumo_editais(fabrica)
    filtrar_antigo = runner.invoke(cli.app, ["filtrar", "--todos", "--config", str(config_filtro)])
    _truncar(engine)

    falso = ClienteFalso(_roteiro_atualizacao())
    monkeypatch.setattr(cli, "_criar_cliente", lambda: como_cliente(falso))
    resultado = runner.invoke(
        cli.app, ["atualizar", "--lookback", "1", "--config", str(config_filtro)]
    )

    assert resultado.exit_code == 0, resultado.output
    assert esperado[0] == 7  # 8 páginas; o repetido vira um edital só
    assert _resumo_editais(fabrica) == esperado
    filtrar_novo = runner.invoke(cli.app, ["filtrar", "--todos", "--config", str(config_filtro)])
    assert filtrar_novo.output == filtrar_antigo.output
    assert "Hidr" in filtrar_novo.output
    with fabrica() as sessao:
        pendentes = sessao.scalar(
            select(func.count()).select_from(Outbox).where(Outbox.publicada_em.is_(None))
        )
    assert pendentes == 0


def _pagina_pendente(fabrica: sessionmaker[Session], numero: str, criada_em: datetime) -> int:
    """Página do PNCP coletada e com mensagem na outbox, ainda não publicada."""
    modalidade = 100 + _contar(fabrica, ColetaJanela)  # uma janela nova a cada chamada
    with fabrica() as sessao:
        janela = ColetaJanela(
            endpoint="atualizacao",
            data_ini=HOJE,
            data_fim=HOJE,
            modalidade=modalidade,
            status="concluida",
            paginas=1,
            registros=1,
        )
        sessao.add(janela)
        sessao.flush()
        pagina = PaginaBruta(
            janela_id=janela.id,
            numero_pagina=1,
            conteudo={"data": [_registro(numero)]},
            coletada_em=criada_em,
        )
        sessao.add(pagina)
        sessao.flush()
        carga = {"pagina_id": pagina.id}
        sessao.add(Outbox(tipo="pagina_coletada", carga=carga, criada_em=criada_em))
        sessao.commit()
        return pagina.id


def test_atualizar_publica_pendencias_antigas_no_comeco(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # O container morreu ontem entre gravar a outbox e publicar: a mensagem ficou pendente.
    pagina_id = _pagina_pendente(fabrica, "ontem/2026", AGORA - timedelta(days=1))
    vista_na_coleta: list[bool] = []

    def coleta_sem_novidade(*_args: Any) -> int:
        with fabrica() as outra:
            pagina = outra.get(PaginaBruta, pagina_id)
            vista_na_coleta.append(pagina is not None and pagina.processada_em is not None)
        return 0

    monkeypatch.setattr(cli, "_rodar_pncp", coleta_sem_novidade)
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})

    resultado = runner.invoke(cli.app, ["atualizar", "--config", str(config_filtro)])

    assert resultado.exit_code == 0, resultado.output
    assert vista_na_coleta == [True]  # processada antes de coletar, não só no fim
    assert _contar(fabrica, Edital) == 1
    with fabrica() as sessao:
        assert sessao.scalars(select(Outbox.publicada_em)).one() is not None


def _retrato_sem_paginas(fabrica: sessionmaker[Session]) -> int:
    with fabrica() as sessao:
        retrato = ColetaRetrato(
            fonte="sanepar",
            status="concluido",
            iniciado_em=AGORA,
            concluido_em=AGORA,
            paginas=1,
            registros=5,
        )
        sessao.add(retrato)
        sessao.flush()
        enfileirar(sessao, "retrato_coletado", {"retrato_id": retrato.id})
        sessao.commit()
        return retrato.id


def test_falha_definitiva_local_sai_com_1(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    retrato_id = _retrato_sem_paginas(fabrica)
    monkeypatch.setattr(cli, "_criar_cliente", lambda: como_cliente(ClienteFalso({})))

    resultado = runner.invoke(cli.app, ["processar"])

    assert resultado.exit_code == 1, resultado.output
    with fabrica() as sessao:
        retrato = sessao.get(ColetaRetrato, retrato_id)
        assert retrato is not None
        assert (retrato.status, retrato.erro) == ("falha", "páginas ausentes")
        # Falha definitiva é confirmada: repetir não muda nada.
        assert sessao.scalars(select(Outbox.publicada_em)).one() is not None


def _cobrir_pncp(fabrica: sessionmaker[Session]) -> None:
    with fabrica() as sessao:
        for dias_atras in (1, 2):
            for modalidade in (5, 6):
                dia = HOJE - timedelta(days=dias_atras)
                sessao.add(
                    ColetaJanela(
                        endpoint="publicacao",
                        data_ini=dia,
                        data_fim=dia,
                        modalidade=modalidade,
                        status="concluida",
                        paginas=0,
                        registros=0,
                    )
                )
        sessao.commit()


def _auditar(config_filtro: Path) -> Any:
    return runner.invoke(cli.app, [*_ARGS_AUDITAR, "--config", str(config_filtro)])


def test_auditar_aponta_outbox_pendente(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})
    _cobrir_pncp(fabrica)
    limpo = _auditar(config_filtro)
    assert limpo.exit_code == 0, limpo.output
    assert "outbox pendente: 0" in limpo.output
    assert '"evento": "auditoria_ok"' in limpo.output
    assert "auditoria_falhou" not in limpo.output
    _pagina_pendente(fabrica, "1/2026", AGORA - timedelta(hours=2))
    _pagina_pendente(fabrica, "2/2026", AGORA - timedelta(minutes=5))

    resultado = _auditar(config_filtro)

    assert resultado.exit_code == 1
    assert "outbox pendente: 2 (a mais velha de 2026-11-26 10:00)" in resultado.output


def test_auditar_aponta_pagina_publicada_parada_e_retrato_sem_processar(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})
    _cobrir_pncp(fabrica)
    velha = _pagina_pendente(fabrica, "1/2026", AGORA - timedelta(hours=3))
    recente = _pagina_pendente(fabrica, "2/2026", AGORA - timedelta(hours=3))
    _retrato_sem_paginas(fabrica)
    with fabrica() as sessao:
        for outbox in sessao.scalars(select(Outbox)):
            recem = outbox.carga.get("pagina_id") == recente
            outbox.publicada_em = AGORA - (timedelta(0) if recem else timedelta(hours=2))
        sessao.commit()
    assert velha != recente

    resultado = _auditar(config_filtro)

    assert resultado.exit_code == 1
    assert "outbox pendente: 0" in resultado.output
    assert "páginas publicadas sem processar há mais de 1 h: 1" in resultado.output
    assert "retratos concluídos sem processar: 1" in resultado.output


def test_publicar_processa_e_apaga_publicadas_antigas(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})
    pagina_id = _pagina_pendente(fabrica, "1/2026", AGORA - timedelta(hours=1))
    with fabrica() as sessao:
        antiga = Outbox(
            tipo="pagina_coletada", carga={"pagina_id": 999}, publicada_em=AGORA - timedelta(days=8)
        )
        sessao.add(antiga)
        sessao.commit()

    resultado = runner.invoke(cli.app, ["publicar"])

    assert resultado.exit_code == 0, resultado.output
    assert _contar(fabrica, Edital) == 1
    with fabrica() as sessao:
        restantes = sessao.scalars(select(Outbox.carga)).all()
    assert restantes == [{"pagina_id": pagina_id}]


class SQSFalso:
    """Cliente boto3 de mentira: recusa todo envio e registra o pedido de redrive."""

    def __init__(self, na_dlq: int = 3) -> None:
        self.na_dlq = na_dlq
        self.movimentos: list[dict[str, str]] = []
        self.atributos: list[tuple[str, list[str]]] = []

    def send_message_batch(self, QueueUrl: str, Entries: list[dict[str, str]]) -> dict[str, Any]:  # noqa: N803
        falhas = [
            {"Id": e["Id"], "Code": "AWS.SimpleQueueService.Throttled", "Message": "calma"}
            for e in Entries
        ]
        return {"Successful": [], "Failed": falhas}

    def get_queue_attributes(self, QueueUrl: str, AttributeNames: list[str]) -> dict[str, Any]:  # noqa: N803
        nome = QueueUrl.rsplit("/", 1)[-1]
        self.atributos.append((nome, AttributeNames))
        atributos = {"QueueArn": f"arn:aws:sqs:us-east-2:123456789012:{nome}"}
        if "ApproximateNumberOfMessages" in AttributeNames:
            atributos["ApproximateNumberOfMessages"] = str(self.na_dlq)
        return {"Attributes": atributos}

    def start_message_move_task(self, SourceArn: str, DestinationArn: str) -> dict[str, str]:  # noqa: N803
        self.movimentos.append({"SourceArn": SourceArn, "DestinationArn": DestinationArn})
        return {"TaskHandle": "handle-123"}


_URL_FILA = "https://sqs.us-east-2.amazonaws.com/123456789012/radar-editais"
_URL_FALHAS = "https://sqs.us-east-2.amazonaws.com/123456789012/radar-editais-falhas"
_URL_JANELAS = "https://sqs.us-east-2.amazonaws.com/123456789012/radar-janelas"


def test_reprocessar_falhas_local_sai_com_2(fabrica: sessionmaker[Session]) -> None:
    resultado = runner.invoke(cli.app, ["reprocessar-falhas"])

    assert resultado.exit_code == 2
    assert "RADAR_FILA=sqs" in resultado.output


def test_reprocessar_falhas_move_da_dlq_para_a_principal(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_FILA", "sqs")
    monkeypatch.setenv("RADAR_FILA_URL", _URL_FILA)
    monkeypatch.setenv("RADAR_FILA_FALHAS_URL", _URL_FALHAS)
    sqs = SQSFalso()
    monkeypatch.setattr(cli, "_criar_cliente_sqs", lambda _url: sqs)

    resultado = runner.invoke(cli.app, ["reprocessar-falhas"])

    assert resultado.exit_code == 0, resultado.output
    assert sqs.movimentos == [
        {
            "SourceArn": "arn:aws:sqs:us-east-2:123456789012:radar-editais-falhas",
            "DestinationArn": "arn:aws:sqs:us-east-2:123456789012:radar-editais",
        }
    ]
    assert sqs.atributos == [
        ("radar-editais-falhas", ["QueueArn", "ApproximateNumberOfMessages"]),
        ("radar-editais", ["QueueArn"]),
    ]
    assert "3 mensagens sendo movidas para a fila principal (tarefa handle-123)" in (
        resultado.output
    )


def test_reprocessar_falhas_com_dlq_vazia_nao_move(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_FILA", "sqs")
    monkeypatch.setenv("RADAR_FILA_URL", _URL_FILA)
    monkeypatch.setenv("RADAR_FILA_FALHAS_URL", _URL_FALHAS)
    sqs = SQSFalso(na_dlq=0)
    monkeypatch.setattr(cli, "_criar_cliente_sqs", lambda _url: sqs)

    resultado = runner.invoke(cli.app, ["reprocessar-falhas"])

    assert resultado.exit_code == 0, resultado.output
    assert sqs.movimentos == []
    assert "nenhuma mensagem na fila de falhas" in resultado.output


def test_reprocessar_falhas_sem_url_da_dlq_sai_com_2(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_FILA", "sqs")
    monkeypatch.setenv("RADAR_FILA_URL", _URL_FILA)
    monkeypatch.setattr(cli, "_criar_cliente_sqs", lambda _url: SQSFalso())

    resultado = runner.invoke(cli.app, ["reprocessar-falhas"])

    assert resultado.exit_code == 2
    assert "RADAR_FILA_FALHAS_URL" in resultado.output


def test_processar_com_sqs_recusando_sai_com_1_e_guarda_o_erro(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_FILA", "sqs")
    monkeypatch.setenv("RADAR_FILA_URL", _URL_FILA)
    monkeypatch.setenv("RADAR_FILA_JANELAS_URL", _URL_JANELAS)
    monkeypatch.setattr(cli, "_criar_cliente_sqs", lambda _url: SQSFalso())
    monkeypatch.setattr(cli, "_criar_cliente", lambda: como_cliente(ClienteFalso({})))
    _pagina_pendente(fabrica, "1/2026", AGORA - timedelta(hours=1))

    resultado = runner.invoke(cli.app, ["processar"])

    assert resultado.exit_code == 1, resultado.output
    with fabrica() as sessao:
        outbox = sessao.scalars(select(Outbox)).one()
        # Relay no começo e no fim: duas tentativas recusadas.
        assert (outbox.publicada_em, outbox.tentativas) == (None, 2)
        assert outbox.erro == "AWS.SimpleQueueService.Throttled: calma"
    assert _contar(fabrica, Edital) == 0


def test_fila_sqs_sem_url_de_janelas_sai_com_2(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_FILA", "sqs")
    monkeypatch.setenv("RADAR_FILA_URL", _URL_FILA)
    monkeypatch.delenv("RADAR_FILA_JANELAS_URL", raising=False)

    resultado = runner.invoke(cli.app, ["publicar"])

    assert resultado.exit_code == 2
    assert "RADAR_FILA_JANELAS_URL" in resultado.output


# --- etapa 4b: resultado de volta, planejar, janelas paradas, reprocessar --fila ---------


def _ultima_linha(saida: str) -> str:
    return saida.strip().splitlines()[-1]


def test_atualizar_imprime_resultado(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})
    falso = ClienteFalso(_roteiro_atualizacao())
    monkeypatch.setattr(cli, "_criar_cliente", lambda: como_cliente(falso))

    ok = runner.invoke(cli.app, ["atualizar", "--lookback", "1", "--config", str(config_filtro)])

    assert ok.exit_code == 0, ok.output
    assert _ultima_linha(ok.output) == "resultado: sem falhas"

    _pagina_pendente(fabrica, "1/2026", AGORA)
    monkeypatch.setattr(cli, "_publicar", lambda sessao, fila: 1)
    ruim = runner.invoke(cli.app, ["atualizar", "--lookback", "1", "--config", str(config_filtro)])

    assert ruim.exit_code == 1, ruim.output
    # relay do começo e do fim
    assert _ultima_linha(ruim.output) == "resultado: 2 falhas (coleta 0, relay 2, fila 0)"


@pytest.mark.parametrize("comando", [["processar"], ["carga-inicial", "--dias", "1"]])
def test_processar_e_carga_inicial_imprimem_resultado(
    fabrica: sessionmaker[Session],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
    comando: list[str],
) -> None:
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})
    ontem = HOJE - timedelta(days=1)
    roteiro: dict[tuple[str, date, date, int, int], Pagina | Exception] = {
        ("publicacao", ontem, ontem, m, 1): Pagina([], 0, 1, {}) for m in (5, 6)
    }
    monkeypatch.setattr(cli, "_criar_cliente", lambda: como_cliente(ClienteFalso(roteiro)))
    extra = ["--config", str(config_filtro)] if comando[0] == "carga-inicial" else []

    resultado = runner.invoke(cli.app, [*comando, *extra])

    assert resultado.exit_code == 0, resultado.output
    assert _ultima_linha(resultado.output) == "resultado: sem falhas"


def test_atualizar_so_retratos_nao_pede_o_pncp(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    falso = ClienteFalso({})
    monkeypatch.setattr(cli, "_criar_cliente", lambda: como_cliente(falso))
    rodados: list[str] = []
    monkeypatch.setattr(
        cli, "_rodar_retrato", lambda sessao, fonte: rodados.append(fonte.nome) or 0
    )

    resultado = runner.invoke(
        cli.app, ["atualizar", "--fontes", "sanepar,compesa", "--config", str(config_filtro)]
    )

    assert resultado.exit_code == 0, resultado.output
    assert falso.chamadas == []
    assert rodados == ["sanepar", "compesa"]
    assert _contar(fabrica, ColetaJanela) == 0
    assert _ultima_linha(resultado.output) == "resultado: sem falhas"


def test_planejar_local_coleta_tudo(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})
    falso = ClienteFalso(_roteiro_atualizacao())
    monkeypatch.setattr(cli, "_criar_cliente", lambda: como_cliente(falso))

    resultado = runner.invoke(cli.app, ["planejar", "--recuo", "1", "--config", str(config_filtro)])

    assert resultado.exit_code == 0, resultado.output
    assert "janelas novas: 4, enfileiradas: 4" in resultado.output
    assert _ultima_linha(resultado.output) == "resultado: sem falhas"
    assert len(falso.chamadas) == 8
    with fabrica() as sessao:
        assert set(sessao.scalars(select(ColetaJanela.status))) == {"concluida"}
        pendentes = sessao.scalar(
            select(func.count()).select_from(Outbox).where(Outbox.publicada_em.is_(None))
        )
    assert pendentes == 0
    assert _contar(fabrica, Edital) == 7  # o mesmo que o atualizar local


def test_auditar_aponta_janela_parada(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})
    _cobrir_pncp(fabrica)
    with fabrica() as sessao:
        sessao.add(
            ColetaJanela(
                endpoint="atualizacao",
                data_ini=HOJE,
                data_fim=HOJE,
                modalidade=6,
                status="em_andamento",
                paginas=0,
                registros=0,
                proxima_pagina=7,
                total_paginas=80,
                iniciada_em=AGORA - timedelta(hours=7),
            )
        )
        sessao.add(
            ColetaJanela(
                endpoint="atualizacao",
                data_ini=HOJE,
                data_fim=HOJE,
                modalidade=5,
                status="em_andamento",
                paginas=0,
                registros=0,
                iniciada_em=AGORA - timedelta(hours=1),
            )
        )
        sessao.commit()

    resultado = _auditar(config_filtro)

    assert resultado.exit_code == 1
    assert "janelas em andamento há mais de 6 h: 1\n" in resultado.output
    assert f"  {HOJE:%Y-%m-%d} modalidade 6 (página 7/80)" in resultado.output


def test_auditar_falha_mostra_onde_parou(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})
    _cobrir_pncp(fabrica)
    ontem = HOJE - timedelta(days=1)
    with fabrica() as sessao:
        janela = sessao.scalars(
            select(ColetaJanela).where(ColetaJanela.data_ini == ontem, ColetaJanela.modalidade == 6)
        ).one()
        janela.status, janela.erro = "falha", "HTTP 500"
        janela.proxima_pagina, janela.total_paginas = 3, 5
        sessao.commit()

    resultado = _auditar(config_filtro)

    assert resultado.exit_code == 1
    assert f"  {ontem:%Y-%m-%d} modalidade 6 (página 3/5): HTTP 500" in resultado.output
    assert "janelas em andamento há mais de 6 h: 0" in resultado.output


def test_reprocessar_falhas_janelas_local_sai_com_2(fabrica: sessionmaker[Session]) -> None:
    resultado = runner.invoke(cli.app, ["reprocessar-falhas", "--fila", "janelas"])

    assert resultado.exit_code == 2
    assert "RADAR_FILA=sqs" in resultado.output


def test_reprocessar_falhas_fila_invalida_sai_com_2(fabrica: sessionmaker[Session]) -> None:
    resultado = runner.invoke(cli.app, ["reprocessar-falhas", "--fila", "xpto"])

    assert resultado.exit_code == 2
    assert "fila inválida" in resultado.output


def test_reprocessar_falhas_janelas_move_da_dlq_de_janelas(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_FILA", "sqs")
    monkeypatch.setenv("RADAR_FILA_URL", _URL_FILA)
    monkeypatch.setenv("RADAR_FILA_FALHAS_URL", _URL_FALHAS)
    monkeypatch.setenv("RADAR_FILA_JANELAS_URL", _URL_JANELAS)
    monkeypatch.setenv("RADAR_FILA_JANELAS_FALHAS_URL", _URL_JANELAS + "-falhas")
    sqs = SQSFalso(na_dlq=2)
    monkeypatch.setattr(cli, "_criar_cliente_sqs", lambda _url: sqs)

    resultado = runner.invoke(cli.app, ["reprocessar-falhas", "--fila", "janelas"])

    assert resultado.exit_code == 0, resultado.output
    assert sqs.movimentos == [
        {
            "SourceArn": "arn:aws:sqs:us-east-2:123456789012:radar-janelas-falhas",
            "DestinationArn": "arn:aws:sqs:us-east-2:123456789012:radar-janelas",
        }
    ]


def test_reprocessar_falhas_janelas_sem_urls_sai_com_2(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_FILA", "sqs")
    monkeypatch.setenv("RADAR_FILA_URL", _URL_FILA)
    monkeypatch.setenv("RADAR_FILA_FALHAS_URL", _URL_FALHAS)

    resultado = runner.invoke(cli.app, ["reprocessar-falhas", "--fila", "janelas"])

    assert resultado.exit_code == 2
    assert "RADAR_FILA_JANELAS_URL e RADAR_FILA_JANELAS_FALHAS_URL" in resultado.output


@pytest.mark.parametrize("comando", ["atualizar", "carga-inicial", "processar"])
@pytest.mark.parametrize(
    ("falhas", "codigo", "linha"),
    [
        (0, 0, "resultado: sem falhas"),
        (1, 1, "resultado: 1 falhas (coleta 1, relay 0, fila 0)"),
    ],
)
def test_execucao_imprime_linha_resultado_que_o_alarme_conta(
    fabrica: sessionmaker[Session],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
    comando: str,
    falhas: int,
    codigo: int,
    linha: str,
) -> None:
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})
    monkeypatch.setattr(cli, "_criar_cliente", lambda: como_cliente(ClienteFalso({})))
    monkeypatch.setattr(cli, "_coletar", lambda sessao, cliente: falhas)
    args = [comando] if comando == "processar" else [comando, "--config", str(config_filtro)]

    resultado = runner.invoke(cli.app, args)

    assert resultado.exit_code == codigo, resultado.output
    linhas = [x for x in resultado.output.splitlines() if x.startswith("resultado: ")]
    assert linhas == [linha]


def _janela_com_lease_vivo(fabrica: sessionmaker[Session]) -> None:
    with fabrica() as sessao:
        sessao.add(
            ColetaJanela(
                endpoint="atualizacao",
                data_ini=HOJE,
                data_fim=HOJE,
                modalidade=6,
                status="em_andamento",
                paginas=0,
                registros=0,
                travada_por="lambda",
                travada_ate=datetime.now(ZoneInfo("UTC")) + timedelta(minutes=10),
            )
        )
        sessao.commit()


@pytest.mark.parametrize(
    "args", [["atualizar", "--fontes", "pncp"], ["atualizar"], ["processar"], ["carga-inicial"]]
)
def test_pncp_local_com_lease_vivo_sai_com_2(
    fabrica: sessionmaker[Session],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})
    falso = ClienteFalso({})
    monkeypatch.setattr(cli, "_criar_cliente", lambda: como_cliente(falso))
    _janela_com_lease_vivo(fabrica)
    extra = ["--config", str(config_filtro)] if args[0] != "processar" else []

    resultado = runner.invoke(cli.app, [*args, *extra])

    assert resultado.exit_code == 2, resultado.output
    assert "lease vivo" in resultado.output
    assert falso.chamadas == []
    assert _contar(fabrica, ColetaJanela) == 1  # nada planejado


def test_atualizar_so_retratos_ignora_lease_vivo_do_pncp(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_rodar_retrato", lambda sessao, fonte: 0)
    _janela_com_lease_vivo(fabrica)

    resultado = runner.invoke(
        cli.app, ["atualizar", "--fontes", "sanepar", "--config", str(config_filtro)]
    )

    assert resultado.exit_code == 0, resultado.output


def test_publicar_local_deixa_coletar_janela_para_a_lambda(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})
    with fabrica() as sessao:
        enfileirar(sessao, "coletar_janela", {"janela_id": 1, "lote": 0})
        sessao.commit()

    resultado = runner.invoke(cli.app, ["publicar"])

    assert resultado.exit_code == 0, resultado.output
    assert _ultima_linha(resultado.output) == "resultado: sem falhas"
    with fabrica() as sessao:
        linha = sessao.scalars(select(Outbox)).one()
    assert (linha.publicada_em, linha.tentativas, linha.erro) == (None, 0, None)


def test_consumo_e_outro_trace_com_link_para_a_janela(
    fabrica: sessionmaker[Session],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
    spans: InMemorySpanExporter,
) -> None:
    """Fila local: a página coletada em `coleta.janela` é consumida noutro trace, por link."""
    dia = HOJE - timedelta(days=1)
    roteiro: dict[tuple[str, date, date, int, int], Pagina | Exception] = {
        ("publicacao", dia, dia, modalidade, pagina): _pagina(
            pagina, _registro(f"{modalidade}-{pagina}/2026")
        )
        for modalidade in (5, 6)
        for pagina in (1, 2)
    }
    monkeypatch.setattr(cli, "_criar_cliente", lambda: como_cliente(ClienteFalso(roteiro)))
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})

    resultado = runner.invoke(
        cli.app, ["carga-inicial", "--dias", "1", "--config", str(config_filtro)]
    )

    assert resultado.exit_code == 0, resultado.output
    terminados = spans.get_finished_spans()
    janelas = {s.context.span_id: s for s in terminados if s.name == "coleta.janela"}
    consumos = [s for s in terminados if s.name == "processar mensagem"]
    assert len(janelas) == 2 and len(consumos) == 4
    for consumo in consumos:
        assert consumo.parent is None
        (link,) = consumo.links
        janela = janelas[link.context.span_id]
        assert consumo.context.trace_id != janela.context.trace_id
        assert link.context.trace_id == janela.context.trace_id


# ---------- Telemetria do processo: frescor, ExecucaoOk e a linha resultado ----------


class FonteVazia:
    """Fonte de retrato que falha na primeira página, sem rede (o frescor é o que importa)."""

    marcador_bloqueio: str | None = None
    minimo_registros = 1
    rotulo_saida = "saiu da lista"

    def __init__(self, nome: str) -> None:
        self.nome = nome

    def coletar(self, cliente: Any) -> Iterator[Any]:
        raise ErroFonte("fora do ar")
        yield

    def extrair(self, paginas: list[Any]) -> list[dict[str, Any]]:
        return []


def _atualizar_pncp(
    config_filtro: Path, monkeypatch: pytest.MonkeyPatch, roteiro: dict[Any, Any] | None = None
) -> Any:
    falso = ClienteFalso(_roteiro_atualizacao() if roteiro is None else roteiro)
    monkeypatch.setattr(cli, "_criar_cliente", lambda: como_cliente(falso))
    return runner.invoke(cli.app, ["atualizar", "--lookback", "1", "--config", str(config_filtro)])


def test_atualizar_mede_frescor_no_comeco_e_no_fim(
    fabrica: sessionmaker[Session],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
    metricas: EmissorEmMemoria,
) -> None:
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {"sanepar": FonteVazia("sanepar")})
    resultado = runner.invoke(
        cli.app, ["atualizar", "--fontes", "sanepar", "--config", str(config_filtro)]
    )
    assert resultado.output.strip().splitlines()[-1].startswith("resultado: ")
    frescor = [r for r in metricas.registros if r.nome == "FrescorHoras"]
    # pncp (coletado pela Lambda) e a fonte escolhida, no começo e no fim; nada da Compesa.
    assert [r.propriedades["fonte"] for r in frescor] == ["pncp", "sanepar"] * 2
    assert all(r.dimensoes == {} for r in frescor)
    assert frescor[0].valor == 9999.0
    assert resultado.output.count('"evento": "frescor_medido"') == 4


def test_atualizar_registra_execucao_ok(
    fabrica: sessionmaker[Session],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
    metricas: EmissorEmMemoria,
) -> None:
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})
    assert _atualizar_pncp(config_filtro, monkeypatch).exit_code == 0
    assert metricas.valores("ExecucaoOk") == [1.0]
    # As datas das janelas vêm do relógio congelado (`cli._agora`), mas `concluida_em` vem do
    # now() do banco (relógio real). A segunda execução só recoleta a janela de hoje se ela
    # foi concluída antes do fim do dia: ancorar a conclusão em AGORA tira a dependência da
    # hora real (com o relógio real já depois de 27/11 00:00 BRT, nada seria reaberto).
    with fabrica() as s:
        s.execute(update(ColetaJanela).values(concluida_em=AGORA))
        s.commit()
    assert _atualizar_pncp(config_filtro, monkeypatch, roteiro={}).exit_code == 1
    assert metricas.valores("ExecucaoOk") == [1.0, 0.0]


def test_auditar_imprime_frescor_sem_emitir_metrica(
    fabrica: sessionmaker[Session],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
    metricas: EmissorEmMemoria,
) -> None:
    """O auditar é manual: a métrica (e o alarme de frescor) é só do `atualizar` agendado."""
    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})
    _cobrir_pncp(fabrica)
    resultado = _auditar(config_filtro)
    assert [r for r in metricas.registros if r.nome == "FrescorHoras"] == []
    assert "frescor pncp: " in resultado.output


class _ExportadorQuebrado:
    def export(self, spans: Any) -> Any:
        raise RuntimeError("X-Ray fora do ar")

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        raise RuntimeError("X-Ray fora do ar")

    def shutdown(self) -> None:
        raise RuntimeError("X-Ray fora do ar")


class _CloudWatchQuebrado:
    def put_metric_data(self, **_: Any) -> None:
        raise RuntimeError("PutMetricData recusado")


def test_telemetria_quebrada_nao_muda_nada(
    engine: Engine,
    fabrica: sessionmaker[Session],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from radar.comum import metricas as modulo_metricas
    from radar.comum import telemetria, telemetria_sdk

    monkeypatch.setattr(cli, "fontes_retrato", lambda: {})
    limpo = _atualizar_pncp(config_filtro, monkeypatch)
    esperado = (limpo.exit_code, _resumo_editais(fabrica))
    _truncar(engine)

    monkeypatch.setenv("RADAR_TELEMETRIA", "xray")
    monkeypatch.setenv("RADAR_METRICAS", "cloudwatch")
    monkeypatch.setattr(telemetria_sdk, "exportador_xray", lambda regiao: _ExportadorQuebrado())
    monkeypatch.setattr(
        modulo_metricas.EmissorCloudWatch, "_obter_cliente", lambda self: _CloudWatchQuebrado()
    )
    quebrado = _atualizar_pncp(config_filtro, monkeypatch)

    assert (quebrado.exit_code, _resumo_editais(fabrica)) == esperado
    assert quebrado.output.strip().splitlines()[-1] == "resultado: sem falhas"
    # O processo desliga o que ligou: nada vaza para o próximo teste.
    assert modulo_metricas.emissor_atual() is None
    assert telemetria.traceparent_atual() is None


def test_tcepe_sondar_imprime_status_e_sai_1_sem_resposta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    def transporte(requisicao: httpx.Request) -> httpx.Response:
        if "falha" in str(requisicao.url):
            raise httpx.ConnectTimeout("sem rota")
        return httpx.Response(200, content=b"[" + b"0," * 100 + b"0]")

    original = httpx.Client

    def cliente(*args: object, **kwargs: object) -> httpx.Client:
        return original(*args, transport=httpx.MockTransport(transporte), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", cliente)
    ok = runner.invoke(cli.app, ["tcepe", "sondar", "--url", "https://exemplo/ok"])
    assert ok.exit_code == 0, ok.output
    assert "tcepe: HTTP 200" in ok.output
    falha = runner.invoke(cli.app, ["tcepe", "sondar", "--url", "https://exemplo/falha"])
    assert falha.exit_code == 1
    assert "tcepe: sem resposta" in falha.output
