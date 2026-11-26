import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from radar.api.exemplo import exportar_exemplo
from radar.api.modelos import EditalDetalheOut
from radar.editais import Criterios, registrar

RAIZ = Path(__file__).resolve().parent.parent.parent
AMOSTRA: dict[str, Any] = json.loads(
    (RAIZ / "tests" / "dados" / "registro_pregao.json").read_text(encoding="utf-8")
)
AGORA = datetime(2026, 11, 26, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
CRITERIOS = Criterios(linhas={"perdas": ["vazamento"]}, ufs=["SP"], modalidades=[6])


def _registro(numero: str, objeto: str, encerramento: str) -> dict[str, Any]:
    registro = copy.deepcopy(AMOSTRA)
    registro["numeroControlePNCP"] = numero
    registro["objetoCompra"] = objeto
    registro["unidadeOrgao"]["ufSigla"] = "SP"
    registro["dataEncerramentoProposta"] = encerramento
    registro["situacaoCompraId"] = 1
    return registro


@pytest.mark.xfail(
    strict=True, reason="exemplo.json é regerado no formato multi-fonte na Task 2 da etapa 3"
)
def test_exemplo_valida_contra_modelos() -> None:
    caminho = RAIZ / "frontend" / "src" / "dados" / "exemplo.json"
    dados = json.loads(caminho.read_text(encoding="utf-8"))

    assert set(dados) == {"atualizado_em", "editais"}
    assert dados["editais"]
    for item in dados["editais"]:
        EditalDetalheOut.model_validate(item)


def test_exporta_casados_e_completa_com_outros_abertos(sessao: Session) -> None:
    registrar(
        sessao,
        [
            _registro("casa/2026", "Conserto de vazamento", "2026-12-10T10:00:00"),
            _registro("outro-cedo/2026", "Compra de canetas", "2026-12-01T10:00:00"),
            _registro("outro-tarde/2026", "Compra de papel", "2026-12-20T10:00:00"),
            _registro("fechado/2026", "Compra de lapis", "2026-11-01T10:00:00"),
        ],
    )

    dados = exportar_exemplo(sessao, CRITERIOS, AGORA, maximo=3)

    assert dados["atualizado_em"] is None
    editais = dados["editais"]
    assert [e["id_publico"] for e in editais] == [
        "pncp~outro-cedo/2026",
        "pncp~casa/2026",
        "pncp~outro-tarde/2026",
    ]
    assert [e["linhas"] for e in editais] == [[], ["perdas"], []]
    for item in editais:
        EditalDetalheOut.model_validate(item)


def test_respeita_o_maximo_priorizando_os_casados(sessao: Session) -> None:
    registrar(
        sessao,
        [
            _registro("casa/2026", "Conserto de vazamento", "2026-12-10T10:00:00"),
            _registro("outro/2026", "Compra de canetas", "2026-12-01T10:00:00"),
        ],
    )

    dados = exportar_exemplo(sessao, CRITERIOS, AGORA, maximo=1)

    assert [e["id_publico"] for e in dados["editais"]] == ["pncp~casa/2026"]
