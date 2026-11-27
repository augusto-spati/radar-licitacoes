import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.analise import (
    AlvoAnalise,
    AnaliseEdital,
    ArmazemLocal,
    ClienteArquivosPNCP,
    Dependencias,
    DocumentoEdital,
    Pedido,
    Resposta,
    TetoAtingido,
    analisar_edital,
    carregar_config_ia,
    carregar_perfil,
    obter_analise,
)

RAIZ = Path(__file__).resolve().parents[2]
DADOS = RAIZ / "tests" / "dados" / "ia"
PDF_TEXTO = (DADOS / "edital_texto.pdf").read_bytes()
PDF_ESCANEADO = (DADOS / "edital_escaneado.pdf").read_bytes()
BASE = "https://pncp.gov.br/pncp-api/v1"
ID = "78200482000110-1-000298/2026"
LISTAGEM = "/pncp-api/v1/orgaos/78200482000110/compras/2026/298/arquivos"
AGORA = datetime(2026, 11, 27, 12, tzinfo=UTC)
CONFIG = carregar_config_ia(RAIZ / "config" / "ia.toml")
PERFIL = carregar_perfil(RAIZ / "config" / "perfil.toml")
MODELO = CONFIG.modelo_padrao


def _doc(seq: int, tipo: str = "Edital") -> dict[str, object]:
    return {
        "sequencialDocumento": seq,
        "url": f"{BASE}/orgaos/78200482000110/compras/2026/298/arquivos/{seq}",
        "titulo": f"doc{seq}.pdf",
        "tipoDocumentoNome": tipo,
        "statusAtivo": True,
    }


class Rede:
    def __init__(self, documentos: dict[int, bytes]) -> None:
        self.documentos = documentos
        self.caminhos: list[str] = []

    def tratar(self, requisicao: httpx.Request) -> httpx.Response:
        self.caminhos.append(requisicao.url.path)
        if requisicao.url.path == LISTAGEM:
            return httpx.Response(200, json=[_doc(seq) for seq in self.documentos])
        seq = int(requisicao.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, content=self.documentos[seq])

    def cliente(self) -> ClienteArquivosPNCP:
        http = httpx.Client(base_url=BASE, transport=httpx.MockTransport(self.tratar))
        return ClienteArquivosPNCP(http, pausa_s=0, espera_base=0)


def _resposta_padrao(pedido: Pedido) -> str:
    return json.dumps(
        {
            "achados": [
                {
                    "campo": "capital_social_minimo",
                    "valor": "",
                    "numero": 250000,
                    "unidade": "reais",
                    "trecho": "capital social mínimo de R$ 250.000,00",
                },
                {
                    "campo": "consorcio",
                    "valor": "vedado",
                    "numero": None,
                    "unidade": "",
                    "trecho": "É vedada a participação de empresas reunidas em consórcio.",
                },
            ]
        },
        ensure_ascii=False,
    )


class ProvedorFalso:
    def __init__(self, responder: Callable[[Pedido], str] = _resposta_padrao) -> None:
        self.responder = responder
        self.chamadas = 0

    def gerar(self, pedido: Pedido) -> Resposta:
        self.chamadas += 1
        return Resposta(
            texto=self.responder(pedido), tokens_entrada=1000, tokens_saida=100, latencia_ms=1
        )


class ProvedorProibido:
    def gerar(self, pedido: Pedido) -> Resposta:
        raise AssertionError("o provedor não devia ser chamado")


def _dep(rede: Rede, tmp_path: Path, provedor: object) -> Dependencias:
    return Dependencias(
        cliente_arquivos=rede.cliente(),
        armazem=ArmazemLocal(tmp_path),
        provedor=provedor,  # type: ignore[arg-type]
        config=CONFIG,
        perfil=PERFIL,
        modelo=MODELO,
    )


ALVO = AlvoAnalise(fonte="pncp", id_na_fonte=ID, valor_estimado=Decimal("1000000"))


def _contar(sessao: Session, modelo: type) -> int:
    return int(sessao.scalar(select(func.count()).select_from(modelo)) or 0)


def test_analisa_e_grava_documento_e_analise(sessao: Session, tmp_path: Path) -> None:
    provedor = ProvedorFalso()
    analise = analisar_edital(sessao, ALVO, _dep(Rede({1: PDF_TEXTO}), tmp_path, provedor), AGORA)

    assert provedor.chamadas == 1
    assert analise.veredito == "apto"
    assert not analise.incompleta
    assert analise.exigencias["capital_social_minimo"]["pagina"] == 1
    assert analise.exigencias["consorcio"]["pagina"] == 2
    assert analise.custo_usd == Decimal("0.000084")  # 1000 × 0,06 + 100 × 0,24 por milhão
    [documento] = sessao.scalars(select(DocumentoEdital)).all()
    assert documento.status == "extraido"
    assert documento.paginas == 2
    assert (tmp_path / "documentos" / f"{documento.sha256}.pdf").is_file()
    assert (tmp_path / "textos" / f"{documento.sha256}.json").is_file()

    lida = obter_analise(sessao, "pncp", ID, MODELO)
    assert lida is not None
    assert lida.analise.id == analise.id
    assert [d.sequencial for d in lida.documentos] == [1]
    assert obter_analise(sessao, "pncp", ID, "outro") is None


def test_zip_e_escaneado_nao_chamam_provedor(sessao: Session, tmp_path: Path) -> None:
    rede = Rede({1: b"PK\x03\x04zip", 2: PDF_ESCANEADO})
    analise = analisar_edital(sessao, ALVO, _dep(rede, tmp_path, ProvedorProibido()), AGORA)

    assert analise.veredito == "verificar"
    assert analise.incompleta
    status = sorted(d.status for d in sessao.scalars(select(DocumentoEdital)))
    assert status == ["nao_pdf", "sem_texto"]


def test_documento_que_falha_marca_incompleta(sessao: Session, tmp_path: Path) -> None:
    rede = Rede({1: PDF_TEXTO, 2: b"%PDF-1.4 corrompido"})
    analise = analisar_edital(sessao, ALVO, _dep(rede, tmp_path, ProvedorFalso()), AGORA)
    assert analise.incompleta
    assert analise.veredito == "verificar"
    status = {d.sequencial: d.status for d in sessao.scalars(select(DocumentoEdital))}
    assert status == {1: "extraido", 2: "falha"}


def test_teto_no_meio_nao_grava_analise_mas_guarda_documentos(
    sessao: Session, tmp_path: Path
) -> None:
    class ProvedorComTeto:
        def __init__(self) -> None:
            self.chamadas = 0

        def gerar(self, pedido: Pedido) -> Resposta:
            self.chamadas += 1
            if self.chamadas == 2:
                raise TetoAtingido("teto")
            return Resposta(
                texto='{"achados": []}', tokens_entrada=1, tokens_saida=1, latencia_ms=1
            )

    grande = Rede({1: PDF_TEXTO})
    provedor = ProvedorComTeto()
    config = replace(CONFIG, caracteres_por_trecho=300)
    dep = replace(_dep(grande, tmp_path, provedor), config=config)
    with pytest.raises(TetoAtingido):
        analisar_edital(sessao, ALVO, dep, AGORA)
    assert provedor.chamadas == 2
    assert _contar(sessao, AnaliseEdital) == 0
    # O documento foi gravado na sua própria transação curta, antes de chamar o modelo.
    assert _contar(sessao, DocumentoEdital) == 1


def test_pdf_ja_no_armazem_nao_baixa_de_novo(sessao: Session, tmp_path: Path) -> None:
    rede = Rede({1: PDF_TEXTO})
    analisar_edital(sessao, ALVO, _dep(rede, tmp_path, ProvedorFalso()), AGORA)
    rede.caminhos.clear()

    analisar_edital(sessao, ALVO, _dep(rede, tmp_path, ProvedorFalso()), AGORA)
    assert rede.caminhos == [LISTAGEM]


def test_refazer_regrava_upsert(sessao: Session, tmp_path: Path) -> None:
    rede = Rede({1: PDF_TEXTO})
    analisar_edital(sessao, ALVO, _dep(rede, tmp_path, ProvedorFalso()), AGORA)

    def sem_consorcio(pedido: Pedido) -> str:
        return '{"achados": []}'

    segunda = analisar_edital(
        sessao, ALVO, _dep(rede, tmp_path, ProvedorFalso(sem_consorcio)), AGORA
    )
    assert _contar(sessao, AnaliseEdital) == 1
    assert segunda.exigencias["consorcio"] is None
    assert _contar(sessao, DocumentoEdital) == 1


def test_fonte_que_nao_e_pncp(sessao: Session, tmp_path: Path) -> None:
    alvo = AlvoAnalise(fonte="sanepar", id_na_fonte="OE-1/26", valor_estimado=None)
    with pytest.raises(ValueError):
        analisar_edital(sessao, alvo, _dep(Rede({}), tmp_path, ProvedorProibido()), AGORA)


def test_pagina_inicial_de_cada_documento(sessao: Session, tmp_path: Path) -> None:
    # Dois PDFs de 2 páginas: o texto juntado tem 4 páginas; o 2º começa na 3.
    rede = Rede({1: PDF_TEXTO, 2: PDF_TEXTO})
    analisar_edital(sessao, ALVO, _dep(rede, tmp_path, ProvedorFalso()), AGORA)
    inicio = {d.sequencial: d.pagina_inicial for d in sessao.scalars(select(DocumentoEdital))}
    assert inicio == {1: 1, 2: 3}

    # Na releitura (texto já no armazém) a página inicial continua certa.
    analisar_edital(sessao, ALVO, _dep(rede, tmp_path, ProvedorFalso()), AGORA)
    sessao.expire_all()
    inicio = {d.sequencial: d.pagina_inicial for d in sessao.scalars(select(DocumentoEdital))}
    assert inicio == {1: 1, 2: 3}


def test_documento_sem_texto_nao_tem_pagina_inicial(sessao: Session, tmp_path: Path) -> None:
    rede = Rede({1: PDF_ESCANEADO, 2: PDF_TEXTO})
    analisar_edital(sessao, ALVO, _dep(rede, tmp_path, ProvedorFalso()), AGORA)
    inicio = {d.sequencial: d.pagina_inicial for d in sessao.scalars(select(DocumentoEdital))}
    assert inicio == {1: None, 2: 1}


def test_recomparar_refaz_veredito_com_perfil_novo_sem_ia(sessao: Session, tmp_path: Path) -> None:
    from radar.analise import recomparar

    # Capital mínimo de R$ 250 mil no edital; o perfil de exemplo tem 1 milhão: apto.
    analisar_edital(sessao, ALVO, _dep(Rede({1: PDF_TEXTO}), tmp_path, ProvedorFalso()), AGORA)
    menor = PERFIL.model_copy(update={"capital_social": Decimal("100000")})

    assert recomparar(sessao, menor) == (1, 1)
    sessao.expire_all()
    [analise] = sessao.scalars(select(AnaliseEdital)).all()
    assert analise.veredito == "inapto"
    [capital] = [i for i in analise.comparacao["itens"] if i["campo"] == "capital_social_minimo"]
    assert capital["situacao"] == "nao_atende"
    assert recomparar(sessao, menor) == (1, 0)  # idempotente
    assert recomparar(sessao, PERFIL) == (1, 1)
