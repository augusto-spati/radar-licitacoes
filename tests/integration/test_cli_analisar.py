import copy
import json
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from radar import cli
from radar.analise import AnaliseEdital, ClienteArquivosPNCP, ConfigIA, Pedido, Resposta
from radar.analise.custos import gasto_do_mes
from radar.analise.modelos import RespostaLLM
from radar.coleta.trava import obter_trava
from radar.comum import Base, fabrica_sessao
from radar.editais import registrar, registrar_normalizados

RAIZ = Path(__file__).resolve().parent.parent.parent
AMOSTRA: dict[str, Any] = json.loads(
    (RAIZ / "tests" / "dados" / "registro_pregao.json").read_text(encoding="utf-8")
)
PDF_TEXTO = (RAIZ / "tests" / "dados" / "ia" / "edital_texto.pdf").read_bytes()
AGORA = datetime(2026, 11, 26, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
BASE = "https://pncp.gov.br/pncp-api/v1"
ABERTO = "78200482000110-1-000298/2026"
OUTRO_ABERTO = "78200482000110-1-000299/2026"

runner = CliRunner()


def _truncar(engine: Engine) -> None:
    tabelas = ", ".join(tabela.name for tabela in Base.metadata.sorted_tables)
    with engine.begin() as conexao:
        conexao.execute(text(f"TRUNCATE {tabelas} RESTART IDENTITY CASCADE"))


class ProvedorFalso:
    chamadas = 0

    def gerar(self, pedido: Pedido) -> Resposta:
        ProvedorFalso.chamadas += 1
        return Resposta(
            texto='{"achados": []}', tokens_entrada=1000, tokens_saida=100, latencia_ms=1
        )


class Rede:
    def __init__(self) -> None:
        self.listagens: list[str] = []

    def tratar(self, requisicao: httpx.Request) -> httpx.Response:
        caminho = requisicao.url.path
        if caminho.endswith("/arquivos"):
            self.listagens.append(caminho)
            return httpx.Response(
                200,
                json=[
                    {
                        "sequencialDocumento": 1,
                        "url": f"{BASE}{caminho.removeprefix('/pncp-api/v1')}/1",
                        "titulo": "edital.pdf",
                        "tipoDocumentoNome": "Edital",
                        "statusAtivo": True,
                    }
                ],
            )
        return httpx.Response(200, content=PDF_TEXTO)


@pytest.fixture
def rede(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[sessionmaker[Session], Rede]]:
    fabrica = fabrica_sessao(engine)
    rede = Rede()
    ProvedorFalso.chamadas = 0
    monkeypatch.setattr(cli, "_criar_sessao", lambda: fabrica())
    monkeypatch.setattr(cli, "_agora", lambda: AGORA)

    def provedor(config: ConfigIA) -> ProvedorFalso:
        return ProvedorFalso()

    def cliente_arquivos() -> ClienteArquivosPNCP:
        http = httpx.Client(base_url=BASE, transport=httpx.MockTransport(rede.tratar))
        return ClienteArquivosPNCP(http, pausa_s=0, espera_base=0)

    monkeypatch.setattr(cli, "_criar_provedor_ia", provedor)
    monkeypatch.setattr(cli, "_criar_cliente_arquivos", cliente_arquivos)
    monkeypatch.setenv("RADAR_IA_PROVEDOR", "bedrock")
    monkeypatch.setenv("RADAR_DOCUMENTOS", str(tmp_path / "documentos"))
    monkeypatch.delenv("RADAR_CONFIG_IA", raising=False)
    monkeypatch.delenv("RADAR_CONFIG_PERFIL", raising=False)
    yield fabrica, rede
    _truncar(engine)


@pytest.fixture
def config_filtro(tmp_path: Path) -> Path:
    caminho = tmp_path / "filtro.toml"
    caminho.write_text(
        'ufs = ["SP", "PR"]\nmodalidades = [6]\n\n[linhas]\nperdas = ["hidrometro", "vazamento"]\n',
        encoding="utf-8",
    )
    return caminho


def _registro(
    numero: str, objeto: str = "Leitura de hidrômetros", encerramento: str = "2026-12-11T10:00:00"
) -> dict[str, Any]:
    registro = copy.deepcopy(AMOSTRA)
    registro["numeroControlePNCP"] = numero
    registro["objetoCompra"] = objeto
    registro["unidadeOrgao"]["ufSigla"] = "SP"
    registro["dataEncerramentoProposta"] = encerramento
    registro["situacaoCompraId"] = 1
    return registro


def _sanepar() -> dict[str, Any]:
    return {
        "fonte": "sanepar",
        "id_na_fonte": "OE-345/26",
        "objeto": "Pesquisa de vazamentos não visíveis",
        "orgao_nome": "Sanepar",
        "uf": "PR",
        "modalidade_nome": "Licitação Eletrônica",
        "situacao_original": "Abertura",
        "situacao_normalizada": "aberta",
        "data_encerramento_proposta": "2026-12-10T09:00:00",
        "bruto": {},
    }


def _popular(fabrica: sessionmaker[Session]) -> None:
    with fabrica() as sessao:
        registrar(
            sessao,
            [
                _registro(ABERTO),
                _registro(OUTRO_ABERTO),
                _registro("78200482000110-1-000300/2026", encerramento="2026-11-01T10:00:00"),
                _registro("78200482000110-1-000301/2026", objeto="Obra de ponte"),
            ],
        )
        registrar_normalizados(sessao, [_sanepar()])
        sessao.commit()


def _analisar(config_filtro: Path, *args: str) -> Any:
    return runner.invoke(cli.app, ["analisar", "--config", str(config_filtro), *args])


def _analisados(fabrica: sessionmaker[Session]) -> set[str]:
    with fabrica() as sessao:
        return set(sessao.scalars(select(AnaliseEdital.id_na_fonte)))


def test_analisar_so_pncp_aberto_que_casa(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path
) -> None:
    fabrica, _ = rede
    _popular(fabrica)
    resultado = _analisar(config_filtro)
    assert resultado.exit_code == 0, resultado.output
    assert _analisados(fabrica) == {ABERTO, OUTRO_ABERTO}


def test_saida_nao_tem_resultado_dois_pontos(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path
) -> None:
    fabrica, _ = rede
    _popular(fabrica)
    resultado = _analisar(config_filtro)
    assert "resultado:" not in resultado.output
    ultima = resultado.output.strip().splitlines()[-1]
    assert ultima.startswith("analise: 2 analisados, 0 pulados, 0 falhas, US$ ")


def test_analisar_pula_ja_analisado(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path
) -> None:
    fabrica, rede_falsa = rede
    _popular(fabrica)
    _analisar(config_filtro)
    rede_falsa.listagens.clear()
    chamadas = ProvedorFalso.chamadas

    resultado = _analisar(config_filtro)
    assert resultado.exit_code == 0, resultado.output
    assert "analise: 0 analisados, 2 pulados, 0 falhas" in resultado.output
    assert rede_falsa.listagens == []
    assert ProvedorFalso.chamadas == chamadas


def test_analisar_limite_e_refazer(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path
) -> None:
    fabrica, _ = rede
    _popular(fabrica)
    assert "analise: 1 analisados" in _analisar(config_filtro, "--limite", "1").output
    resultado = _analisar(config_filtro, "--refazer")
    assert "analise: 2 analisados, 0 pulados" in resultado.output
    with fabrica() as sessao:
        assert sessao.scalar(select(func.count()).select_from(AnaliseEdital)) == 2


def test_analisar_um_edital_encerrado(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path
) -> None:
    fabrica, _ = rede
    _popular(fabrica)
    resultado = _analisar(config_filtro, "--edital", "pncp~78200482000110-1-000300/2026")
    assert resultado.exit_code == 0, resultado.output
    assert _analisados(fabrica) == {"78200482000110-1-000300/2026"}


def test_edital_desconhecido_sai_2(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path
) -> None:
    assert _analisar(config_filtro, "--edital", "pncp~nada").exit_code == 2


def test_provedor_desligado_sai_2(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RADAR_IA_PROVEDOR", "desligado")
    resultado = _analisar(config_filtro)
    assert resultado.exit_code == 2
    assert "RADAR_IA_PROVEDOR=bedrock" in resultado.output


def test_teto_sai_1(
    rede: tuple[sessionmaker[Session], Rede],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fabrica, _ = rede
    _popular(fabrica)
    ia = (RAIZ / "config" / "ia.toml").read_text(encoding="utf-8")
    arquivo = tmp_path / "ia.toml"
    arquivo.write_text(ia.replace("teto_mensal_usd = 2.0", "teto_mensal_usd = 0.0001"), "utf-8")
    monkeypatch.setenv("RADAR_CONFIG_IA", str(arquivo))

    resultado = _analisar(config_filtro)
    assert resultado.exit_code == 1
    assert ProvedorFalso.chamadas == 0
    assert _analisados(fabrica) == set()
    assert "resultado:" not in resultado.output
    assert resultado.output.strip().splitlines()[-1].startswith("analise: 0 analisados")


def test_falha_de_um_edital_nao_para_os_outros(
    rede: tuple[sessionmaker[Session], Rede],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fabrica, rede_falsa = rede
    _popular(fabrica)
    original = rede_falsa.tratar

    def tratar(requisicao: httpx.Request) -> httpx.Response:
        if "/2026/298/" in requisicao.url.path:
            return httpx.Response(404)
        return original(requisicao)

    monkeypatch.setattr(rede_falsa, "tratar", tratar)
    resultado = _analisar(config_filtro)
    assert resultado.exit_code == 1
    assert "analise: 1 analisados, 0 pulados, 1 falhas" in resultado.output
    assert _analisados(fabrica) == {OUTRO_ABERTO}


def _ia_com(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **trocas: str) -> None:
    ia = (RAIZ / "config" / "ia.toml").read_text(encoding="utf-8")
    for velho, novo in trocas.items():
        assert velho in ia
        ia = ia.replace(velho, novo)
    arquivo = tmp_path / "ia-teste.toml"
    arquivo.write_text(ia, encoding="utf-8")
    monkeypatch.setenv("RADAR_CONFIG_IA", str(arquivo))


def test_sem_transacao_aberta_durante_a_chamada_ao_provedor(
    rede: tuple[sessionmaker[Session], Rede],
    config_filtro: Path,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fabrica, _ = rede
    _popular(fabrica)
    espiao = create_engine(engine.url)
    vistas: list[tuple[int, int]] = []

    class ProvedorEspiao(ProvedorFalso):
        def gerar(self, pedido: Pedido) -> Resposta:
            with espiao.connect() as conexao:
                presas = conexao.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid()"
                        " AND state LIKE 'idle in transaction%'"
                    )
                )
            vistas.append((engine.pool.checkedout(), int(presas or 0)))  # type: ignore[attr-defined]
            return super().gerar(pedido)

    monkeypatch.setattr(cli, "_criar_provedor_ia", lambda config: ProvedorEspiao())
    resultado = _analisar(config_filtro)
    assert resultado.exit_code == 0, resultado.output
    assert vistas
    assert set(vistas) == {(0, 0)}
    espiao.dispose()


def test_falha_no_meio_mantem_as_respostas_pagas_e_o_gasto(
    rede: tuple[sessionmaker[Session], Rede],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fabrica, _ = rede
    _popular(fabrica)
    _ia_com(
        tmp_path, monkeypatch, **{"caracteres_por_trecho = 60000": "caracteres_por_trecho = 300"}
    )
    pagas: list[int] = []

    class ProvedorQueCai(ProvedorFalso):
        def gerar(self, pedido: Pedido) -> Resposta:
            if ProvedorFalso.chamadas == 1:  # a 2ª chamada da rodada cai
                ProvedorFalso.chamadas += 1
                raise RuntimeError("Bedrock caiu")
            pagas.append(1)
            return super().gerar(pedido)

    monkeypatch.setattr(cli, "_criar_provedor_ia", lambda config: ProvedorQueCai())
    resultado = _analisar(config_filtro, "--limite", "1")
    assert resultado.exit_code == 1
    assert "1 falhas" in resultado.output
    with fabrica() as sessao:
        assert sessao.scalar(select(func.count()).select_from(RespostaLLM)) == len(pagas) == 1
        assert gasto_do_mes(sessao, AGORA) == Decimal("0.000084")
        assert sessao.scalar(select(func.count()).select_from(AnaliseEdital)) == 0


def test_orcamento_de_tempo_nao_comeca_edital_novo(
    rede: tuple[sessionmaker[Session], Rede],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fabrica, _ = rede
    _popular(fabrica)
    instantes = [0.0, 0.0]  # início da rodada e 1º edital; depois, 31 min passados
    monkeypatch.setattr(cli, "_relogio_rodada", lambda: instantes.pop(0) if instantes else 1860.0)

    resultado = _analisar(config_filtro)
    assert resultado.exit_code == 0, resultado.output
    assert len(_analisados(fabrica)) == 1
    assert "orçamento de tempo" in resultado.output
    assert "analise: 1 analisados, 0 pulados, 0 falhas" in resultado.output


def test_rodada_concorrente_nao_analisa(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path
) -> None:
    fabrica, _ = rede
    _popular(fabrica)
    outro = uuid.uuid4()
    with fabrica() as sessao:
        assert obter_trava(sessao, "analise", outro, timedelta(minutes=30))

    resultado = _analisar(config_filtro)
    assert resultado.exit_code == 0, resultado.output
    assert "outra rodada de análise em andamento" in resultado.output
    assert "resultado:" not in resultado.output
    assert _analisados(fabrica) == set()
    assert ProvedorFalso.chamadas == 0


def test_rodada_solta_a_trava_no_fim(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path, engine: Engine
) -> None:
    fabrica, _ = rede
    _popular(fabrica)
    assert _analisar(config_filtro).exit_code == 0
    with engine.connect() as conexao:
        assert conexao.scalar(text("SELECT count(*) FROM trava_fonte")) == 0
