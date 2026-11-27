"""Achar a planilha nos arquivos do PNCP e o caminho da IA (provedor falso, nunca o Bedrock)."""

import io
import json
import zipfile
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from pdf_minimo import pdf_com_texto
from radar.analise import (
    ArquivoPNCP,
    CacheArquivos,
    ConfigIA,
    Pedido,
    PlanilhaIAInvalida,
    ProvedorComCache,
    PuladoTeto,
    Resposta,
    TetoAtingido,
    carregar_config_ia,
    carregar_config_planilhas,
    documentos_do_edital,
    extrair_planilha_ia,
    nome_de_planilha,
    paginas_com_precos,
    procurar_planilhas,
    texto_para_ia,
)
from radar.analise.provedor import ErroProvedor

RAIZ = Path(__file__).resolve().parents[2]
CONFIG_IA = carregar_config_ia(RAIZ / "config" / "ia.toml")
CONFIG = carregar_config_planilhas(RAIZ / "config" / "ia.toml")
PLANILHA = pdf_com_texto([["2.0 Coletor", "2.11 Tubo PVC DN 150 m 10,00 9,00 11,00 110,00"]])
EDITAL = pdf_com_texto([["EDITAL", "R$ " * 9]])


@pytest.mark.parametrize(
    "nome",
    [
        "Projeto Adutora/Planilha_orcamentaria_assinado.pdf",
        "Pasta Técnica/d) Planilha Orçamentária_Cronograma fisico financeiro_BDI.pdf",
        "Anexo IV/Planilha Orçamentária.pdf",
        "03 Anexo III - Escopo de Fornecimento e Estimativa de Preços.pdf",
        "PO_Ad_Cintec.pdf",
        "Planilha de custos.pdf",
    ],
)
def test_nome_de_planilha(nome: str) -> None:
    assert nome_de_planilha(nome)


@pytest.mark.parametrize(
    "nome",
    [
        "04 Anexo IV - Composição de Preços.xlsx",
        "Anexo IV/Planilha de Cálculo de BDI.pdf",
        "Anexo IV/Planilhas Encargos Sociais.pdf",
        "Anexo IV/Cronograma Físico-Financeiro.pdf",
        "Modelo de planilha orçamentária para a proposta.pdf",
        "06 Anexo VI - Memorial de Cálculo.pdf",
        "Edital CE 3949-2026.pdf",
    ],
)
def test_nao_e_nome_de_planilha(nome: str) -> None:
    assert not nome_de_planilha(nome)


def test_paginas_com_precos() -> None:
    assert paginas_com_precos(["R$ " * 8, "R$ " * 7, "", "R$" * 20]) == [0, 3]


def test_config_das_planilhas() -> None:
    assert (CONFIG.teto_edital_usd, CONFIG.teto_mensal_usd) == (Decimal("0.02"), Decimal("0.30"))
    assert CONFIG.limite_download_bytes == 40_000_000
    assert CONFIG.minimo_linhas == 5


def test_config_com_teto_que_nao_cabe_e_recusada(tmp_path: Path) -> None:
    texto = (RAIZ / "config" / "ia.toml").read_text(encoding="utf-8")
    caminho = tmp_path / "ia.toml"
    caminho.write_text(texto.replace("teto_mensal_usd = 0.30", "teto_mensal_usd = 5.0"), "utf-8")
    with pytest.raises(ValueError, match="tetos"):
        carregar_config_planilhas(caminho)


def _zip(entradas: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as pacote:
        for nome, dados in entradas.items():
            pacote.writestr(nome, dados)
    return buffer.getvalue()


def _arquivo(seq: int, titulo: str, tipo: str = "Outros Documentos") -> ArquivoPNCP:
    return ArquivoPNCP(seq, titulo, tipo, f"https://pncp.gov.br/x/{seq}")


class Baixador:
    def __init__(self, por_seq: dict[int, bytes]) -> None:
        self.por_seq = por_seq
        self.pedidos: list[tuple[int, int]] = []

    def __call__(self, url: str, limite: int) -> bytes:
        seq = int(url.rsplit("/", 1)[-1])
        self.pedidos.append((seq, limite))
        return self.por_seq[seq]


def test_planilha_dentro_do_zip_sem_extensao_no_titulo() -> None:
    arquivos = [
        _arquivo(1, "Edital_CE_n_00526_ADUTORA_SAE", "Edital"),
        _arquivo(2, "Processo_completo_revisado__execucao_obra_adutora"),
        _arquivo(3, "Esclarecimento_36339407000195"),
    ]
    pacote = _zip(
        {
            "Projeto/Planilha_orcamentaria_assinado.pdf": PLANILHA,
            "Projeto/Planilha_orcamentaria_assinado - NAO IMPRIMIR.xlsx": b"PK",
            "Projeto/Cronograma_assinado.pdf": EDITAL,
        }
    )
    baixar = Baixador({1: EDITAL, 2: pacote})
    busca = procurar_planilhas(arquivos, baixar, CONFIG)

    assert [d.nome for d in busca.planilhas] == ["Projeto/Planilha_orcamentaria_assinado.pdf"]
    assert "2.11 Tubo PVC DN 150" in busca.planilhas[0].paginas[0]
    # Só o ZIP: o edital fica para o passo 2 e o esclarecimento nunca é baixado.
    assert baixar.pedidos == [(2, 40_000_000)]


def test_sem_planilha_pelo_nome_vai_ao_edital_e_ao_edital_do_zip() -> None:
    arquivos = [_arquivo(1, "Edital", "Edital"), _arquivo(2, "ANEXOS ZIP CC 009-2026")]
    pacote = _zip({"CONCORRÊNCIA 009-2026 - COLETOR DE ESGOTOS.pdf": EDITAL})
    baixar = Baixador({1: EDITAL, 2: pacote})
    busca = procurar_planilhas(arquivos, baixar, CONFIG)
    assert busca.planilhas == []
    assert len(busca.editais) == 1

    documentos = documentos_do_edital(arquivos, baixar, CONFIG, busca)
    assert len(documentos) == 2
    assert [seq for seq, _ in baixar.pedidos] == [2, 1]


def test_teto_de_downloads_por_edital() -> None:
    arquivos = [_arquivo(i, f"Anexo {i}") for i in range(1, 10)]
    baixar = Baixador({i: b"nada" for i in range(1, 10)})
    busca = procurar_planilhas(arquivos, baixar, replace(CONFIG, max_downloads=2))
    assert busca.downloads == 2


def test_texto_para_ia_so_paginas_com_preco_e_corte() -> None:
    paginas = ["sem preço", "R$ 1 " * 8, "R$ 2 " * 8]
    assert texto_para_ia(paginas, CONFIG).startswith("[página 2]")
    assert "[página 3]" not in texto_para_ia(paginas, replace(CONFIG, max_caracteres=60))


# --- IA com provedor falso ---


class ProvedorFalso:
    def __init__(self, texto: str) -> None:
        self.texto = texto
        self.pedidos: list[Pedido] = []

    def gerar(self, pedido: Pedido) -> Resposta:
        self.pedidos.append(pedido)
        return Resposta(texto=self.texto, tokens_entrada=2500, tokens_saida=3600, latencia_ms=1)


RESPOSTA = json.dumps(
    {
        "linhas": [
            {
                "item": "1.1",
                "descricao": "Placa",
                "unidade": "m²",
                "quantidade": "6,00",
                "preco_com_bdi": "1.088,69",
                "total": "6.532,13",
            }
        ],
        "total_planilha": None,
    }
)


def _provedor(
    tmp_path: Path, falso: ProvedorFalso, config: ConfigIA = CONFIG_IA
) -> ProvedorComCache:
    return ProvedorComCache(falso, CacheArquivos(tmp_path), "gravar", config)


def test_ia_chama_uma_vez_e_o_cache_nao_paga_de_novo(tmp_path: Path) -> None:
    falso = ProvedorFalso(RESPOSTA)
    provedor = _provedor(tmp_path, falso)
    primeira = extrair_planilha_ia(
        "texto", provedor, "amazon.nova-lite-v1:0", CONFIG_IA, CONFIG, Decimal(0)
    )
    segunda = extrair_planilha_ia(
        "texto", provedor, "amazon.nova-lite-v1:0", CONFIG_IA, CONFIG, Decimal(0)
    )

    assert len(falso.pedidos) == 1
    assert falso.pedidos[0].texto == "<planilha>\ntexto\n</planilha>"
    assert primeira.linhas[0]["preco_com_bdi"] == "1.088,69"
    # 2.500 × 0,06 + 3.600 × 0,24 por milhão.
    assert primeira.custo_usd == Decimal("0.001014")
    assert segunda.custo_usd == 0
    assert primeira.estimativa_usd < CONFIG.teto_edital_usd


def test_ia_pulada_pelo_teto_do_edital_e_do_mes(tmp_path: Path) -> None:
    falso = ProvedorFalso(RESPOSTA)
    provedor = _provedor(tmp_path, falso)
    with pytest.raises(PuladoTeto, match="por edital"):
        extrair_planilha_ia(
            "x" * 1_000_000, provedor, "amazon.nova-lite-v1:0", CONFIG_IA, CONFIG, Decimal(0)
        )
    with pytest.raises(PuladoTeto, match="7b.2"):
        extrair_planilha_ia(
            "texto", provedor, "amazon.nova-lite-v1:0", CONFIG_IA, CONFIG, Decimal("0.2999")
        )
    assert falso.pedidos == []


def test_ia_respeita_o_teto_mensal_da_ia(tmp_path: Path) -> None:
    falso = ProvedorFalso(RESPOSTA)

    def teto(_pedido: Pedido) -> None:
        raise TetoAtingido("teto")

    provedor = ProvedorComCache(falso, CacheArquivos(tmp_path), "gravar", CONFIG_IA, teto)
    with pytest.raises(TetoAtingido):
        extrair_planilha_ia(
            "texto", provedor, "amazon.nova-lite-v1:0", CONFIG_IA, CONFIG, Decimal(0)
        )
    assert falso.pedidos == []


def test_zip_com_planilhas_demais_le_so_o_teto() -> None:
    entradas = {f"Lote {i}/Planilha Orçamentária.pdf": PLANILHA for i in range(8)}
    busca = procurar_planilhas(
        [_arquivo(1, "Anexos")],
        Baixador({1: _zip(entradas)}),
        replace(CONFIG, max_planilhas_por_edital=5),
    )
    assert (len(busca.planilhas), busca.puladas) == (5, 3)


def test_entrada_grande_demais_e_pulada_sem_derrubar_o_edital() -> None:
    entradas = {"A/Planilha orcamentaria.pdf": PLANILHA, "B/Planilha orcamentaria.pdf": PLANILHA}
    pequeno = replace(CONFIG, limite_descompactado_bytes=len(PLANILHA) + 10)
    busca = procurar_planilhas([_arquivo(1, "Anexos")], Baixador({1: _zip(entradas)}), pequeno)
    # A segunda passaria do teto descompactado do edital: pulada, a primeira fica.
    assert [d.nome for d in busca.planilhas] == ["A/Planilha orcamentaria.pdf"]
    assert busca.puladas == 1


def test_entrada_acima_do_limite_por_entrada_e_pulada(monkeypatch: pytest.MonkeyPatch) -> None:
    import radar.analise.pacotes as pacotes

    monkeypatch.setattr(pacotes, "LIMITE_ENTRADA", 10)
    entradas = {"Planilha orcamentaria.pdf": PLANILHA}
    busca = procurar_planilhas([_arquivo(1, "Anexos")], Baixador({1: _zip(entradas)}), CONFIG)
    assert (busca.planilhas, busca.puladas) == ([], 1)


def test_ia_invalida_ou_cortada_sai_com_o_custo(tmp_path: Path) -> None:
    class Cortada:
        def gerar(self, pedido: Pedido) -> Resposta:
            assert pedido.recusar_truncada  # a planilha pede para recusar saída cortada
            paga = Resposta(texto="", tokens_entrada=2500, tokens_saida=3600, latencia_ms=1)
            raise ErroProvedor("resposta cortada em max_tokens", resposta=paga)

    provedor = ProvedorComCache(Cortada(), CacheArquivos(tmp_path), "gravar", CONFIG_IA)
    with pytest.raises(PlanilhaIAInvalida) as erro:
        extrair_planilha_ia(
            "texto", provedor, "amazon.nova-lite-v1:0", CONFIG_IA, CONFIG, Decimal(0)
        )
    assert erro.value.custo_usd == Decimal("0.001014")
    assert list(tmp_path.rglob("*.json")) == []  # não virou cache

    lixo = ProvedorComCache(
        ProvedorFalso("não é json"), CacheArquivos(tmp_path), "gravar", CONFIG_IA
    )
    with pytest.raises(PlanilhaIAInvalida) as erro:
        extrair_planilha_ia("texto 2", lixo, "amazon.nova-lite-v1:0", CONFIG_IA, CONFIG, Decimal(0))
    assert erro.value.custo_usd == Decimal("0.001014")
