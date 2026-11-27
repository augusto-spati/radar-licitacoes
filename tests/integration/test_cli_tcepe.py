"""`radar tcepe atualizar` com cliente falso montado nas amostras reais do TCE-PE."""

import json
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from radar import cli
from radar.coleta import ErroTcepe, NumeroSemBusca
from radar.comum import Base, fabrica_sessao
from radar.editais import ResultadoExterno
from radar.precos import PrecoItem

DADOS = Path(__file__).resolve().parent.parent / "dados" / "tcepe"
AGORA = datetime(2026, 11, 28, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
runner = CliRunner()
COMPESA = "09769035000164"


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


# --- Revisão final: número não numérico, limpeza da listagem e cortesia ---


def test_numero_que_nao_serve_para_buscar_contrato_grava_o_estimado_sem_falha(
    fabrica: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class SemNumero(ClienteTcepeFalso):
        def processos(self, ano: int) -> list[dict[str, Any]]:
            base = _ler("processos")[0]  # Pedra
            # Mais processos sem número que o disjuntor aguenta: nenhum conta como falha.
            return [
                {**base, "processoContratacaoId": str(60116414 + n), "numeroContratacao": " "}
                for n in range(FALHAS + 1)
            ]

        def orcamento(self, processo_id: int | str) -> list[dict[str, Any]]:
            self.orcamentos.append(str(processo_id))
            return _ler("orcamento_60116414")

        def instrumentos(self, uj_id: int | str, numero: str, ano: int) -> list[dict[str, Any]]:
            if not numero.strip():
                raise NumeroSemBusca("processo sem número")
            raise AssertionError("não devia buscar")

    ClienteTcepeFalso.instancias = []
    monkeypatch.setattr(cli, "_criar_cliente_tcepe", SemNumero)
    monkeypatch.setattr(cli, "configurar_logs", lambda: None)  # deixa o caplog ver o log
    with caplog.at_level("WARNING"):
        resultado = runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"])
    assert resultado.exit_code == 0, resultado.output
    assert f"tcepe: {FALHAS + 1} processos de saneamento" in resultado.output
    assert "0 falhas" in resultado.output
    with fabrica() as sessao:
        linhas = sessao.scalars(select(PrecoItem)).all()
    assert linhas
    assert all(linha.preco_contratado_com_bdi is None for linha in linhas)
    assert "tcepe_sem_busca_de_contrato" in caplog.text


FALHAS = cli.FALHAS_SEGUIDAS_TCEPE


class Contador(ClienteTcepeFalso):
    """Conta as buscas de contrato; a listagem pode ser trocada entre as rodadas."""

    listagem: list[dict[str, Any]] | None = None
    orcamento_vazio: set[str] = set()
    buscas: list[str] = []

    def processos(self, ano: int) -> list[dict[str, Any]]:
        self.anos.append(ano)
        if ano != 2026:
            return []
        return Contador.listagem if Contador.listagem is not None else _ler("processos")

    def orcamento(self, processo_id: int | str) -> list[dict[str, Any]]:
        if str(processo_id) in Contador.orcamento_vazio:
            return [{"itemId": "1", "itemDescricao": "PLACA DE OBRA", "ValorUnitario": "10"}]
        return super().orcamento(processo_id)

    def instrumentos(self, uj_id: int | str, numero: str, ano: int) -> list[dict[str, Any]]:
        Contador.buscas.append(f"{uj_id}/{numero}")
        return super().instrumentos(uj_id, numero, ano)


def _por_processo(fabrica: sessionmaker[Session]) -> dict[str, int]:
    with fabrica() as sessao:
        chaves = sessao.scalars(select(PrecoItem.chave_origem)).all()
    contagem: dict[str, int] = {}
    for chave in chaves:
        contagem[chave.split(":")[0]] = contagem.get(chave.split(":")[0], 0) + 1
    return contagem


@pytest.fixture
def contador(monkeypatch: pytest.MonkeyPatch) -> type[Contador]:
    Contador.listagem = None
    Contador.orcamento_vazio = set()
    Contador.buscas = []
    ClienteTcepeFalso.instancias = []
    monkeypatch.setattr(cli, "_criar_cliente_tcepe", Contador)
    return Contador


@pytest.mark.parametrize(
    "mudanca",
    [
        {"estagioNome": "Revogado", "valorHomologado": "0"},
        {"estagioNome": "Anulado"},
        {"estagioNome": "Em andamento"},
        {"orcamentoSigiloso": "1"},
    ],
)
def test_processo_que_saiu_do_alvo_tem_as_linhas_apagadas(
    fabrica: sessionmaker[Session], contador: type[Contador], mudanca: dict[str, str]
) -> None:
    assert runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"]).exit_code == 0
    antes = _por_processo(fabrica)
    assert set(antes) == {"60116414", "60116598"}

    contador.listagem = [
        {**p, **mudanca} if p["processoContratacaoId"] == "60116414" else p
        for p in _ler("processos")
    ]
    resultado = runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"])

    assert resultado.exit_code == 0, resultado.output
    assert "1 processos removidos" in resultado.output
    assert _por_processo(fabrica) == {"60116598": antes["60116598"]}


def test_processo_que_ficou_sem_item_classificado_tem_as_linhas_apagadas(
    fabrica: sessionmaker[Session], contador: type[Contador]
) -> None:
    assert runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"]).exit_code == 0
    contador.orcamento_vazio = {"60116414"}

    resultado = runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"])

    assert resultado.exit_code == 0, resultado.output
    assert set(_por_processo(fabrica)) == {"60116598"}


def test_processo_ja_casado_com_o_mesmo_homologado_nao_busca_contrato_de_novo(
    fabrica: sessionmaker[Session], contador: type[Contador]
) -> None:
    assert runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"]).exit_code == 0
    assert sorted(contador.buscas) == ["462/016", "802/4015"]
    with fabrica() as sessao:
        antes = {
            linha.chave_origem: linha.preco_contratado_com_bdi
            for linha in sessao.scalars(select(PrecoItem)).all()
        }
    assert any(v is not None for v in antes.values())

    contador.buscas = []
    assert runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"]).exit_code == 0
    assert contador.buscas == []
    with fabrica() as sessao:
        depois = {
            linha.chave_origem: linha.preco_contratado_com_bdi
            for linha in sessao.scalars(select(PrecoItem)).all()
        }
    assert depois == antes

    # Homologado mudou: busca de novo só esse.
    contador.listagem = [
        {**p, "valorHomologado": "580000.00"} if p["processoContratacaoId"] == "60116414" else p
        for p in _ler("processos")
    ]
    assert runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"]).exit_code == 0
    assert contador.buscas == ["462/016"]


def test_nao_segura_transacao_aberta_durante_a_rede(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """O Neon derruba transação ociosa: a 1ª coleta real caiu com `received 2 results from
    command 'ROLLBACK'` depois de minutos buscando contratos com a leitura ainda aberta."""
    sessoes: list[Session] = []

    def criar() -> Session:
        sessao = fabrica()
        sessoes.append(sessao)
        return sessao

    abertas: list[str] = []

    class Vigia(ClienteTcepeFalso):
        def _conferir(self, onde: str) -> None:
            abertas.extend(onde for s in sessoes if s.in_transaction())

        def orcamento(self, processo_id: int | str) -> list[dict[str, Any]]:
            self._conferir(f"orcamento {processo_id}")
            return super().orcamento(processo_id)

        def instrumentos(self, uj_id: int | str, numero: str, ano: int) -> list[dict[str, Any]]:
            self._conferir(f"instrumentos {numero}")
            return super().instrumentos(uj_id, numero, ano)

    monkeypatch.setattr(cli, "_criar_sessao", criar)
    monkeypatch.setattr(cli, "_criar_cliente_tcepe", Vigia)
    resultado = runner.invoke(cli.app, ["tcepe", "atualizar"])
    assert resultado.exit_code == 0, resultado.output
    assert abertas == []


# --- Histórico do órgão (7a.3): processos da Compesa no `resultado_externo` ---


class ClienteHistoricoFalso(ClienteTcepeFalso):
    """Só a Adutora do Agreste (Compesa, homologada, linha `redes`), com contrato e contratada."""

    def __init__(self) -> None:
        super().__init__()
        self.chamadas: list[str] = []
        self.estagio = "Concluído - Adjudicado/Homologado"

    def processos(self, ano: int) -> list[dict[str, Any]]:
        self.anos.append(ano)
        if ano != 2026:
            return []
        return [{**p, "estagioNome": self.estagio} for p in _ler("processos_compesa_historico")]

    def orcamento(self, processo_id: int | str) -> list[dict[str, Any]]:
        self.orcamentos.append(str(processo_id))
        return []  # o preço (7b) não é o assunto aqui

    def instrumentos(self, uj_id: int | str, numero: str, ano: int) -> list[dict[str, Any]]:
        self.chamadas.append(f"instrumentos:{numero}")
        return _ler("instrumentos_compesa_4044") if numero == "4044" else []

    def participantes_instrumento(self, instrumento_id: int | str) -> list[dict[str, Any]]:
        self.chamadas.append(f"participantes_instrumento:{instrumento_id}")
        return _ler(f"participantes_instrumento_{instrumento_id}")

    def participantes_processo(self, processo_id: int | str) -> list[dict[str, Any]]:
        self.chamadas.append(f"participantes_processo:{processo_id}")
        return _ler(f"participantes_processo_{processo_id}")


def _casos(fabrica: sessionmaker[Session]) -> list[ResultadoExterno]:
    with fabrica() as sessao:
        casos = list(sessao.scalars(select(ResultadoExterno)).all())
        sessao.expunge_all()
        return casos


def test_compesa_entra_no_historico_do_orgao_e_rodar_de_novo_nao_duplica(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    ClienteTcepeFalso.instancias = []
    monkeypatch.setattr(cli, "_criar_cliente_tcepe", ClienteHistoricoFalso)

    resultado = runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"])

    assert resultado.exit_code == 0, resultado.output
    assert "histórico do órgão: 1 casos (1 com vencedor), 0 removidos" in resultado.output
    # O prefixo "resultado:" alimenta um alarme: a linha da rodada nunca o usa.
    assert "resultado:" not in resultado.output.replace("resultados:", "")
    [caso] = _casos(fabrica)
    assert (caso.fonte, caso.id_na_fonte, caso.orgao_cnpj) == ("tce_pe", "60120073", COMPESA)
    assert caso.vencedor_ni == "10514594000102"
    assert caso.linhas == ["redes"]
    assert caso.desconto == Decimal("0.0703")
    cliente = ClienteTcepeFalso.instancias[0]
    assert isinstance(cliente, ClienteHistoricoFalso)
    assert cliente.chamadas == ["instrumentos:4044", "participantes_instrumento:60332570"]

    outra = runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"])

    assert outra.exit_code == 0, outra.output
    assert len(_casos(fabrica)) == 1
    # Mesmo homologado e vencedor já gravado: sem ir buscar o contrato de novo.
    segundo = ClienteTcepeFalso.instancias[1]
    assert isinstance(segundo, ClienteHistoricoFalso)
    assert segundo.chamadas == []


def test_sem_contrato_o_vencedor_vem_dos_participantes_do_processo(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    class SemContrato(ClienteHistoricoFalso):
        def instrumentos(self, uj_id: int | str, numero: str, ano: int) -> list[dict[str, Any]]:
            self.chamadas.append(f"instrumentos:{numero}")
            return []

    ClienteTcepeFalso.instancias = []
    monkeypatch.setattr(cli, "_criar_cliente_tcepe", SemContrato)
    resultado = runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"])
    assert resultado.exit_code == 0, resultado.output
    [caso] = _casos(fabrica)
    assert caso.vencedor_nome == "EMCOSA EMPRESA DE CONSTRUCOES E SANEAMENTO LTDA"


def test_processo_revogado_sai_do_historico(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    ClienteTcepeFalso.instancias = []
    monkeypatch.setattr(cli, "_criar_cliente_tcepe", ClienteHistoricoFalso)
    assert runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"]).exit_code == 0
    assert len(_casos(fabrica)) == 1

    class Revogado(ClienteHistoricoFalso):
        def __init__(self) -> None:
            super().__init__()
            self.estagio = "Concluído - Revogado"

    monkeypatch.setattr(cli, "_criar_cliente_tcepe", Revogado)
    resultado = runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"])

    assert resultado.exit_code == 0, resultado.output
    assert "histórico do órgão: 0 casos (0 com vencedor), 1 removidos" in resultado.output
    assert _casos(fabrica) == []


def test_falha_do_vencedor_conta_falha_e_nao_grava_o_caso(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Cai(ClienteHistoricoFalso):
        def participantes_instrumento(self, instrumento_id: int | str) -> list[dict[str, Any]]:
            raise ErroTcepe("fora do ar")

    ClienteTcepeFalso.instancias = []
    monkeypatch.setattr(cli, "_criar_cliente_tcepe", Cai)
    resultado = runner.invoke(cli.app, ["tcepe", "atualizar", "--anos", "2026"])
    assert resultado.exit_code == 1
    assert "1 falhas" in resultado.output
    assert _casos(fabrica) == []
