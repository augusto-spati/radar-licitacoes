import copy
import json
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from radar import cli
from radar.coleta.pncp import Pagina
from radar.comum import Base, fabrica_sessao
from radar.editais import ResultadoBusca, ResultadoItem, registrar
from radar.editais.modelos import Edital

RAIZ = Path(__file__).resolve().parent.parent.parent
AMOSTRA: dict[str, Any] = json.loads(
    (RAIZ / "tests" / "dados" / "registro_pregao.json").read_text(encoding="utf-8")
)
AGORA = datetime(2026, 11, 26, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
CNPJ = "12345678000199"
ID = f"{CNPJ}-1-000007/2026"

runner = CliRunner()


def _truncar(engine: Engine) -> None:
    tabelas = ", ".join(tabela.name for tabela in Base.metadata.sorted_tables)
    with engine.begin() as conexao:
        conexao.execute(text(f"TRUNCATE {tabelas} RESTART IDENTITY CASCADE"))


@pytest.fixture
def fabrica(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    fabrica = fabrica_sessao(engine)
    monkeypatch.setattr(cli, "_criar_sessao", lambda: fabrica())
    monkeypatch.setattr(cli, "_agora", lambda: AGORA)
    yield fabrica
    _truncar(engine)


@pytest.fixture
def config_filtro(tmp_path: Path) -> Path:
    caminho = tmp_path / "filtro.toml"
    caminho.write_text(
        'ufs = ["SP"]\nmodalidades = [6]\n\n[linhas]\nperdas = ["hidrometro"]\n',
        encoding="utf-8",
    )
    return caminho


def _registro(numero: str, objeto: str, encerramento: datetime) -> dict[str, Any]:
    registro = copy.deepcopy(AMOSTRA)
    registro["numeroControlePNCP"] = numero
    registro["objetoCompra"] = objeto
    registro["unidadeOrgao"]["ufSigla"] = "SP"
    registro["dataEncerramentoProposta"] = encerramento.replace(tzinfo=None).isoformat()
    return registro


class ClienteResultadosFalso:
    chamadas: list[tuple[str, int, int]] = []

    def itens_com_resultados(self, cnpj: str, ano: int, sequencial: int) -> list[dict[str, Any]]:
        ClienteResultadosFalso.chamadas.append((cnpj, ano, sequencial))
        return [
            {
                "numeroItem": 1,
                "descricao": "Hidrômetro",
                "valorTotal": 100,
                "orcamentoSigiloso": False,
                "situacaoCompraItemNome": "Homologado",
                "temResultado": True,
                "resultados": [
                    {
                        "niFornecedor": "11222333000144",
                        "nomeRazaoSocialFornecedor": "Acme Ltda",
                        "valorTotalHomologado": 80,
                        "quantidadeHomologada": 1,
                        "valorUnitarioHomologado": 80,
                    }
                ],
            }
        ]

    def fechar(self) -> None:
        pass


def test_resultados_atualizar_grava_e_nao_busca_de_novo(
    fabrica: sessionmaker[Session],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ClienteResultadosFalso.chamadas = []
    monkeypatch.setattr(cli, "_criar_cliente_resultados", ClienteResultadosFalso)
    with fabrica() as sessao:
        registrar(sessao, [_registro(ID, "Leitura de hidrometro", AGORA - timedelta(days=10))])
        sessao.commit()

    primeira = runner.invoke(cli.app, ["resultados", "atualizar", "--config", str(config_filtro)])
    assert primeira.exit_code == 0, primeira.output
    assert "resultados: 1 editais, 1 itens, 1 homologadas, 0 falhas" in primeira.output
    assert not any(linha.startswith("resultado:") for linha in primeira.output.splitlines())
    with fabrica() as sessao:
        assert sessao.scalar(select(func.count()).select_from(ResultadoBusca)) == 1
        assert sessao.scalar(select(func.count()).select_from(ResultadoItem)) == 1

    segunda = runner.invoke(cli.app, ["resultados", "atualizar", "--config", str(config_filtro)])
    assert segunda.exit_code == 0, segunda.output
    assert "resultados: 0 editais" in segunda.output
    assert ClienteResultadosFalso.chamadas == [(CNPJ, 2026, 7)]


def test_resultados_atualizar_falha_de_um_edital_segue_e_sai_com_1(
    fabrica: sessionmaker[Session],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Quebrado(ClienteResultadosFalso):
        def itens_com_resultados(
            self, cnpj: str, ano: int, sequencial: int
        ) -> list[dict[str, Any]]:
            if sequencial == 8:
                raise RuntimeError("falhou")
            return super().itens_com_resultados(cnpj, ano, sequencial)

    monkeypatch.setattr(cli, "_criar_cliente_resultados", Quebrado)
    with fabrica() as sessao:
        registrar(
            sessao,
            [
                _registro(
                    f"{CNPJ}-1-{n:06d}/2026", "Leitura de hidrometro", AGORA - timedelta(days=10)
                )
                for n in (7, 8)
            ],
        )
        sessao.commit()
    resultado = runner.invoke(cli.app, ["resultados", "atualizar", "--config", str(config_filtro)])
    assert resultado.exit_code == 1
    assert "resultados: 1 editais, 1 itens, 1 homologadas, 1 falhas" in resultado.output
    assert "resultado:" not in resultado.output.replace("resultados:", "")
    # O edital que falhou fica marcado como buscado: não volta amanhã ao topo da fila.
    segunda = runner.invoke(cli.app, ["resultados", "atualizar", "--config", str(config_filtro)])
    assert segunda.exit_code == 0, segunda.output
    assert "resultados: 0 editais" in segunda.output


def test_resultados_atualizar_para_apos_5_falhas_seguidas_do_pncp(
    fabrica: sessionmaker[Session],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from radar.coleta import ErroResultados

    class Fora(ClienteResultadosFalso):
        def itens_com_resultados(
            self, cnpj: str, ano: int, sequencial: int
        ) -> list[dict[str, Any]]:
            # O 1º (o mais recente) dá certo; depois, o PNCP cai de vez.
            if sequencial == 20:
                return super().itens_com_resultados(cnpj, ano, sequencial)
            ClienteResultadosFalso.chamadas.append((cnpj, ano, sequencial))
            raise ErroResultados("503")

    ClienteResultadosFalso.chamadas = []
    monkeypatch.setattr(cli, "_criar_cliente_resultados", Fora)
    with fabrica() as sessao:
        registrar(
            sessao,
            [
                _registro(
                    f"{CNPJ}-1-{n:06d}/2026",
                    "Leitura de hidrometro",
                    AGORA - timedelta(days=30 - n),
                )
                for n in range(10, 21)
            ],
        )
        sessao.commit()
    resultado = runner.invoke(cli.app, ["resultados", "atualizar", "--config", str(config_filtro)])
    assert resultado.exit_code == 1
    assert [s for _, _, s in ClienteResultadosFalso.chamadas] == [20, 19, 18, 17, 16, 15]
    assert "resultados: 1 editais, 1 itens, 1 homologadas, 5 falhas" in resultado.output
    assert "interrompida após 5 falhas seguidas do PNCP" in resultado.output


class ClientePNCPFalso:
    def buscar(
        self,
        endpoint: str,
        ini: date,
        fim: date,
        modalidade: int,
        pagina: int,
        uf: str | None = None,
    ) -> Pagina:
        registros = [
            _registro(ID, "Leitura de hidrometro", AGORA),
            _registro(f"{CNPJ}-1-000009/2026", "Obra de ponte", AGORA),
        ]
        return Pagina(registros=registros, total_paginas=1, numero_pagina=pagina, bruto={})


def test_historico_carregar_grava_so_o_que_casa(
    fabrica: sessionmaker[Session],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_criar_cliente", ClientePNCPFalso)
    resultado = runner.invoke(
        cli.app, ["historico", "carregar", "--meses", "2", "--config", str(config_filtro)]
    )
    assert resultado.exit_code == 0, resultado.output
    assert (
        "historico: 2 combinações (2 concluídas, 0 falhas), 2 páginas, 4 lidos, 1 gravados"
        in resultado.output
    )
    assert "resultado:" not in resultado.output
    with fabrica() as sessao:
        assert list(sessao.scalars(select(Edital.id_na_fonte))) == [ID]


def test_historico_mes_invalido(config_filtro: Path) -> None:
    resultado = runner.invoke(
        cli.app,
        ["historico", "carregar", "--refazer-mes", "2026-13", "--config", str(config_filtro)],
    )
    assert resultado.exit_code == 2
