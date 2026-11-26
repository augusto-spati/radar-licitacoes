import copy
import json
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fakes import ClienteFalso, como_cliente
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from radar import cli
from radar.coleta.modelos import ColetaJanela, PaginaBruta
from radar.coleta.pncp import Pagina
from radar.comum import Base, fabrica_sessao
from radar.editais import registrar
from radar.editais.modelos import Edital

RAIZ = Path(__file__).resolve().parent.parent.parent
AMOSTRA: dict[str, Any] = json.loads(
    (RAIZ / "tests" / "dados" / "registro_pregao.json").read_text(encoding="utf-8")
)
AGORA = datetime(2026, 11, 26, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
HOJE = AGORA.date()

runner = CliRunner()
_ARGS_AUDITAR = ["auditar", "--endpoint", "publicacao", "--dias", "2"]


@pytest.fixture
def fabrica(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    """O CLI faz commit de verdade, então limpa as tabelas ao fim em vez de usar savepoint."""
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
