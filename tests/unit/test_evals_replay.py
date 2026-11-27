# ruff: noqa: E501  (respostas de brinquedo, uma por linha)
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from radar import cli
from radar.analise.cache import CacheArquivos, ProvedorComCache
from radar.analise.evals import (
    INSTRUCAO_GRAVAR,
    avaliar,
    ler_linha_de_base,
    para_markdown,
    validar_gabaritos,
)
from radar.analise.provedor import Pedido, Resposta, carregar_config_ia

RAIZ = Path(__file__).resolve().parents[2]
BRINQUEDO = RAIZ / "tests" / "dados" / "evals"
CONFIG = carregar_config_ia(RAIZ / "config" / "ia.toml")
MODELO = CONFIG.modelo_padrao

runner = CliRunner()


def _achado(campo: str, valor: str, numero: float | None, unidade: str, trecho: str) -> dict[str, object]:  # fmt: skip
    return {"campo": campo, "valor": valor, "numero": numero, "unidade": unidade, "trecho": trecho}


class ModeloDeBrinquedo:
    """Acerta capital, visita e atestado; erra o consórcio; perde o FGTS; inventa a garantia."""

    def gerar(self, pedido: Pedido) -> Resposta:
        if "BRINQUEDO 1" in pedido.texto:
            achados = [
                _achado("capital_social_minimo", "", 100000, "reais", "capital social mínimo de R$ 100.000,00"),
                _achado("visita_tecnica", "obrigatoria", None, "", "A visita técnica é obrigatória."),
                _achado("garantia_contratual", "", 5, "%", "garantia contratual"),
                _achado("atestado_tecnico", "pesquisa de vazamentos", 500000, "metros", "pesquisa de vazamentos em no mínimo 500 km"),
                _achado("consorcio", "permitido", None, "", "participação de consórcio"),
            ]  # fmt: skip
        else:
            achados = [
                _achado("prazo_execucao", "", 6, "meses", "Prazo de execução: 6 (seis) meses.")
            ]
        texto = json.dumps({"achados": achados}, ensure_ascii=False)
        return Resposta(texto=texto, tokens_entrada=1000, tokens_saida=100, latencia_ms=40)


@pytest.fixture
def raiz(tmp_path: Path) -> Path:
    """Cópia do corpus de brinquedo com as respostas gravadas (modo gravar, provedor falso)."""
    destino = tmp_path / "evals"
    shutil.copytree(BRINQUEDO, destino)
    cache = CacheArquivos(destino / "respostas")
    gravador = ProvedorComCache(ModeloDeBrinquedo(), cache, "gravar", CONFIG)
    avaliar(destino, gravador, MODELO, CONFIG, "todos", replay=False)
    return destino


def _replay(raiz: Path) -> ProvedorComCache:
    return ProvedorComCache(None, CacheArquivos(raiz / "respostas"), "replay", CONFIG)


def test_replay_completo_gera_relatorio(raiz: Path) -> None:
    relatorio = avaliar(raiz, _replay(raiz), MODELO, CONFIG, "todos")

    assert relatorio.ausentes == []
    ajuste = relatorio.por_campo["ajuste"]
    assert ajuste["capital_social_minimo"].f1 == 1.0
    assert ajuste["visita_tecnica"].f1 == 1.0
    assert ajuste["atestados"].f1 == 1.0  # 500.000 m = 500 km
    assert ajuste["consorcio"].f1 == 0.0
    assert ajuste["documentos"].recall == 0.0
    assert ajuste["garantia_contratual_percentual"].taxa_alucinacao == 1.0
    assert relatorio.por_campo["teste"]["prazo_execucao_dias"].f1 == 1.0
    assert relatorio.custo_usd > 0  # custo de gerar as respostas, mesmo lidas do cache
    assert relatorio.latencias_ms == [40, 40]
    texto = para_markdown(relatorio)
    assert "## Conjunto ajuste" in texto
    assert "não conferido" not in texto


def test_replay_por_conjunto(raiz: Path) -> None:
    relatorio = avaliar(raiz, _replay(raiz), MODELO, CONFIG, "teste")
    assert list(relatorio.por_campo) == ["teste"]


def test_replay_com_falta_lista_chaves(raiz: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    [primeira, *_] = sorted((raiz / "respostas").rglob("*.json"))
    primeira.unlink()

    relatorio = avaliar(raiz, _replay(raiz), MODELO, CONFIG, "todos")
    assert relatorio.ausentes == [primeira.stem]

    resultado = runner.invoke(
        cli.app, ["avaliar", "rodar", "--modo", "replay", "--raiz", str(raiz)]
    )
    assert resultado.exit_code == 1
    assert INSTRUCAO_GRAVAR in resultado.output
    assert primeira.stem in resultado.output


def test_rodar_replay_pela_cli(raiz: Path) -> None:
    resultado = runner.invoke(
        cli.app, ["avaliar", "rodar", "--modo", "replay", "--raiz", str(raiz)]
    )
    assert resultado.exit_code == 0, resultado.output
    assert "| capital_social_minimo | 1 | 0 | 0 |" in resultado.output


def test_gravar_exige_bedrock(raiz: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RADAR_IA_PROVEDOR", raising=False)
    resultado = runner.invoke(
        cli.app, ["avaliar", "rodar", "--modo", "gravar", "--raiz", str(raiz)]
    )
    assert resultado.exit_code == 2


def test_nao_conferido_vai_para_secao_separada(raiz: Path) -> None:
    caminho = next((raiz / "gabarito").glob("*000002*.json"))
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    dados.update(conferido=False, conferido_por=None, conferido_em=None)
    caminho.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")

    relatorio = avaliar(raiz, _replay(raiz), MODELO, CONFIG, "todos")
    assert "teste" not in relatorio.por_campo
    assert relatorio.nao_conferidos["prazo_execucao_dias"].vp == 1
    assert "rascunho não conferido: métrica sem valor" in para_markdown(relatorio).lower()


def test_validar_brinquedo_sem_erros() -> None:
    assert validar_gabaritos(BRINQUEDO) == []


def test_validar_acusa_trecho_inexistente(raiz: Path) -> None:
    caminho = next((raiz / "gabarito").glob("*000001*.json"))
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    dados["exigencias"]["capital_social_minimo"]["trecho"] = "capital de cem mil reais"
    dados["exigencias"]["consorcio"]["pagina"] = 1
    caminho.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")

    erros = validar_gabaritos(raiz)
    assert any("capital_social_minimo" in e and "não existe no texto" in e for e in erros)
    assert any("consorcio" in e and "está na página 2" in e for e in erros)
    resultado = runner.invoke(cli.app, ["avaliar", "validar", "--raiz", str(raiz)])
    assert resultado.exit_code == 1


def test_validar_acusa_slug_errado(raiz: Path) -> None:
    caminho = next((raiz / "gabarito").glob("*000002*.json"))
    caminho.rename(caminho.with_name("outro.json"))
    assert any("slug" in e for e in validar_gabaritos(raiz))


def test_evals_do_repositorio_em_replay() -> None:
    """O teste do CI: o corpus de evals/ em replay, sem rede e sem custo."""
    raiz = RAIZ / "evals"
    if not list((raiz / "gabarito").glob("*.json")):
        pytest.skip("evals/gabarito/ ainda vazio")
    assert validar_gabaritos(raiz) == []
    gabaritos = [json.loads(p.read_text("utf-8")) for p in (raiz / "gabarito").glob("*.json")]
    if not any(g["conferido"] for g in gabaritos):
        # Rascunho sem conferência ainda não tem respostas gravadas (a rodada real é depois
        # dela); a partir do primeiro gabarito conferido, resposta ausente falha.
        pytest.skip("nenhum gabarito conferido em evals/gabarito/")
    relatorio = avaliar(raiz, _replay(raiz), MODELO, CONFIG, "todos")
    assert not relatorio.ausentes, (
        f"{len(relatorio.ausentes)} respostas ausentes em evals/respostas: {INSTRUCAO_GRAVAR} "
        f"--modelo {MODELO}; chaves: {relatorio.ausentes}"
    )
    base = ler_linha_de_base(raiz / "linha_de_base.json")
    if not base:
        return
    atual = relatorio.por_campo.get("ajuste", {})
    caidos = {
        campo: (f1, atual[campo].f1)
        for campo, f1 in base.items()
        if campo in atual and (atual[campo].f1 or 0.0) < f1 - 0.05
    }
    assert not caidos, f"F1 caiu mais de 0,05 em relação a evals/linha_de_base.json: {caidos}"


def test_preparar_grava_texto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from radar.analise import ClienteArquivosPNCP

    pdf = (RAIZ / "tests" / "dados" / "ia" / "edital_texto.pdf").read_bytes()
    base = "https://pncp.gov.br/pncp-api/v1"

    def tratar(requisicao: httpx.Request) -> httpx.Response:
        caminho = requisicao.url.path
        if caminho.endswith("/consulta/v1/orgaos/78200482000110/compras/2026/298"):
            return httpx.Response(200, json={"valorTotalEstimado": 1500000.5})
        if caminho.endswith("/arquivos"):
            documento = {
                "sequencialDocumento": 1,
                "url": f"{base}/x/1",
                "titulo": "edital.pdf",
                "tipoDocumentoNome": "Edital",
            }
            return httpx.Response(200, json=[documento])
        return httpx.Response(200, content=pdf)

    def cliente() -> ClienteArquivosPNCP:
        http = httpx.Client(base_url=base, transport=httpx.MockTransport(tratar))
        return ClienteArquivosPNCP(http, pausa_s=0, espera_base=0)

    monkeypatch.setattr(cli, "_criar_cliente_arquivos", cliente)
    resultado = runner.invoke(
        cli.app,
        ["avaliar", "preparar", "pncp~78200482000110-1-000298/2026", "--raiz", str(tmp_path)],
    )
    assert resultado.exit_code == 0, resultado.output
    dados = json.loads(
        (tmp_path / "textos" / "pncp_78200482000110-1-000298_2026.json").read_text("utf-8")
    )
    assert dados["valor_estimado"] == "1500000.5"
    assert len(dados["paginas"]) == 2
    assert dados["documentos"][0]["status"] == "extraido"
    assert len(dados["documentos"][0]["sha256"]) == 64
