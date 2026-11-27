"""`radar planilhas atualizar` de ponta a ponta: rede do PNCP falsa, PDF gerado no teste e
provedor de IA falso (nunca o Bedrock)."""

import copy
import io
import json
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime, timedelta
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
from radar.analise.provedor import ErroProvedor
from radar.comum import Base, fabrica_sessao
from radar.editais import registrar
from radar.precos import (
    GastoPlanilha,
    PlanilhaEdital,
    PrecoItem,
    gasto_planilhas_do_mes,
    registrar_gasto_planilha,
)

RAIZ = Path(__file__).resolve().parent.parent.parent
DADOS = RAIZ / "tests" / "dados"
AMOSTRA: dict[str, Any] = json.loads((DADOS / "registro_pregao.json").read_text(encoding="utf-8"))
AGORA = datetime(2026, 11, 27, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
BASE = "https://pncp.gov.br/pncp-api/v1"
COLETOR = "56024581000156-1-000529/2026"
ADUTORA = "45355575000165-1-000141/2026"
VAZIO = "11111111000111-1-000001/2026"
PE = "44444444000144-1-000004/2026"

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


def _linha_ia(item: str, descricao: str, q: str, sem: str, com: str, total: str) -> dict[str, str]:
    return {
        "item": item,
        "descricao": descricao,
        "unidade": "unid",
        "quantidade": q,
        "preco_sem_bdi": sem,
        "preco_com_bdi": com,
        "total": total,
    }


# As três linhas do "SUB TOTAL 1 R$ 16.122,94" de Ibaté, como a IA devolveria do texto
# picotado, e uma inventada (fecha a conta, mas os números não estão no texto).
IA_IBATE = json.dumps(
    {
        "linhas": [
            _linha_ia("1.1", "Placa de identificação de obra", "6,00", "870,95", "1.088,69",
                      "6.532,13"),
            _linha_ia("1.2", "Locação de container tipo escritório", "3,00", "1.553,18",
                      "1.941,48", "5.824,43"),
            _linha_ia("1.3", "Locação de container tipo depósito", "3,00", "1.004,37",
                      "1.255,46", "3.766,39"),
            _linha_ia("9.9", "TUBO PVC DN 100 PARA REDE DE ÁGUA", "10", "", "50,00", "500,00"),
        ],
        "total_planilha": "16.122,94",
    }
)  # fmt: skip


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
    # As três linhas da IA passaram na guarda (a inventada não) e nenhuma é serviço-padrão.
    assert adutora is not None and adutora.custo_usd == Decimal("0.001014")
    assert (adutora.linhas_lidas, adutora.linhas_gravadas) == (3, 0)
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
        "0 IA inválida, 0 aguardando IA, 0 falhas, 3 linhas gravadas, US$ 0.0010"
    )

    # Segunda rodada: nada a refazer, nenhuma requisição.
    rede_.caminhos.clear()
    resultado = _atualizar(config_filtro)
    assert resultado.exit_code == 0, resultado.output
    assert rede_.caminhos == []
    assert resultado.output.strip().splitlines()[-1].startswith("planilhas: 0 editais")


def _gastos(fabrica: sessionmaker[Session]) -> list[tuple[str, Decimal]]:
    with fabrica() as sessao:
        linhas = sessao.execute(
            select(GastoPlanilha.id_pncp, GastoPlanilha.custo_usd).order_by(GastoPlanilha.id)
        ).all()
    return [(i, c) for i, c in linhas]


def test_teto_mensal_da_7b2_pula_a_ia(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path
) -> None:
    fabrica, _ = rede
    _popular(fabrica)
    with fabrica() as sessao:
        registrar_gasto_planilha(sessao, "outro", Decimal("0.2999"))
        sessao.commit()
    resultado = _atualizar(config_filtro, "--edital", f"pncp~{ADUTORA}")
    assert resultado.exit_code == 0, resultado.output
    assert _estados(fabrica)[ADUTORA] == ("pulado_teto", None)
    assert ProvedorFalso.pedidos == []
    assert "1 puladas pelo teto" in resultado.output


def test_gasto_do_mes_vem_do_provedor_e_refazer_nao_zera(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path
) -> None:
    """Refazer o edital acerta o cache (custo zero): o gasto do mês continua o da 1ª chamada,
    não some quando `planilha_edital.custo_usd` é sobrescrito."""
    fabrica, _ = rede
    _popular(fabrica)
    assert _atualizar(config_filtro, "--edital", f"pncp~{ADUTORA}").exit_code == 0
    assert _atualizar(config_filtro, "--edital", f"pncp~{ADUTORA}", "--refazer").exit_code == 0

    assert len(ProvedorFalso.pedidos) == 1  # a 2ª veio do cache
    assert _gastos(fabrica) == [(ADUTORA, Decimal("0.001014"))]
    with fabrica() as sessao:
        assert gasto_planilhas_do_mes(sessao, AGORA) == Decimal("0.001014")


class ProvedorInvalido:
    """Cobra e devolve lixo (ou corta em max_tokens): o ProvedorComCache conta e não guarda."""

    pedidos = 0

    def gerar(self, pedido: Pedido) -> Resposta:
        ProvedorInvalido.pedidos += 1
        paga = Resposta(texto="", tokens_entrada=2500, tokens_saida=3600, latencia_ms=1)
        raise ErroProvedor("resposta cortada em max_tokens", resposta=paga)


def test_resposta_paga_e_invalida_conta_no_teto_e_nao_e_comprada_de_novo(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fabrica, rede_ = rede
    _popular(fabrica)
    ProvedorInvalido.pedidos = 0
    monkeypatch.setattr(cli, "_criar_provedor_ia", lambda config: ProvedorInvalido())
    resultado = _atualizar(config_filtro, "--edital", f"pncp~{ADUTORA}")
    assert resultado.exit_code == 0, resultado.output
    assert _estados(fabrica)[ADUTORA] == ("ia_invalida", "ia")
    assert _gastos(fabrica) == [(ADUTORA, Decimal("0.001014"))]
    with fabrica() as sessao:
        estado = sessao.get(PlanilhaEdital, ADUTORA)
        assert estado is not None and estado.custo_usd == Decimal("0.001014")
    assert "1 IA inválida" in resultado.output

    # A rodada normal não compra de novo (estado final), mesmo semanas depois.
    rede_.caminhos.clear()
    monkeypatch.setattr(cli, "_agora", lambda: AGORA + timedelta(days=30))
    assert _atualizar(config_filtro).exit_code == 0
    assert ProvedorInvalido.pedidos == 1
    assert not any("45355575000165" in c for c in rede_.caminhos)


def test_falha_depois_da_ia_registra_o_custo(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fabrica, _ = rede
    _popular(fabrica)

    def quebra(*args: object, **kwargs: object) -> None:
        raise RuntimeError("banco caiu")

    monkeypatch.setattr(cli, "gravar_planilha", quebra)
    resultado = _atualizar(config_filtro, "--edital", f"pncp~{ADUTORA}")
    assert resultado.exit_code == 1
    with fabrica() as sessao:
        estado = sessao.get(PlanilhaEdital, ADUTORA)
        assert estado is not None
        assert (estado.status, estado.motivo, estado.custo_usd) == (
            "falhou",
            "RuntimeError",
            Decimal("0.001014"),
        )
    assert _gastos(fabrica) == [(ADUTORA, Decimal("0.001014"))]


def test_ia_desligada_aguarda_sem_virar_falha_semanal(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fabrica, rede_ = rede
    _popular(fabrica)
    monkeypatch.setenv("RADAR_IA_PROVEDOR", "desligado")
    resultado = _atualizar(config_filtro, "--edital", f"pncp~{ADUTORA}")
    assert resultado.exit_code == 0, resultado.output
    assert _estados(fabrica)[ADUTORA] == ("aguarda_ia", None)
    assert ProvedorFalso.pedidos == []

    # Com a IA desligada, nem semanas depois ele volta; ligada, volta na rodada seguinte.
    monkeypatch.setattr(cli, "_agora", lambda: AGORA + timedelta(days=60))
    rede_.caminhos.clear()
    assert _atualizar(config_filtro).exit_code == 0
    assert not any("45355575000165" in c for c in rede_.caminhos)
    monkeypatch.setenv("RADAR_IA_PROVEDOR", "bedrock")
    assert _atualizar(config_filtro).exit_code == 0
    assert _estados(fabrica)[ADUTORA] == ("extraida", "ia")


def test_pe_fica_fora_porque_o_tce_pe_cobre(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path
) -> None:
    fabrica, rede_ = rede
    _popular(fabrica)
    with fabrica() as sessao:
        registro = _registro(PE, "Implantação de rede de água", "2026-11-03T09:00:00")
        registro["unidadeOrgao"]["ufSigla"] = "PE"
        registrar(sessao, [registro])
        sessao.commit()
    config_filtro.write_text(
        config_filtro.read_text(encoding="utf-8").replace('["SP"]', '["SP", "PE"]'),
        encoding="utf-8",
    )
    assert _atualizar(config_filtro).exit_code == 0
    assert PE not in _estados(fabrica)
    assert not any(c.startswith(PE[:14]) for c in rede_.caminhos)
    assert _atualizar(config_filtro, "--edital", f"pncp~{PE}").exit_code == 2


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


def _transacoes_abertas_na_rede(
    rede: tuple[sessionmaker[Session], Rede],
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """Roda a rodada e devolve onde (rede do PNCP, IA) havia sessão com transação aberta."""
    fabrica, rede_ = rede
    sessoes: list[Session] = []

    def criar() -> Session:
        sessao = fabrica()
        sessoes.append(sessao)
        return sessao

    abertas: list[str] = []
    tratar = rede_.tratar

    def vigia_rede(requisicao: httpx.Request) -> httpx.Response:
        abertas.extend(f"rede {requisicao.url.path}" for s in sessoes if s.in_transaction())
        return tratar(requisicao)

    class ProvedorVigia(ProvedorFalso):
        def gerar(self, pedido: Pedido) -> Resposta:
            abertas.extend("ia" for s in sessoes if s.in_transaction())
            return super().gerar(pedido)

    monkeypatch.setattr(cli, "_criar_sessao", criar)
    monkeypatch.setattr(rede_, "tratar", vigia_rede)
    monkeypatch.setattr(cli, "_criar_provedor_ia", lambda config: ProvedorVigia())
    resultado = _atualizar(config_filtro, "--refazer")
    assert resultado.exit_code == 0, resultado.output
    assert len(ProvedorFalso.pedidos) == 1  # o vigia passou pela IA (senão não prova nada)
    return abertas


def test_nao_segura_transacao_aberta_durante_a_rede_e_a_ia(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O Neon derruba transação ociosa (ver test_cli_tcepe): nenhuma leitura fica aberta
    enquanto se lista, baixa ou chama a IA."""
    _popular(rede[0])
    assert _transacoes_abertas_na_rede(rede, config_filtro, monkeypatch) == []


def test_o_vigia_pega_leitura_esquecida_aberta(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prova do vigia: uma leitura deixada aberta antes da rede aparece."""
    _popular(rede[0])
    original = cli.gasto_planilhas_do_mes

    def esquece_aberta(sessao: Session, agora: datetime) -> Decimal:
        valor = original(sessao, agora)
        sessao.rollback = lambda: None  # type: ignore[method-assign]
        return valor

    monkeypatch.setattr(cli, "gasto_planilhas_do_mes", esquece_aberta)
    abertas = _transacoes_abertas_na_rede(rede, config_filtro, monkeypatch)
    assert abertas and abertas[0].startswith("rede ")


def test_so_obra_entra_compra_e_projeto_ficam_fora(
    rede: tuple[sessionmaker[Session], Rede], config_filtro: Path
) -> None:
    """1ª rodada real: 10 alvos, a maioria compra (hidrômetros), calibração e projeto, que não
    têm planilha de serviço; só gastavam download e IA. Entra obra/serviço de engenharia."""
    fabrica, rede_ = rede
    _popular(fabrica)
    fora = {
        "44444444000144-1-000004/2026": "Aquisição de tubos de PVC para rede de água",
        "55555555000155-1-000005/2026": (
            "Registro de preço para aquisição de hidrômetros da rede de água"
        ),
        "66666666000166-1-000006/2026": "Elaboração de projeto executivo de rede de água",
        "77777777000177-1-000007/2026": "Calibração de macromedidores da rede de água",
    }
    dentro = {"88888888000188-1-000008/2026": "Manutenção e reforma das redes de água"}
    with fabrica() as sessao:
        registrar(
            sessao,
            [_registro(n, o, "2026-11-05T09:00:00") for n, o in {**fora, **dentro}.items()],
        )
        sessao.commit()
    resultado = _atualizar(config_filtro)
    estados = _estados(fabrica)
    assert not set(fora) & set(estados), resultado.output
    assert set(dentro) <= set(estados), resultado.output
    assert not any(c.startswith(n[:14]) for c in rede_.caminhos for n in fora)
