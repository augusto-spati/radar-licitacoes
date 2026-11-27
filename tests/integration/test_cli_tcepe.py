"""`radar tcepe atualizar` com cliente falso montado nas amostras reais do TCE-PE."""

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from radar import cli
from radar.coleta import ErroTcepe
from radar.comum import Base, fabrica_sessao
from radar.precos import PrecoItem

DADOS = Path(__file__).resolve().parent.parent / "dados" / "tcepe"
AGORA = datetime(2026, 11, 28, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
runner = CliRunner()


def _ler(nome: str) -> Any:
    return json.loads((DADOS / f"{nome}.json").read_text(encoding="utf-8"))


@pytest.fixture
def fabrica(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    fabrica = fabrica_sessao(engine)
    monkeypatch.setattr(cli, "_criar_sessao", lambda: fabrica())
    monkeypatch.setattr(cli, "_agora", lambda: AGORA)
    yield fabrica
    tabelas = ", ".join(tabela.name for tabela in Base.metadata.sorted_tables)
    with engine.begin() as conexao:
        conexao.execute(text(f"TRUNCATE {tabelas} RESTART IDENTITY CASCADE"))


class ClienteTcepeFalso:
    """Serve as amostras: Pedra (60116414) e Compesa (60116598) são de saneamento."""

    instancias: list["ClienteTcepeFalso"] = []

    def __init__(self) -> None:
        self.anos: list[int] = []
        self.orcamentos: list[str] = []
        self.fechado = False
        ClienteTcepeFalso.instancias.append(self)

    def processos(self, ano: int) -> list[dict[str, Any]]:
        self.anos.append(ano)
        return _ler("processos") if ano == 2026 else []

    def orcamento(self, processo_id: int | str) -> list[dict[str, Any]]:
        self.orcamentos.append(str(processo_id))
        return _ler(f"orcamento_{processo_id}")

    def instrumentos(self, uj_id: int | str, numero: str, ano: int) -> list[dict[str, Any]]:
        return [
            i
            for i in _ler("instrumentos")
            if i["unidadeJurisdicionadaId"] == str(uj_id)
            and i["NumeroProcessoContratacao"] == numero
        ]

    def itens_instrumento(self, instrumento_id: int | str) -> list[dict[str, Any]]:
        return _ler(f"itens_instrumento_{instrumento_id}")

    def fechar(self) -> None:
        self.fechado = True


def _contar(fabrica: sessionmaker[Session]) -> int:
    with fabrica() as sessao:
        return sessao.scalar(select(func.count()).select_from(PrecoItem)) or 0


def test_atualizar_grava_precos_e_e_idempotente(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    ClienteTcepeFalso.instancias = []
    monkeypatch.setattr(cli, "_criar_cliente_tcepe", ClienteTcepeFalso)
    resultado = runner.invoke(cli.app, ["tcepe", "atualizar"])
    assert resultado.exit_code == 0, resultado.output
    cliente = ClienteTcepeFalso.instancias[0]
    assert cliente.anos == [2026, 2025]  # ano corrente e anterior, a partir de _agora()
    assert cliente.fechado
    gravados = _contar(fabrica)
    assert gravados > 0
    assert f"tcepe: 2 processos de saneamento, {gravados} itens gravados, 0 falhas" in (
        resultado.output
    )
    assert "resultado:" not in resultado.output.replace("resultados:", "")
    outra = runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"])
    assert outra.exit_code == 0
    assert _contar(fabrica) == gravados


def test_atualizar_so_busca_orcamento_de_saneamento_homologado(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Misto(ClienteTcepeFalso):
        def processos(self, ano: int) -> list[dict[str, Any]]:
            brutos = _ler("processos")
            fora = {**brutos[0], "processoContratacaoId": "1", "objeto": "Merenda escolar"}
            aberto = {
                **brutos[0],
                "processoContratacaoId": "2",
                "estagioNome": "Em andamento",
                "valorHomologado": "0",
            }
            self.anos.append(ano)
            return [*brutos, fora, aberto]

    ClienteTcepeFalso.instancias = []
    monkeypatch.setattr(cli, "_criar_cliente_tcepe", Misto)
    resultado = runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"])
    assert resultado.exit_code == 0, resultado.output
    assert "1" not in ClienteTcepeFalso.instancias[0].orcamentos
    assert "2" not in ClienteTcepeFalso.instancias[0].orcamentos


def test_erro_no_meio_mantem_os_anteriores_nada_pela_metade_e_sai_com_1(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Cai(ClienteTcepeFalso):
        def itens_instrumento(self, instrumento_id: int | str) -> list[dict[str, Any]]:
            if str(instrumento_id) == "60314628":  # contrato do 2º processo (Compesa)
                raise ErroTcepe("fora do ar")
            return super().itens_instrumento(instrumento_id)

    ClienteTcepeFalso.instancias = []
    monkeypatch.setattr(cli, "_criar_cliente_tcepe", Cai)
    resultado = runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"])
    assert resultado.exit_code == 1
    assert "1 falhas" in resultado.output
    with fabrica() as sessao:
        processos = set(sessao.scalars(select(PrecoItem.processo)).all())
    # O processo que terminou antes da queda está gravado inteiro; o que caiu, nada.
    assert processos == {"462/016/2026"}
    assert "tcepe: 1 processos de saneamento" in resultado.output
    assert ClienteTcepeFalso.instancias[0].fechado


def test_disjuntor_apos_5_erros_seguidos_do_tcepe(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    chamadas: list[str] = []

    class Fora(ClienteTcepeFalso):
        def processos(self, ano: int) -> list[dict[str, Any]]:
            base = _ler("processos")[0]
            return [{**base, "processoContratacaoId": str(100 + n)} for n in range(10)]

        def orcamento(self, processo_id: int | str) -> list[dict[str, Any]]:
            chamadas.append(str(processo_id))
            raise ErroTcepe("503")

    monkeypatch.setattr(cli, "_criar_cliente_tcepe", Fora)
    resultado = runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026,2025"])
    assert resultado.exit_code == 1
    assert len(chamadas) == 5
    assert "0 processos de saneamento, 0 itens gravados, 5 falhas" in resultado.output
    assert "interrompida após 5 falhas seguidas do TCE-PE" in resultado.output
    assert _contar(fabrica) == 0


def test_anos_invalido_e_rejeitado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_criar_cliente_tcepe", ClienteTcepeFalso)
    resultado = runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "abc"])
    assert resultado.exit_code != 0
