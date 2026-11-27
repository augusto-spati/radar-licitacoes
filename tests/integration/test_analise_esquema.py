from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from radar.analise.modelos import AnaliseEdital, DocumentoEdital, RespostaLLM


def _analise() -> AnaliseEdital:
    return AnaliseEdital(
        fonte="pncp",
        id_na_fonte="78200482000110-1-000298/2026",
        modelo="amazon.nova-lite-v1:0",
        versao_prompt="abc123def456",
        hash_documentos="h",
        veredito="apto",
        incompleta=False,
        exigencias={},
        comparacao={},
        trechos=1,
        trechos_invalidos=0,
        achados_descartados=0,
        truncado=False,
        tokens_entrada=10,
        tokens_saida=5,
        custo_usd=0,
    )


def test_status_do_documento_invalido(sessao: Session) -> None:
    sessao.add(
        DocumentoEdital(
            fonte="pncp",
            id_na_fonte="x",
            sequencial=1,
            titulo="Edital",
            tipo_nome="Edital",
            url="https://exemplo",
            status="x",
            baixado_em=datetime.now(UTC),
        )
    )
    with pytest.raises(IntegrityError):
        sessao.flush()


def test_analise_unica_por_modelo_e_prompt(sessao: Session) -> None:
    sessao.add(_analise())
    sessao.flush()
    sessao.add(_analise())
    with pytest.raises(IntegrityError):
        sessao.flush()


def test_veredito_invalido(sessao: Session) -> None:
    analise = _analise()
    analise.veredito = "talvez"
    sessao.add(analise)
    with pytest.raises(IntegrityError):
        sessao.flush()


def test_resposta_llm_grava(sessao: Session) -> None:
    sessao.add(
        RespostaLLM(
            chave="k" * 64,
            modelo="amazon.nova-lite-v1:0",
            resposta="{}",
            tokens_entrada=1,
            tokens_saida=1,
            custo_usd=0,
            latencia_ms=10,
        )
    )
    sessao.flush()
    assert sessao.get(RespostaLLM, "k" * 64) is not None
