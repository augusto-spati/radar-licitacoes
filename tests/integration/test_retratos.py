"""Motor de retratos: cortesia, mínimo por fonte, falha sem reconciliar e a ponte da cli."""

import json
import logging
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from radar import cli
from radar.coleta import (
    ClienteEducado,
    ErroFonte,
    PaginaColetada,
    executar_retrato,
    ids_do_retrato,
    ler_paginas_nao_processadas,
    ultimo_retrato,
)
from radar.coleta.modelos import ColetaJanela, ColetaRetrato, PaginaBruta
from radar.coleta.retratos import CHAVE_RETRATOS, USER_AGENT, chave_da_fonte
from radar.comum import Base, fabrica_sessao
from radar.editais import registrar_normalizados
from radar.editais.modelos import Edital

FUSO = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 11, 26, 12, 0, tzinfo=FUSO)
HOJE = AGORA.date()
runner = CliRunner()


def _normalizado(fonte: str, id_na_fonte: str, uf: str = "PR") -> dict[str, Any]:
    return {
        "fonte": fonte,
        "id_na_fonte": id_na_fonte,
        "objeto": f"Pesquisa de vazamentos {id_na_fonte}",
        "orgao_nome": "Companhia de Saneamento",
        "uf": uf,
        "modalidade_nome": "Licitação Eletrônica",
        "situacao_original": "Abertura",
        "situacao_normalizada": "aberta",
        "data_encerramento_proposta": "2026-12-10T09:00:00",
        "anexos": [],
        "bruto": {"id": id_na_fonte},
    }


class FonteFalsa:
    """Cada página da lista é um JSON {"ids": [...], "ufs": {...}} servido em /p<N>.

    `extrair` monta um normalizado por id; a uf pode ser trocada para forçar um inválido.
    """

    marcador_bloqueio: str | None = None

    def __init__(self, nome: str, paginas: int, minimo_registros: int = 1) -> None:
        self.nome = nome
        self.minimo_registros = minimo_registros
        self.paginas = paginas

    def coletar(self, cliente: ClienteEducado) -> Iterator[PaginaColetada]:
        for numero in range(1, self.paginas + 1):
            url = f"https://{self.nome}.test/p{numero}"
            resposta = cliente.get(url)
            yield PaginaColetada(url=url, formato="html", texto=resposta.text)

    def extrair(self, paginas: list[PaginaColetada]) -> list[dict[str, Any]]:
        saida = []
        for pagina in paginas:
            dados = json.loads(pagina.texto)
            for id_na_fonte in dados["ids"]:
                uf = dados.get("ufs", {}).get(id_na_fonte, "PR")
                saida.append(_normalizado(self.nome, id_na_fonte, uf))
        return saida


Rota = dict[str, httpx.Response | Exception]


def _http(rotas: Rota, chamadas: list[str] | None = None) -> httpx.Client:
    def tratar(requisicao: httpx.Request) -> httpx.Response:
        url = str(requisicao.url)
        if chamadas is not None:
            chamadas.append(url)
        resposta = rotas[url]
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    return httpx.Client(transport=httpx.MockTransport(tratar))


def _lista(*ids: str, ufs: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(200, text=json.dumps({"ids": list(ids), "ufs": ufs or {}}))


def _cliente(rotas: Rota, chamadas: list[str] | None = None) -> ClienteEducado:
    return ClienteEducado(_http(rotas, chamadas), dormir=lambda _s: None)


def _rotas(nome: str, *paginas: httpx.Response | Exception) -> Rota:
    return {f"https://{nome}.test/p{n}": r for n, r in enumerate(paginas, start=1)}


# --- ClienteEducado ---------------------------------------------------------------------


def test_intervalo_entre_requisicoes() -> None:
    esperas: list[float] = []
    vistos: list[str] = []

    def tratar(requisicao: httpx.Request) -> httpx.Response:
        vistos.append(requisicao.headers["User-Agent"])
        return httpx.Response(200, text="ok")

    cliente = ClienteEducado(
        httpx.Client(transport=httpx.MockTransport(tratar)), dormir=esperas.append
    )
    for _ in range(3):
        cliente.get("https://x.test/")

    assert esperas == [3.0, 3.0]  # a primeira requisição não espera
    assert vistos == [USER_AGENT] * 3


@pytest.mark.parametrize("status", [403, 429])
def test_403_para_sem_tentar_de_novo(status: int) -> None:
    chamadas: list[str] = []
    cliente = _cliente({"https://x.test/": httpx.Response(status)}, chamadas)

    with pytest.raises(ErroFonte, match=str(status)):
        cliente.get("https://x.test/")

    assert chamadas == ["https://x.test/"]


def test_marcador_de_bloqueio_vira_erro() -> None:
    cliente = ClienteEducado(
        _http({"https://x.test/": httpx.Response(200, text="<div class='g-recaptcha'>")}),
        dormir=lambda _s: None,
        marcador_bloqueio=r"recaptcha",
    )

    with pytest.raises(ErroFonte, match="bloqueio"):
        cliente.get("https://x.test/")


def test_erro_de_transporte_vira_erro_fonte() -> None:
    cliente = _cliente({"https://x.test/": httpx.ConnectTimeout("lento")})

    with pytest.raises(ErroFonte, match="ConnectTimeout"):
        cliente.get("https://x.test/")


def test_cliente_expoe_cookies() -> None:
    cliente = _cliente(
        {"https://x.test/": httpx.Response(200, headers={"Set-Cookie": "sessao=abc; Path=/"})}
    )
    cliente.get("https://x.test/")

    assert cliente.cookies.get("sessao") == "abc"


# --- executar_retrato ------------------------------------------------------------------


def _retratos(sessao: Session, fonte: str) -> list[ColetaRetrato]:
    return list(
        sessao.scalars(
            select(ColetaRetrato).where(ColetaRetrato.fonte == fonte).order_by(ColetaRetrato.id)
        )
    )


def test_retrato_concluido_grava_paginas_e_devolve_normalizados(sessao: Session) -> None:
    fonte = FonteFalsa("sanepar", paginas=2)
    cliente = _cliente(_rotas("sanepar", _lista("OE-1/26", "OE-2/26"), _lista("OE-3/26")))

    resumo = executar_retrato(sessao, fonte, cliente, AGORA)

    assert resumo.status == "concluido"
    assert resumo.registros == 3
    assert ids_do_retrato(resumo) == {"OE-1/26", "OE-2/26", "OE-3/26"}
    retrato = _retratos(sessao, "sanepar")[-1]
    assert (retrato.status, retrato.paginas, retrato.registros) == ("concluido", 2, 3)
    assert retrato.concluido_em is not None
    paginas = sessao.scalars(select(PaginaBruta).where(PaginaBruta.retrato_id == retrato.id)).all()
    assert [p.numero_pagina for p in paginas] == [1, 2]
    assert all(p.formato == "html" and p.conteudo_texto for p in paginas)
    assert all(p.janela_id is None and p.processada_em is not None for p in paginas)
    assert sessao.scalar(select(func.count()).select_from(Edital)) == 0  # papel da cli


def test_retrato_abaixo_do_minimo_e_falha(sessao: Session) -> None:
    fonte = FonteFalsa("sanepar", paginas=1, minimo_registros=5)
    cliente = _cliente(_rotas("sanepar", _lista("OE-1/26", "OE-2/26")))

    resumo = executar_retrato(sessao, fonte, cliente, AGORA)

    assert resumo.status == "falha"
    assert resumo.erro == "abaixo do minimo: 2 < 5"
    assert resumo.normalizados == []
    assert ids_do_retrato(resumo) == set()
    assert _retratos(sessao, "sanepar")[-1].status == "falha"


def test_pagina_com_erro_e_falha(sessao: Session) -> None:
    chamadas: list[str] = []
    fonte = FonteFalsa("sanepar", paginas=3)
    rotas = _rotas("sanepar", _lista("OE-1/26"), httpx.Response(500), _lista("OE-3/26"))

    resumo = executar_retrato(sessao, fonte, _cliente(rotas, chamadas), AGORA)

    assert resumo.status == "falha"
    assert resumo.erro is not None and "500" in resumo.erro
    assert len(chamadas) == 2  # para na primeira falha
    retrato = _retratos(sessao, "sanepar")[-1]
    assert (retrato.status, retrato.paginas) == ("falha", 1)


def test_retrato_encolheu_loga(sessao: Session, caplog: pytest.LogCaptureFixture) -> None:
    fonte = FonteFalsa("sanepar", paginas=1)
    ids = [f"OE-{n}/26" for n in range(10)]
    executar_retrato(sessao, fonte, _cliente(_rotas("sanepar", _lista(*ids))), AGORA)

    with caplog.at_level(logging.WARNING):
        resumo = executar_retrato(
            sessao, fonte, _cliente(_rotas("sanepar", _lista(*ids[:4]))), AGORA
        )

    assert resumo.status == "concluido"
    assert resumo.registros == 4
    assert "retrato_encolheu" in [r.getMessage() for r in caplog.records]


def test_retrato_encolheu_ignora_retrato_em_falha_como_base(
    sessao: Session, caplog: pytest.LogCaptureFixture
) -> None:
    # concluido (10) -> falha (0 registros) -> concluido (4): a base é o 10, não o da falha.
    fonte = FonteFalsa("sanepar", paginas=1)
    ids = [f"OE-{n}/26" for n in range(10)]
    executar_retrato(sessao, fonte, _cliente(_rotas("sanepar", _lista(*ids))), AGORA)
    falha = executar_retrato(sessao, fonte, _cliente(_rotas("sanepar", httpx.Response(403))), AGORA)
    assert falha.status == "falha"

    with caplog.at_level(logging.WARNING):
        resumo = executar_retrato(
            sessao, fonte, _cliente(_rotas("sanepar", _lista(*ids[:4]))), AGORA
        )

    assert resumo.status == "concluido"
    encolheu = [r for r in caplog.records if r.getMessage() == "retrato_encolheu"]
    assert [getattr(r, "anterior", None) for r in encolheu] == [10]


def test_minimo_conta_ids_distintos(sessao: Session) -> None:
    # Seletor de id quebrado: três linhas, um id só.
    fonte = FonteFalsa("sanepar", paginas=1, minimo_registros=2)
    cliente = _cliente(_rotas("sanepar", _lista("OE-1/26", "OE-1/26", "OE-1/26")))

    resumo = executar_retrato(sessao, fonte, cliente, AGORA)

    assert resumo.status == "falha"
    assert resumo.erro == "abaixo do minimo: 1 < 2"


def test_retrato_ocupado_por_outro_processo_nao_roda(sessao: Session, engine: Engine) -> None:
    chamadas: list[str] = []
    fonte = FonteFalsa("sanepar", paginas=1)
    with engine.connect() as outra:
        outra.scalar(select(func.pg_advisory_lock(CHAVE_RETRATOS, chave_da_fonte("sanepar"))))
        try:
            resumo = executar_retrato(
                sessao, fonte, _cliente(_rotas("sanepar", _lista("OE-1/26")), chamadas), AGORA
            )
        finally:
            outra.scalar(select(func.pg_advisory_unlock(CHAVE_RETRATOS, chave_da_fonte("sanepar"))))

    assert resumo.status == "ocupado"
    assert chamadas == []
    assert _retratos(sessao, "sanepar") == []


def test_retrato_em_andamento_que_sobrou_vira_falha(sessao: Session) -> None:
    sessao.add(
        ColetaRetrato(fonte="sanepar", status="em_andamento", iniciado_em=AGORA, paginas=0,
                      registros=0)
    )  # fmt: skip
    sessao.commit()
    fonte = FonteFalsa("sanepar", paginas=1)

    executar_retrato(sessao, fonte, _cliente(_rotas("sanepar", _lista("OE-1/26"))), AGORA)

    status = [(r.status, r.erro) for r in _retratos(sessao, "sanepar")]
    assert status == [("falha", "interrompido"), ("concluido", None)]


def test_ultimo_retrato_ignora_o_em_andamento(sessao: Session) -> None:
    fonte = FonteFalsa("sanepar", paginas=1)
    executar_retrato(sessao, fonte, _cliente(_rotas("sanepar", _lista("OE-1/26"))), AGORA)
    sessao.add(
        ColetaRetrato(fonte="sanepar", status="em_andamento", iniciado_em=AGORA, paginas=0,
                      registros=0)
    )  # fmt: skip
    sessao.commit()

    info = ultimo_retrato(sessao, "sanepar")

    assert info is not None
    assert (info.status, info.registros, info.iniciado_em) == ("concluido", 1, AGORA)
    assert ultimo_retrato(sessao, "compesa") is None


def test_paginas_de_retrato_nao_entram_na_fila_do_pncp(sessao: Session) -> None:
    # Uma de retrato concluído (marcada processada) e uma de retrato em falha (não marcada).
    fonte = FonteFalsa("sanepar", paginas=2)
    executar_retrato(
        sessao, fonte, _cliente(_rotas("sanepar", _lista("OE-1/26"), _lista("OE-2/26"))), AGORA
    )
    executar_retrato(
        sessao, fonte, _cliente(_rotas("sanepar", _lista("OE-1/26"), httpx.Response(403))), AGORA
    )

    assert ler_paginas_nao_processadas(sessao) == []


# --- esquema --------------------------------------------------------------------------


def _retrato_id(sessao: Session) -> int:
    retrato = ColetaRetrato(
        fonte="sanepar", status="concluido", iniciado_em=AGORA, paginas=0, registros=0
    )
    sessao.add(retrato)
    sessao.flush()
    return retrato.id


def _janela_id(sessao: Session) -> int:
    janela = ColetaJanela(
        endpoint="publicacao", data_ini=HOJE, data_fim=HOJE, modalidade=6, status="pendente",
        paginas=0, registros=0,
    )  # fmt: skip
    sessao.add(janela)
    sessao.flush()
    return janela.id


def _pagina(**campos: Any) -> PaginaBruta:
    base: dict[str, Any] = {"numero_pagina": 1, "coletada_em": AGORA, "formato": "json"}
    return PaginaBruta(**(base | campos))


@pytest.mark.parametrize(
    "montar",
    [
        pytest.param(lambda j, r: _pagina(conteudo={}), id="sem-janela-nem-retrato"),
        pytest.param(lambda j, r: _pagina(janela_id=j, retrato_id=r, conteudo={}), id="os-dois"),
        pytest.param(lambda j, r: _pagina(retrato_id=r, formato="html"), id="html-sem-texto"),
        pytest.param(
            lambda j, r: _pagina(janela_id=j, conteudo_texto="{}"), id="json-sem-conteudo"
        ),
        pytest.param(
            lambda j, r: _pagina(retrato_id=r, formato="xml", conteudo_texto="<a/>"),
            id="formato-desconhecido",
        ),
        pytest.param(
            lambda j, r: _pagina(janela_id=j, formato="html", conteudo_texto="<a/>"),
            id="janela-nao-json",
        ),
    ],
)
def test_pagina_bruta_check_janela_ou_retrato(
    sessao: Session, montar: Callable[[int, int], PaginaBruta]
) -> None:
    janela_id, retrato_id = _janela_id(sessao), _retrato_id(sessao)
    sessao.add(montar(janela_id, retrato_id))

    with pytest.raises(IntegrityError):
        sessao.flush()


def test_pagina_bruta_aceita_janela_json_e_retrato_html(sessao: Session) -> None:
    janela_id, retrato_id = _janela_id(sessao), _retrato_id(sessao)
    sessao.add(_pagina(janela_id=janela_id, conteudo={"data": []}))
    sessao.add(_pagina(retrato_id=retrato_id, formato="html", conteudo_texto="<tr/>"))
    sessao.add(_pagina(retrato_id=retrato_id, numero_pagina=2, formato="js", conteudo_texto="x"))

    sessao.flush()


def test_coleta_retrato_status_checado(sessao: Session) -> None:
    sessao.add(
        ColetaRetrato(fonte="sanepar", status="xpto", iniciado_em=AGORA, paginas=0, registros=0)
    )

    with pytest.raises(IntegrityError):
        sessao.flush()


# --- cli ------------------------------------------------------------------------------


@pytest.fixture
def fabrica(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    fabrica = fabrica_sessao(engine)
    monkeypatch.setattr(cli, "_criar_sessao", lambda: fabrica())
    monkeypatch.setattr(cli, "_agora", lambda: AGORA)
    yield fabrica
    tabelas = ", ".join(tabela.name for tabela in Base.metadata.sorted_tables)
    with engine.begin() as conexao:
        conexao.execute(text(f"TRUNCATE {tabelas} RESTART IDENTITY CASCADE"))


@pytest.fixture
def config_filtro(tmp_path: Path) -> Path:
    caminho = tmp_path / "filtro.toml"
    caminho.write_text(
        'ufs = ["PR"]\nmodalidades = [6]\n\n[linhas]\nperdas = ["vazamento"]\n', encoding="utf-8"
    )
    return caminho


def _registrar_fontes(
    monkeypatch: pytest.MonkeyPatch, fontes: list[FonteFalsa], rotas: Rota
) -> list[str]:
    chamadas: list[str] = []
    monkeypatch.setattr(cli, "_fontes_retrato", lambda: fontes)
    monkeypatch.setattr(cli, "_criar_cliente_retrato", lambda _fonte: _cliente(rotas, chamadas))
    return chamadas


def _situacoes(fabrica: sessionmaker[Session], fonte: str) -> dict[str, str]:
    with fabrica() as sessao:
        linhas = sessao.execute(
            select(Edital.id_na_fonte, Edital.situacao_normalizada).where(Edital.fonte == fonte)
        ).all()
    return dict(linhas)


def _ja_abertos(fabrica: sessionmaker[Session], fonte: str, *ids: str) -> None:
    with fabrica() as sessao:
        registrar_normalizados(sessao, [_normalizado(fonte, i) for i in ids])
        sessao.commit()


def _atualizar(config: Path, fontes: str) -> Any:
    return runner.invoke(cli.app, ["atualizar", "--fontes", fontes, "--config", str(config)])


def test_retrato_concluido_registra_e_reconcilia(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ja_abertos(fabrica, "sanepar", "OE-1/26", "OE-9/26", "OE-8/26")
    # OE-8 continua na lista, mas com uf inválida: foi listado, então não é encerrado.
    pagina = _lista("OE-1/26", "OE-2/26", "OE-8/26", ufs={"OE-8/26": "PRX"})
    _registrar_fontes(monkeypatch, [FonteFalsa("sanepar", 1)], _rotas("sanepar", pagina))

    resultado = _atualizar(config_filtro, "sanepar")

    assert resultado.exit_code == 0, resultado.output
    assert _situacoes(fabrica, "sanepar") == {
        "OE-1/26": "aberta",
        "OE-2/26": "aberta",
        "OE-8/26": "aberta",
        "OE-9/26": "encerrada",
    }
    with fabrica() as sessao:
        assert sessao.scalar(select(func.count()).select_from(ColetaJanela)) == 0  # sem pncp


def test_retrato_abaixo_do_minimo_e_falha_e_nao_reconcilia(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review Focus 1: o layout mudou e os seletores não acham nada.
    _ja_abertos(fabrica, "sanepar", "OE-1/26", "OE-2/26")
    _registrar_fontes(
        monkeypatch, [FonteFalsa("sanepar", 1, minimo_registros=3)], _rotas("sanepar", _lista())
    )

    resultado = _atualizar(config_filtro, "sanepar")

    assert resultado.exit_code == 1
    assert "abaixo do minimo: 0 < 3" in resultado.output
    assert _situacoes(fabrica, "sanepar") == {"OE-1/26": "aberta", "OE-2/26": "aberta"}


def test_pagina_com_erro_e_falha_e_nao_reconcilia(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review Focus 2: a página do meio dá erro; nada é gravado nem encerrado.
    _ja_abertos(fabrica, "sanepar", "OE-1/26", "OE-9/26")
    # OE-5 é novo e já foi lido na página 1: não pode ser gravado.
    rotas = _rotas("sanepar", _lista("OE-5/26"), httpx.Response(503), _lista("OE-3/26"))
    _registrar_fontes(monkeypatch, [FonteFalsa("sanepar", 3)], rotas)

    resultado = _atualizar(config_filtro, "sanepar")

    assert resultado.exit_code == 1
    assert _situacoes(fabrica, "sanepar") == {"OE-1/26": "aberta", "OE-9/26": "aberta"}


def test_retrato_vazio_nao_reconcilia(
    fabrica: sessionmaker[Session],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Guarda da cli: mesmo concluído (mínimo 0), retrato sem ids não encerra ninguém.
    _ja_abertos(fabrica, "sanepar", "OE-1/26")
    _registrar_fontes(
        monkeypatch, [FonteFalsa("sanepar", 1, minimo_registros=0)], _rotas("sanepar", _lista())
    )
    monkeypatch.setattr(cli, "configurar_logs", lambda: None)

    with caplog.at_level(logging.WARNING):
        resultado = _atualizar(config_filtro, "sanepar")

    assert resultado.exit_code == 0, resultado.output
    assert "reconciliacao_ignorada" in [r.getMessage() for r in caplog.records]
    assert _situacoes(fabrica, "sanepar") == {"OE-1/26": "aberta"}


def test_fonte_falha_isolada_saida_1(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rotas = _rotas("sanepar", httpx.Response(403)) | _rotas("compesa", _lista("2026LC1"))
    fontes = [FonteFalsa("sanepar", 1), FonteFalsa("compesa", 1)]
    chamadas = _registrar_fontes(monkeypatch, fontes, rotas)

    resultado = _atualizar(config_filtro, "sanepar,compesa")

    assert resultado.exit_code == 1
    assert chamadas == ["https://sanepar.test/p1", "https://compesa.test/p1"]
    assert _situacoes(fabrica, "compesa") == {"2026LC1": "aberta"}
    with fabrica() as sessao:
        assert ultimo_retrato(sessao, "sanepar").status == "falha"  # type: ignore[union-attr]
        assert ultimo_retrato(sessao, "compesa").status == "concluido"  # type: ignore[union-attr]


def test_erro_inesperado_no_registro_e_isolado(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ja_abertos(fabrica, "sanepar", "OE-9/26")
    rotas = _rotas("sanepar", _lista("OE-1/26")) | _rotas("compesa", _lista("2026LC1"))
    _registrar_fontes(monkeypatch, [FonteFalsa("sanepar", 1), FonteFalsa("compesa", 1)], rotas)
    original = cli.registrar_normalizados

    def registrar_com_erro(sessao: Session, registros: list[dict[str, Any]]) -> Any:
        if registros[0]["fonte"] == "sanepar":
            raise RuntimeError("boom")
        return original(sessao, registros)

    monkeypatch.setattr(cli, "registrar_normalizados", registrar_com_erro)

    resultado = _atualizar(config_filtro, "sanepar,compesa")

    assert resultado.exit_code == 1
    assert _situacoes(fabrica, "sanepar") == {"OE-9/26": "aberta"}
    assert _situacoes(fabrica, "compesa") == {"2026LC1": "aberta"}
    with fabrica() as sessao:
        sanepar = ultimo_retrato(sessao, "sanepar")
        compesa = ultimo_retrato(sessao, "compesa")
    assert sanepar is not None and sanepar.status == "falha"
    assert sanepar.erro == "registro: RuntimeError('boom')"
    assert compesa is not None and compesa.status == "concluido"


def test_segunda_rodada_durante_o_registro_e_ocupado(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # O lock da fonte vale até o fim do registro, não só durante a leitura das páginas.
    fonte = FonteFalsa("sanepar", 1)
    _registrar_fontes(monkeypatch, [fonte], _rotas("sanepar", _lista("OE-1/26")))
    original = cli.registrar_normalizados
    concorrentes: list[str] = []

    def registrar_com_concorrente(sessao: Session, registros: list[dict[str, Any]]) -> Any:
        with fabrica() as outra:
            cliente = _cliente(_rotas("sanepar", _lista("OE-2/26")))
            concorrentes.append(executar_retrato(outra, fonte, cliente, AGORA).status)
        return original(sessao, registros)

    monkeypatch.setattr(cli, "registrar_normalizados", registrar_com_concorrente)

    resultado = _atualizar(config_filtro, "sanepar")

    assert resultado.exit_code == 0, resultado.output
    assert concorrentes == ["ocupado"]
    assert _situacoes(fabrica, "sanepar") == {"OE-1/26": "aberta"}
    with fabrica() as sessao:
        assert len(_retratos(sessao, "sanepar")) == 1


def test_fontes_desconhecida_sai_com_2(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registrar_fontes(monkeypatch, [FonteFalsa("sanepar", 1)], {})

    resultado = _atualizar(config_filtro, "sanepar,xpto")

    assert resultado.exit_code == 2
    assert "xpto" in resultado.output


def test_processar_ignora_paginas_de_retrato(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registrar_fontes(monkeypatch, [FonteFalsa("sanepar", 1)], _rotas("sanepar", _lista("OE-1/26")))
    assert _atualizar(config_filtro, "sanepar").exit_code == 0
    registrar = []
    monkeypatch.setattr(cli, "registrar", lambda _s, r: registrar.append(r))

    resultado = runner.invoke(cli.app, ["processar"])

    assert resultado.exit_code == 0, resultado.output
    assert registrar == []


def _cobrir_pncp(fabrica: sessionmaker[Session]) -> None:
    with fabrica() as sessao:
        for dias_atras in (1, 2):
            dia = HOJE - timedelta(days=dias_atras)
            sessao.add(
                ColetaJanela(
                    endpoint="publicacao", data_ini=dia, data_fim=dia, modalidade=6,
                    status="concluida", paginas=1, registros=1,
                )
            )  # fmt: skip
        sessao.commit()


def _retrato(fabrica: sessionmaker[Session], fonte: str, status: str, horas_atras: float) -> None:
    with fabrica() as sessao:
        sessao.add(
            ColetaRetrato(
                fonte=fonte, status=status, iniciado_em=AGORA - timedelta(hours=horas_atras),
                paginas=1, registros=7, erro="HTTP 403" if status == "falha" else None,
            )
        )  # fmt: skip
        sessao.commit()


def _auditar(config: Path) -> Any:
    args = ["auditar", "--endpoint", "publicacao", "--dias", "2", "--config", str(config)]
    return runner.invoke(cli.app, args)


def test_auditar_mostra_retratos(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cobrir_pncp(fabrica)
    _registrar_fontes(monkeypatch, [FonteFalsa("sanepar", 1), FonteFalsa("compesa", 1)], {})
    _retrato(fabrica, "sanepar", "falha", horas_atras=30)
    _retrato(fabrica, "sanepar", "concluido", horas_atras=2)
    _retrato(fabrica, "compesa", "concluido", horas_atras=35)

    resultado = _auditar(config_filtro)

    assert resultado.exit_code == 0, resultado.output
    assert "retrato sanepar: concluido em 2026-11-26 10:00, 7 registros" in resultado.output
    assert "retrato compesa: concluido em 2026-11-25 01:00, 7 registros" in resultado.output


@pytest.mark.parametrize(
    ("status", "horas_atras", "trecho"),
    [
        ("falha", 1, "falha em 2026-11-26 11:00, 7 registros: HTTP 403"),
        ("concluido", 37, "mais de 36 h"),
    ],
)
def test_auditar_sai_com_1_quando_retrato_falhou_ou_e_velho(
    fabrica: sessionmaker[Session],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    horas_atras: float,
    trecho: str,
) -> None:
    _cobrir_pncp(fabrica)
    _registrar_fontes(monkeypatch, [FonteFalsa("sanepar", 1)], {})
    _retrato(fabrica, "sanepar", status, horas_atras)

    resultado = _auditar(config_filtro)

    assert resultado.exit_code == 1
    assert trecho in resultado.output


def test_auditar_sai_com_1_quando_fonte_nunca_rodou(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cobrir_pncp(fabrica)
    _registrar_fontes(monkeypatch, [FonteFalsa("sanepar", 1)], {})

    resultado = _auditar(config_filtro)

    assert resultado.exit_code == 1
    assert "retrato sanepar: nenhum" in resultado.output


def test_auditar_sem_fontes_de_retrato_nao_muda(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cobrir_pncp(fabrica)
    monkeypatch.setattr(cli, "_fontes_retrato", lambda: [])

    resultado = _auditar(config_filtro)

    assert resultado.exit_code == 0, resultado.output
    assert "retrato" not in resultado.output


def test_cli_registra_a_sanepar_real_e_atualizar_grava_com_a_fonte(
    fabrica: sessionmaker[Session], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from radar.coleta.sanepar import ler_lista

    assert [f.nome for f in cli._fontes_retrato()] == ["sanepar"]

    dados = Path(__file__).resolve().parent.parent / "dados" / "fontes"

    def ler(nome: str) -> str:
        return (dados / nome).read_text(encoding="utf-8").replace("57 editais", "40 editais")

    p1, p2, vazia = (
        ler("lista-oe-abertura-p1.html"),
        ler("lista-oe-abertura-p2.html"),
        ler("lista-oe-abertura-p4-vazia.html"),
    )
    detalhe = ler("detalhe-345-26-vazamentos.html")
    base = "https://www.sanepar.com.br"
    rotas: Rota = {
        f"{base}/licitacoes?Tipo=OE&Situacao=Abertura&page=0": httpx.Response(200, text=p1),
        f"{base}/licitacoes?Tipo=OE&Situacao=Abertura&page=1": httpx.Response(200, text=p2),
        f"{base}/licitacoes?Tipo=OE&Situacao=Abertura&page=2": httpx.Response(200, text=vazia),
        f"{base}/licitacoes?Tipo=OE&Situacao=Julgamento&page=0": httpx.Response(200, text=vazia),
    }
    for linha in ler_lista(p1).linhas + ler_lista(p2).linhas:
        rotas[f"{base}{linha.caminho_detalhe}"] = httpx.Response(200, text=detalhe)
    monkeypatch.setattr(cli, "_criar_cliente_retrato", lambda _fonte: _cliente(rotas))

    resultado = _atualizar(config_filtro, "sanepar")

    assert resultado.exit_code == 0, resultado.output
    situacoes = _situacoes(fabrica, "sanepar")
    assert len(situacoes) == 40 and "OE-352/26" in situacoes
    assert set(situacoes.values()) == {"aberta"}
