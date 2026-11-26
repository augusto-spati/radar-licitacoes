import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.editais import registrar
from radar.editais.modelos import Edital

CHAVE = "13127340000120-1-000036/2025"


@pytest.fixture
def registro() -> dict[str, Any]:
    caminho = Path(__file__).resolve().parent.parent / "dados" / "registro_pregao.json"
    return json.loads(caminho.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _contar(sessao: Session) -> int:
    return sessao.scalar(select(func.count()).select_from(Edital)) or 0


def test_insere_registro_real(sessao: Session, registro: dict[str, Any]) -> None:
    resultado = registrar(sessao, [registro])

    assert (resultado.inseridos, resultado.atualizados) == (1, 0)
    edital = sessao.get(Edital, ("pncp", CHAVE))
    assert edital is not None
    assert edital.uf == "MA"
    assert edital.orgao_nome == "SECRETARIA DE ESTADO DE ADMINISTRACAO PENITENCIARIA - SEAP"
    assert edital.valor_estimado == Decimal("1369661.52")
    assert edital.data_atualizacao.isoformat() == "2026-11-26T07:00:52"
    assert edital.data_atualizacao.tzinfo is None
    assert edital.bruto == registro


def test_repetir_nao_duplica(sessao: Session, registro: dict[str, Any]) -> None:
    registrar(sessao, [registro])
    resultado = registrar(sessao, [registro])

    assert _contar(sessao) == 1
    assert resultado.ignorados == 1
    assert (resultado.inseridos, resultado.atualizados) == (0, 0)


def test_versao_mais_nova_vence_e_mais_velha_nao(sessao: Session, registro: dict[str, Any]) -> None:
    registrar(sessao, [registro])

    nova = copy.deepcopy(registro)
    nova["objetoCompra"] = "OBJETO NOVO"
    nova["dataAtualizacaoGlobal"] = "2026-11-27T08:00:00"
    resultado = registrar(sessao, [nova])
    assert (resultado.atualizados, resultado.inseridos, resultado.ignorados) == (1, 0, 0)
    sessao.expire_all()
    assert sessao.get(Edital, ("pncp", CHAVE)).objeto == "OBJETO NOVO"  # type: ignore[union-attr]

    velha = copy.deepcopy(registro)
    velha["objetoCompra"] = "OBJETO VELHO"
    velha["dataAtualizacaoGlobal"] = "2026-11-20T08:00:00"
    resultado = registrar(sessao, [velha])
    assert (resultado.ignorados, resultado.atualizados) == (1, 0)
    sessao.expire_all()
    assert sessao.get(Edital, ("pncp", CHAVE)).objeto == "OBJETO NOVO"  # type: ignore[union-attr]


def test_campos_nulos_sao_aceitos(sessao: Session, registro: dict[str, Any]) -> None:
    registro["dataEncerramentoProposta"] = None
    registro["valorTotalEstimado"] = None
    registro["unidadeOrgao"] = None

    resultado = registrar(sessao, [registro])

    assert resultado.inseridos == 1
    edital = sessao.get(Edital, ("pncp", CHAVE))
    assert edital is not None
    assert edital.data_encerramento_proposta is None
    assert edital.valor_estimado is None
    assert edital.uf is None
    assert edital.municipio is None


def test_cai_para_data_atualizacao_se_global_faltar(
    sessao: Session, registro: dict[str, Any]
) -> None:
    del registro["dataAtualizacaoGlobal"]

    registrar(sessao, [registro])

    edital = sessao.get(Edital, ("pncp", CHAVE))
    assert edital is not None
    assert edital.data_atualizacao.isoformat() == registro["dataAtualizacao"]


def test_registro_invalido_nao_derruba_lote(
    sessao: Session, registro: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    sem_chave = copy.deepcopy(registro)
    del sem_chave["numeroControlePNCP"]

    with caplog.at_level("WARNING"):
        resultado = registrar(sessao, [sem_chave, registro])

    assert resultado.invalidos == 1
    assert resultado.inseridos == 1
    assert any(r.getMessage() == "registro_invalido" for r in caplog.records)


def test_registro_que_falha_na_validacao_e_invalido(
    sessao: Session, registro: dict[str, Any]
) -> None:
    quebrado = copy.deepcopy(registro)
    quebrado["numeroControlePNCP"] = "OUTRO/2025"
    quebrado["dataPublicacaoPncp"] = "nao-e-data"

    resultado = registrar(sessao, [quebrado])

    assert resultado.invalidos == 1
    assert _contar(sessao) == 0


def test_duplicado_no_mesmo_lote_fica_com_o_mais_novo(
    sessao: Session, registro: dict[str, Any]
) -> None:
    velha = copy.deepcopy(registro)
    velha["objetoCompra"] = "VELHO"
    velha["dataAtualizacaoGlobal"] = "2026-11-20T08:00:00"
    nova = copy.deepcopy(registro)
    nova["objetoCompra"] = "NOVO"
    nova["dataAtualizacaoGlobal"] = "2026-11-27T08:00:00"

    resultado = registrar(sessao, [nova, velha])

    assert (resultado.inseridos, resultado.ignorados) == (1, 1)
    assert sessao.get(Edital, ("pncp", CHAVE)).objeto == "NOVO"  # type: ignore[union-attr]
