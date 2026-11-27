"""Cadeia de fatias pela fila local equivale ao laço local sem orçamento."""

import copy
import json
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fakes import Chave, ClienteFalso, como_cliente
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from radar.coleta import coletar_pendentes, planejar_janelas
from radar.coleta.modelos import ColetaJanela
from radar.coleta.pncp import Pagina
from radar.comum import Base, Mensagem, Outbox, fabrica_sessao, publicar_outbox
from radar.editais import Criterios, filtrar
from radar.editais.modelos import Edital
from radar.processador import FilaLocal, planejar_e_enfileirar, processar_janela

RAIZ = Path(__file__).resolve().parent.parent.parent
AMOSTRA: dict[str, Any] = json.loads(
    (RAIZ / "tests" / "dados" / "registro_pregao.json").read_text(encoding="utf-8")
)
BRT = ZoneInfo("America/Sao_Paulo")
HOJE = date(2026, 11, 10)
AGORA = datetime(2026, 11, 12, 12, 0, tzinfo=BRT)  # o dia de HOJE já acabou: nada reabre
RECUO = 2
CRITERIOS = Criterios(linhas={"perdas": ["hidrometro"]}, ufs=["SP"], modalidades=[5, 6])
PAGINAS = 3


def _roteiro() -> dict[Chave, Pagina | BaseException]:
    roteiro: dict[Chave, Pagina | BaseException] = {}
    for n in range(RECUO + 1):
        dia = HOJE - timedelta(days=n)
        for modalidade in CRITERIOS.modalidades:
            numeros = [f"{dia:%Y%m%d}-{modalidade}-{i}/2026" for i in range(PAGINAS * 2)]
            # Um registro repetido entre páginas, como o PNCP faz quando a lista muda.
            numeros[2] = numeros[1]
            for p in range(1, PAGINAS + 1):
                registros = []
                for numero in numeros[(p - 1) * 2 : p * 2]:
                    registro = copy.deepcopy(AMOSTRA)
                    registro["numeroControlePNCP"] = numero
                    registro["objetoCompra"] = f"Leitura de hidrômetros {numero}"
                    registro["unidadeOrgao"]["ufSigla"] = "SP"
                    registros.append(registro)
                bruto = {
                    "data": registros,
                    "totalPaginas": PAGINAS,
                    "numeroPagina": p,
                    "totalRegistros": PAGINAS * 2 - 1,
                }
                roteiro[("atualizacao", dia, dia, modalidade, p)] = Pagina(
                    registros=registros, total_paginas=PAGINAS, numero_pagina=p, bruto=bruto
                )
    return roteiro


def _truncar(engine: Engine) -> None:
    tabelas = ", ".join(tabela.name for tabela in Base.metadata.sorted_tables)
    with engine.begin() as conexao:
        conexao.execute(text(f"TRUNCATE {tabelas} RESTART IDENTITY CASCADE"))


@pytest.fixture
def fabrica(engine: Engine) -> Iterator[sessionmaker[Session]]:
    _truncar(engine)
    yield fabrica_sessao(engine)
    _truncar(engine)


def _retrato(fabrica: sessionmaker[Session]) -> dict[str, Any]:
    with fabrica() as sessao:
        janelas = sessao.execute(
            select(
                ColetaJanela.data_ini,
                ColetaJanela.modalidade,
                ColetaJanela.status,
                ColetaJanela.paginas,
                ColetaJanela.registros,
            ).order_by(ColetaJanela.data_ini, ColetaJanela.modalidade)
        ).all()
        editais = sessao.scalar(select(func.count()).select_from(Edital))
        filtrados = [
            e.id_na_fonte for e in filtrar(sessao, CRITERIOS, AGORA, somente_abertos=False)
        ]
        pendentes = sessao.scalar(
            select(func.count()).select_from(Outbox).where(Outbox.publicada_em.is_(None))
        )
    return {
        "janelas": [tuple(j) for j in janelas],
        "editais": editais,
        "filtrados": sorted(filtrados),
        "outbox_pendente": pendentes,
    }


def test_cadeia_local_equivale_ao_laco(fabrica: sessionmaker[Session]) -> None:
    # Cadeia: planejador + relay com a fila local; orçamento zero = uma página por fatia.
    cadeia = ClienteFalso(_roteiro())

    def coletora(sessao: Session, mensagem: Mensagem) -> str:
        return processar_janela(sessao, mensagem, como_cliente(cadeia), uuid4().hex, 0.0).desfecho

    fila = FilaLocal(fabrica, {}, lambda: AGORA, coletora=coletora)
    with fabrica() as sessao:
        assert planejar_e_enfileirar(sessao, CRITERIOS, HOJE, recuo=RECUO) == (6, 6)
        publicar_outbox(sessao, fila)
    pela_cadeia = _retrato(fabrica)
    with fabrica() as sessao:
        fatias = sessao.scalars(select(ColetaJanela.lote)).all()
    _truncar(fabrica.kw["bind"])

    # Laço: o mesmo dia a dia no próprio processo, sem orçamento.
    laco = ClienteFalso(_roteiro())
    fila_laco = FilaLocal(fabrica, {}, lambda: AGORA)
    with fabrica() as sessao:
        planejar_janelas(
            sessao, "atualizacao", HOJE - timedelta(days=RECUO), HOJE, CRITERIOS.modalidades
        )
        resumo = coletar_pendentes(sessao, como_cliente(laco), AGORA)
        publicar_outbox(sessao, fila_laco)
    pelo_laco = _retrato(fabrica)

    assert fila.falhas == fila_laco.falhas == 0
    assert set(fatias) == {PAGINAS - 1}  # cada janela andou em 3 fatias (lotes 0, 1 e 2)
    assert sorted(cadeia.chamadas) == sorted(laco.chamadas)  # nenhuma página pedida duas vezes
    assert len(cadeia.chamadas) == 6 * PAGINAS
    assert (resumo.concluidas, resumo.falhas) == (6, 0)
    assert pela_cadeia == pelo_laco
    assert pela_cadeia["editais"] == 6 * (PAGINAS * 2 - 1)
    assert {j[2] for j in pela_cadeia["janelas"]} == {"concluida"}
    assert pela_cadeia["outbox_pendente"] == 0
    assert len(pela_cadeia["filtrados"]) == pela_cadeia["editais"]


def test_conferencia_da_continuacao_na_lambda(
    fabrica: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    import radar.processador.lambda_coletora as lambda_coletora

    monkeypatch.setattr(lambda_coletora, "_criar_sessao", fabrica)
    with fabrica() as sessao:
        publicada = Outbox(tipo="coletar_janela", carga={}, publicada_em=AGORA)
        pendente = Outbox(tipo="coletar_janela", carga={})
        sessao.add_all([publicada, pendente])
        sessao.commit()
        ids = {publicada.id, pendente.id}
        esperado = {pendente.id}

    assert lambda_coletora._nao_publicadas(ids) == esperado
