import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from radar.editais import Criterios, carregar_criterios, filtrar, listar_abertos, registrar

RAIZ = Path(__file__).resolve().parent.parent.parent
AMOSTRA: dict[str, Any] = json.loads(
    (RAIZ / "tests" / "dados" / "registro_pregao.json").read_text(encoding="utf-8")
)
FUSO = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 11, 26, 12, 0, tzinfo=FUSO)

CRITERIOS = Criterios(
    linhas={
        "perdas": ["hidrometro", "vazamento"],
        "comercial": ["leitura de hidrometro", "leitura"],
        "redes": ["rede de agua"],
    },
    ufs=["SP", "PR"],
    modalidades=[6],
)


def _registro(
    numero: str,
    objeto: str,
    uf: str = "SP",
    encerramento: str | None = "2026-12-11T10:00:00",
    situacao_id: int = 1,
) -> dict[str, Any]:
    registro = copy.deepcopy(AMOSTRA)
    registro["numeroControlePNCP"] = numero
    registro["objetoCompra"] = objeto
    registro["unidadeOrgao"]["ufSigla"] = uf
    registro["dataEncerramentoProposta"] = encerramento
    registro["situacaoCompraId"] = situacao_id
    return registro


def test_termo_sem_acento_acha_texto_com_acento(sessao: Session) -> None:
    registrar(
        sessao,
        [_registro("1/2026", "Prestação de serviços de LEITURA DE HIDRÔMETROS")],
    )

    resultado = filtrar(sessao, CRITERIOS, AGORA)

    assert [r.id_na_fonte for r in resultado] == ["1/2026"]
    assert set(resultado[0].linhas) == {"comercial", "perdas"}


def test_filtra_uf(sessao: Session) -> None:
    objeto = "Manutenção de rede de água"
    registrar(sessao, [_registro("sp/2026", objeto, "SP"), _registro("ba/2026", objeto, "BA")])

    resultado = filtrar(sessao, CRITERIOS, AGORA)

    assert [r.id_na_fonte for r in resultado] == ["sp/2026"]


def test_somente_abertos(sessao: Session) -> None:
    registrar(
        sessao,
        [_registro("velho/2026", "Conserto de vazamento", encerramento="2026-11-25T10:00:00")],
    )

    assert filtrar(sessao, CRITERIOS, AGORA) == []
    assert len(filtrar(sessao, CRITERIOS, AGORA, somente_abertos=False)) == 1


def test_nao_casa_termo_solto(sessao: Session) -> None:
    registrar(sessao, [_registro("onibus/2026", "Contratação de locação de ônibus")])

    assert filtrar(sessao, CRITERIOS, AGORA) == []


def test_carregar_criterios() -> None:
    criterios = carregar_criterios(RAIZ / "config" / "filtro.toml")

    assert len(criterios.linhas) == 4
    assert len(criterios.ufs) == 4


def test_listar_abertos_ignora_texto_e_respeita_uf_prazo_e_limite(sessao: Session) -> None:
    registrar(
        sessao,
        [
            _registro("tarde/2026", "Compra de canetas", encerramento="2026-12-20T10:00:00"),
            _registro("cedo/2026", "Conserto de vazamento", encerramento="2026-12-01T10:00:00"),
            _registro("velho/2026", "Compra de papel", encerramento="2026-11-20T10:00:00"),
            _registro("ba/2026", "Compra de lapis", "BA", encerramento="2026-12-02T10:00:00"),
        ],
    )

    resultado = listar_abertos(sessao, CRITERIOS, AGORA, limite=10)

    assert [r.id_na_fonte for r in resultado] == ["cedo/2026", "tarde/2026"]
    assert resultado[0].linhas == ["perdas"]
    assert resultado[1].linhas == []
    assert len(listar_abertos(sessao, CRITERIOS, AGORA, limite=1)) == 1
