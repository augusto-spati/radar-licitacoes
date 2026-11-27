"""`radar planilhas atualizar` de ponta a ponta: rede do PNCP falsa, PDF gerado no teste e
provedor de IA falso (nunca o Bedrock)."""

import copy
import io
import json
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from pdf_minimo import pdf_com_texto
from radar import cli
from radar.analise import ClienteArquivosPNCP, ConfigIA, Pedido, Resposta
from radar.analise.modelos import RespostaLLM
from radar.comum import Base, fabrica_sessao
from radar.editais import registrar
from radar.precos import PlanilhaEdital, PrecoItem, registrar_planilha

RAIZ = Path(__file__).resolve().parent.parent.parent
DADOS = RAIZ / "tests" / "dados"
AMOSTRA: dict[str, Any] = json.loads((DADOS / "registro_pregao.json").read_text(encoding="utf-8"))
AGORA = datetime(2026, 11, 27, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
BASE = "https://pncp.gov.br/pncp-api/v1"
COLETOR = "56024581000156-1-000529/2026"
ADUTORA = "45355575000165-1-000141/2026"
VAZIO = "11111111000111-1-000001/2026"

runner = CliRunner()


def _paginas(nome: str) -> list[list[str]]:
    texto = (DADOS / "planilhas" / f"{nome}.txt").read_text(encoding="utf-8")
    return [p.splitlines() for p in re.split(r"=====PAGINA \d+\n", texto) if p.strip()]


PLANILHA_PDF = pdf_com_texto(_paginas("ribeirao"))
# Ibaté: a planilha está dentro do edital, em texto picotado (a IA é a reserva).
EDITAL_IBATE = pdf_com_texto([["EDITAL DE CONCORRÊNCIA"], *_paginas("ibate")])
EDITAL_SEM_PRECO = pdf_com_texto([["EDITAL", "Objeto: coletor de esgoto"]])


def _zip(entradas: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as pacote:
        for nome, dados in entradas.items():
            pacote.writestr(nome, dados)
    return buffer.getvalue()


ARQUIVOS: dict[str, list[tuple[str, str, bytes]]] = {
    "56024581000156/compras/2026/529": [
        ("Edital", "ed_conc_eletr_019_2026", EDITAL_SEM_PRECO),
        (
            "Outros Documentos",
            "CE_019_2026",
            _zip({"Anexo IV/Planilha Orçamentária.pdf": PLANILHA_PDF}),
        ),
        ("Outros Documentos", "Esclarecimento_1", b"%PDF nunca baixado"),
    ],
    "45355575000165/compras/2026/141": [("Edital", "EDITAL_CE_3949-2026", EDITAL_IBATE)],
    "11111111000111/compras/2026/1": [],
}

IA_IBATE = json.dumps(
    {
        "linhas": [
            {
                "item": "2.2",
                "codigo": "103091",
                "fonte": "SINAPI",
                "descricao": 'ASSENTAMENTO DE TUBO DE FERRO FUNDIDO PARA REDE DE ÁGUA, DN 6" '
                "polegadas (150mm), JUNTA FLANGEADA (NÃO INCLUI O FORNECIMENTO). AF_09/2021",
                "unidade": "m",
                "quantidade": "24,00",
                "preco_sem_bdi": "24,10",
                "preco_com_bdi": "30,13",
                "total": "723,00",
            },
            # Inventada: fecha a conta mas os números não estão no texto.
            {
                "item": "9.9",
                "descricao": "TUBO PVC DN 100 PARA REDE DE ÁGUA",
                "unidade": "m",
                "quantidade": "10",
                "preco_com_bdi": "50,00",
                "total": "500,00",
            },
        ],
        "total_planilha": None,
    }
)


class ProvedorFalso:
    pedidos: list[Pedido] = []

    def gerar(self, pedido: Pedido) -> Resposta:
        ProvedorFalso.pedidos.append(pedido)
        return Resposta(texto=IA_IBATE, tokens_entrada=2500, tokens_saida=3600, latencia_ms=1)


class Rede:
    def __init__(self) -> None:
        self.caminhos: list[str] = []

    def tratar(self, requisicao: httpx.Request) -> httpx.Response:
        caminho = requisicao.url.path.removeprefix("/pncp-api/v1/orgaos/")
        self.caminhos.append(caminho)
        if caminho.endswith("/arquivos"):
            compra = caminho.removesuffix("/arquivos")
            return httpx.Response(
                200,
                json=[
                    {
                        "sequencialDocumento": i,
                        "url": f"{BASE}/orgaos/{compra}/arquivos/{i}",
                        "titulo": titulo,
                        "tipoDocumentoNome": tipo,
                        "statusAtivo": True,
                    }
                    for i, (tipo, titulo, _) in enumerate(ARQUIVOS[compra], start=1)
                ],
            )
        compra, _, seq = caminho.rpartition("/arquivos/")
        return httpx.Response(200, content=ARQUIVOS[compra][int(seq) - 1][2])


def _truncar(engine: Engine) -> None:
    tabelas = ", ".join(tabela.name for tabela in Base.metadata.sorted_tables)
    with engine.begin() as conexao:
        conexao.execute(text(f"TRUNCATE {tabelas} RESTART IDENTITY CASCADE"))


@pytest.fixture
def rede(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[sessionmaker[Session], Rede]]:
    fabrica = fabrica_sessao(engine)
    rede = Rede()
    ProvedorFalso.pedidos = []
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
    monkeypatch.delenv("RADAR_CONFIG_IA", raising=False)
    yield fabrica, rede
    _truncar(engine)


@pytest.fixture
def config_filtro(tmp_path: Path) -> Path:
    caminho = tmp_path / "filtro.toml"
    caminho.write_text(
        'ufs = ["SP"]\nmodalidades = [4]\n\n[linhas]\n'
        'redes = ["coletor de esgoto", "rede adutora", "rede de agua"]\n'
        'comercial = ["leitura de hidrometro"]\n',
        encoding="utf-8",
    )
    return caminho


def _registro(numero: str, objeto: str, publicacao: str) -> dict[str, Any]:
    registro = copy.deepcopy(AMOSTRA)
    registro["numeroControlePNCP"] = numero
    registro["objetoCompra"] = objeto
    registro["modalidadeId"] = 4
    registro["dataPublicacaoPncp"] = publicacao
    registro["unidadeOrgao"]["ufSigla"] = "SP"
    registro["unidadeOrgao"]["municipioNome"] = "Cidade"
    registro["orgaoEntidade"]["cnpj"] = numero[:14]
    return registro


def _popular(fabrica: sessionmaker[Session]) -> None:
    with fabrica() as sessao:
        registrar(
            sessao,
            [
                _registro(
                    COLETOR, "Substituição de coletor de esgoto na rua A", "2026-11-20T10:00:00"
                ),
                _registro(
                    ADUTORA,
                    "Execução de rede adutora de abastecimento de água",
                    "2026-10-28T12:04:35",
                ),
                _registro(VAZIO, "Implantação de rede de água", "2026-11-01T09:00:00"),
                # Fora: linha comercial, e publicado antes da janela.
                _registro(
                    "22222222000122-1-000002/2026", "Leitura de hidrômetros", "2026-11-02T09:00:00"
                ),
                _registro(
                    "33333333000133-1-000003/2026", "Rede de água antiga", "2026-01-02T09:00:00"
                ),
            ],
        )
        sessao.commit()


def _atualizar(config_filtro: Path, *args: str) -> Any:
    return runner.invoke(cli.app, ["planilhas", "atualizar", "--config", str(config_filtro), *args])


def _estados(fabrica: sessionmaker[Session]) -> dict[str, tuple[str, str | None]]:
    with fabrica() as sessao:
        return {
            e.id_pncp: (e.status, e.metodo) for e in sessao.scalars(select(PlanilhaEdital)).all()
        }


def test_rodada_completa_deterministico_ia_e_sem_planilha(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path
) -> None:
    fabrica, rede_ = rede
    _popular(fabrica)
    resultado = _atualizar(config_filtro)
    assert resultado.exit_code == 0, resultado.output

    assert _estados(fabrica) == {
        COLETOR: ("extraida", "deterministico"),
        ADUTORA: ("extraida", "ia"),
        VAZIO: ("sem_planilha", None),
    }
    with fabrica() as sessao:
        itens = sessao.scalars(select(PrecoItem).order_by(PrecoItem.chave_origem)).all()
        por_edital = {(i.processo, i.servico, i.material, i.dn, i.escopo) for i in itens}
        coletor = sessao.get(PlanilhaEdital, COLETOR)
        adutora = sessao.get(PlanilhaEdital, ADUTORA)
        respostas = sessao.scalar(select(func.count()).select_from(RespostaLLM))
    assert por_edital == {
        (COLETOR, "rede_esgoto", "pvc", 150, "completo"),
        (COLETOR, "rede_esgoto", "pvc", 200, "completo"),
        (COLETOR, "rede_esgoto", "pvc", 250, "completo"),
    }
    assert all(i.origem == "pncp_planilha" and i.uf == "SP" for i in itens)
    assert coletor is not None and coletor.documento == "Anexo IV/Planilha Orçamentária.pdf"
    # A linha da IA passou na conta (a inventada não), mas "JUNTA FLANGEADA" cai na exclusão
    # "flange" da rede de água: lida e não gravada.
    assert adutora is not None and adutora.custo_usd == Decimal("0.001014")
    assert (adutora.linhas_lidas, adutora.linhas_gravadas) == (1, 0)
    assert respostas == 1
    # Uma chamada à IA, só com as páginas com R$ do edital de Ibaté.
    assert len(ProvedorFalso.pedidos) == 1
    assert "[página 2]" in ProvedorFalso.pedidos[0].texto
    assert "EDITAL DE CONCORRÊNCIA" not in ProvedorFalso.pedidos[0].texto
    # Esclarecimento nunca baixado; o edital do coletor também não (a planilha bastou).
    assert "56024581000156/compras/2026/529/arquivos/3" not in rede_.caminhos
    assert "56024581000156/compras/2026/529/arquivos/1" not in rede_.caminhos

    ultima = resultado.output.strip().splitlines()[-1]
    assert "resultado:" not in resultado.output
    assert ultima == (
        "planilhas: 3 editais, 1 lidas sem IA, 1 com IA, 1 sem planilha, 0 puladas pelo teto, "
        "0 falhas, 3 linhas gravadas, US$ 0.0010"
    )

    # Segunda rodada: nada a refazer, nenhuma requisição.
    rede_.caminhos.clear()
    resultado = _atualizar(config_filtro)
    assert resultado.exit_code == 0, resultado.output
    assert rede_.caminhos == []
    assert resultado.output.strip().splitlines()[-1].startswith("planilhas: 0 editais")


def test_teto_mensal_da_7b2_pula_a_ia(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path
) -> None:
    fabrica, _ = rede
    _popular(fabrica)
    with fabrica() as sessao:
        registrar_planilha(sessao, "gasto", "extraida", metodo="ia", custo_usd=Decimal("0.2999"))
        sessao.commit()
    resultado = _atualizar(config_filtro, "--edital", f"pncp~{ADUTORA}")
    assert resultado.exit_code == 0, resultado.output
    assert _estados(fabrica)[ADUTORA] == ("pulado_teto", None)
    assert ProvedorFalso.pedidos == []
    assert "1 puladas pelo teto" in resultado.output


def test_ia_desligada_deixa_para_depois(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fabrica, _ = rede
    _popular(fabrica)
    monkeypatch.setenv("RADAR_IA_PROVEDOR", "desligado")
    resultado = _atualizar(config_filtro, "--edital", f"pncp~{ADUTORA}")
    assert resultado.exit_code == 0, resultado.output
    assert _estados(fabrica)[ADUTORA] == ("falhou", None)
    assert ProvedorFalso.pedidos == []


def test_falha_de_rede_marca_falhou_e_sai_1(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fabrica, rede_ = rede
    _popular(fabrica)

    def erro(requisicao: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    monkeypatch.setattr(rede_, "tratar", erro)
    resultado = _atualizar(config_filtro, "--edital", f"pncp~{COLETOR}")
    assert resultado.exit_code == 1
    with fabrica() as sessao:
        estado = sessao.get(PlanilhaEdital, COLETOR)
        assert estado is not None and (estado.status, estado.motivo) == ("falhou", "ErroArquivos")
    assert "1 falhas" in resultado.output


def test_edital_desconhecido_sai_2(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path
) -> None:
    assert _atualizar(config_filtro, "--edital", "pncp~nada").exit_code == 2
    assert _atualizar(config_filtro, "--edital", "sanepar~OE-1").exit_code == 2
