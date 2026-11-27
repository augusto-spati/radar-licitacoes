import copy
import json
from datetime import datetime, timedelta
from decimal import Decimal
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


def _item(
    situacao: str = "homologado", numero: int = 1, com_vencedor: bool = False
) -> ItemResultado:
    extra: dict[str, Any] = {}
    if com_vencedor:
        extra = {
            "fornecedor_ni": "11111111000111",
            "fornecedor_nome": "Acme",
            "valor_total_homologado": Decimal("80"),
        }
    return ItemResultado(numero, "d", None, None, None, Decimal("100"), False, situacao, **extra)


def test_alvos(sessao: Session) -> None:
    # 1 aberto, 2 encerrado há 10 dias sem busca, 3 buscado ontem, 4 parcial buscado há 8 dias,
    # 5 encerrado há 200 dias e nunca buscado (entra: carga histórica), 6 de outra fonte,
    # 7 encerrado há 200 dias, parcial buscado há 8 dias (fora da janela de rebusca).
    dias = {1: -5, 2: 10, 3: 10, 4: 10, 5: 200, 6: 10, 7: 200}
    registrar(sessao, [_registro(n, d) for n, d in dias.items()])
    sessao.execute(
        text("UPDATE edital SET fonte='sanepar' WHERE id_na_fonte=:i").bindparams(i=_id(6))
    )
    gravar_resultados(sessao, "pncp", _id(3), [_item("homologado")], AGORA - timedelta(days=1))
    parcial = [_item("homologado", 1), _item("em_andamento", 2)]
    assert (
        gravar_resultados(sessao, "pncp", _id(4), parcial, AGORA - timedelta(days=8)).situacao
        == "parcial"
    )
    gravar_resultados(sessao, "pncp", _id(7), parcial, AGORA - timedelta(days=8))
    sessao.flush()

    alvos = alvos_resultado(sessao, CRITERIOS, AGORA, 10)

    # Nunca buscados primeiro (o mais recente antes), depois a rebusca.
    assert [a.id_na_fonte for a in alvos] == [_id(2), _id(5), _id(4)]
    assert (alvos[0].cnpj, alvos[0].ano, alvos[0].sequencial) == (CNPJ, 2026, 2)
    assert len(alvos_resultado(sessao, CRITERIOS, AGORA, 1)) == 1


def test_homologada_sem_vencedor_e_rebuscada(sessao: Session) -> None:
    registrar(sessao, [_registro(9, 10), _registro(8, 10)])
    gravar_resultados(sessao, "pncp", _id(9), [_item("homologado")], AGORA - timedelta(days=8))
    gravar_resultados(sessao, "pncp", _id(8), [_item("homologado")], AGORA - timedelta(days=1))
    sessao.flush()

    assert [a.id_na_fonte for a in alvos_resultado(sessao, CRITERIOS, AGORA, 10)] == [_id(9)]


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


def test_lista_vazia_nao_apaga_o_que_ja_estava_gravado(sessao: Session) -> None:
    registrar(sessao, [_registro(9, 10)])
    gravar_resultados(sessao, "pncp", _id(9), [_item(com_vencedor=True)], AGORA)
    resumo = gravar_resultados(sessao, "pncp", _id(9), [], AGORA + timedelta(days=8))
    sessao.flush()

    assert resumo.situacao == "homologada" and resumo.vencedor_nome == "Acme"
    assert sessao.scalar(select(func.count()).select_from(ResultadoItem)) == 1
    busca = sessao.scalars(select(ResultadoBusca)).one()
    assert busca.situacao == "homologada" and busca.itens == 1
    assert busca.buscado_em == AGORA + timedelta(days=8)


def test_lista_vazia_sem_nada_gravado_vira_sem_itens_e_e_rebuscada(sessao: Session) -> None:
    registrar(sessao, [_registro(9, 10)])
    resumo = gravar_resultados(sessao, "pncp", _id(9), [], AGORA - timedelta(days=8))
    sessao.flush()

    assert resumo.situacao == "sem_itens"
    assert [a.id_na_fonte for a in alvos_resultado(sessao, CRITERIOS, AGORA, 10)] == [_id(9)]


def test_item_que_volta_sem_resultado_nao_perde_o_vencedor(sessao: Session) -> None:
    registrar(sessao, [_registro(9, 10)])
    gravar_resultados(sessao, "pncp", _id(9), [_item(com_vencedor=True)], AGORA)
    resumo = gravar_resultados(sessao, "pncp", _id(9), [_item()], AGORA + timedelta(days=1))
    sessao.flush()

    assert resumo.desconto == Decimal("0.2000") and resumo.vencedor_nome == "Acme"
    item = sessao.scalars(select(ResultadoItem)).one()
    assert item.fornecedor_ni == "11111111000111" and item.valor_total_homologado == 80


def test_numero_de_item_repetido_no_lote_vale_o_ultimo(sessao: Session) -> None:
    registrar(sessao, [_registro(9, 10)])
    gravar_resultados(sessao, "pncp", _id(9), [_item("em_andamento"), _item("homologado")], AGORA)
    sessao.flush()

    assert sessao.scalar(select(ResultadoItem.situacao)) == "homologado"
