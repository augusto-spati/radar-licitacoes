import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from radar.editais import (
    Criterios,
    ItemResultado,
    ResultadoBusca,
    ResultadoItem,
    alvos_resultado,
    gravar_resultados,
    registrar,
)

RAIZ = Path(__file__).resolve().parent.parent.parent
AMOSTRA: dict[str, Any] = json.loads(
    (RAIZ / "tests" / "dados" / "registro_pregao.json").read_text(encoding="utf-8")
)
FUSO = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 11, 26, 12, 0, tzinfo=FUSO)
CRITERIOS = Criterios(linhas={"perdas": ["hidrometro"]}, ufs=["SP"], modalidades=[6])
CNPJ = "12345678000199"


def _id(seq: int) -> str:
    return f"{CNPJ}-1-{seq:06d}/2026"


def _registro(seq: int, dias_desde_encerramento: int) -> dict[str, Any]:
    r = copy.deepcopy(AMOSTRA)
    r["numeroControlePNCP"] = _id(seq)
    r["objetoCompra"] = "Leitura de hidrometro"
    r["unidadeOrgao"]["ufSigla"] = "SP"
    fim = AGORA.replace(tzinfo=None) - timedelta(days=dias_desde_encerramento)
    r["dataEncerramentoProposta"] = fim.isoformat()
    return r


def _item(situacao: str = "homologado") -> ItemResultado:
    return ItemResultado(1, "d", None, None, None, None, False, situacao)


def test_alvos(sessao: Session) -> None:
    # 1 aberto, 2 encerrado há 10 dias sem busca, 3 buscado ontem, 4 parcial buscado há 8 dias,
    # 5 encerrado há 200 dias, 6 de outra fonte.
    dias = {1: -5, 2: 10, 3: 10, 4: 10, 5: 200, 6: 10}
    registrar(sessao, [_registro(n, d) for n, d in dias.items()])
    sessao.execute(
        text("UPDATE edital SET fonte='sanepar' WHERE id_na_fonte=:i").bindparams(i=_id(6))
    )
    gravar_resultados(sessao, "pncp", _id(3), [_item("homologado")], AGORA - timedelta(days=1))
    gravar_resultados(sessao, "pncp", _id(4), [_item("em_andamento")], AGORA - timedelta(days=8))
    sessao.flush()

    alvos = alvos_resultado(sessao, CRITERIOS, AGORA, 10)

    assert [a.id_na_fonte for a in alvos] == [_id(2), _id(4)]  # nunca buscado primeiro
    assert (alvos[0].cnpj, alvos[0].ano, alvos[0].sequencial) == (CNPJ, 2026, 2)
    assert len(alvos_resultado(sessao, CRITERIOS, AGORA, 1)) == 1


def test_gravar_duas_vezes_nao_duplica(sessao: Session) -> None:
    registrar(sessao, [_registro(9, 10)])
    gravar_resultados(sessao, "pncp", _id(9), [_item("em_andamento")], AGORA)
    resumo = gravar_resultados(sessao, "pncp", _id(9), [_item()], AGORA + timedelta(days=1))
    sessao.flush()

    assert resumo.situacao == "homologada"
    assert sessao.scalar(select(func.count()).select_from(ResultadoItem)) == 1
    assert sessao.scalar(select(ResultadoItem.situacao)) == "homologado"
    busca = sessao.scalars(select(ResultadoBusca)).one()
    assert busca.situacao == "homologada" and busca.itens == 1
