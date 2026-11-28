"""Frescor por fonte: só conta o que já chegou a edital."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from radar.coleta import FalhaColeta, falhas_de_coleta, frescor_por_fonte
from radar.coleta.modelos import ColetaJanela, ColetaRetrato, PaginaBruta

AGORA = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def _retrato(
    sessao: Session, horas: float, processado: bool, status: str = "concluido"
) -> ColetaRetrato:
    retrato = ColetaRetrato(
        fonte="sanepar",
        status=status,
        iniciado_em=AGORA - timedelta(hours=horas),
        processado_em=AGORA if processado else None,
    )
    sessao.add(retrato)
    sessao.flush()
    return retrato


def _janela(sessao: Session, dia: date, horas: float, pendente: bool) -> ColetaJanela:
    janela = ColetaJanela(
        endpoint="atualizacao",
        data_ini=dia,
        data_fim=dia,
        modalidade=6,
        status="concluida",
        paginas=1,
        registros=1,
        concluida_em=AGORA - timedelta(hours=horas),
    )
    sessao.add(janela)
    sessao.flush()
    sessao.add(
        PaginaBruta(
            janela_id=janela.id,
            numero_pagina=1,
            conteudo={"data": []},
            coletada_em=AGORA,
            processada_em=None if pendente else AGORA,
        )
    )
    sessao.flush()
    return janela


def test_frescor_retrato_processado(sessao: Session) -> None:
    _retrato(sessao, 10, processado=True)
    assert frescor_por_fonte(sessao, AGORA, ["sanepar"]) == {"sanepar": 10.0}


def test_retrato_concluido_nao_processado_nao_conta(sessao: Session) -> None:
    _retrato(sessao, 30, processado=True)
    _retrato(sessao, 2, processado=False)
    _retrato(sessao, 1, processado=True, status="falha")
    assert frescor_por_fonte(sessao, AGORA, ["sanepar"]) == {"sanepar": 30.0}


def test_frescor_pncp_ignora_janela_com_pagina_pendente(sessao: Session) -> None:
    _janela(sessao, date(2026, 11, 25), 20, pendente=False)
    _janela(sessao, date(2026, 11, 26), 3, pendente=True)
    assert frescor_por_fonte(sessao, AGORA, ["pncp"]) == {"pncp": 20.0}


def test_fonte_sem_dado_e_9999(sessao: Session) -> None:
    assert frescor_por_fonte(sessao, AGORA, ["pncp", "compesa"]) == {
        "pncp": 9999.0,
        "compesa": 9999.0,
    }


def test_fonte_fresca_e_ok_nao_aparece(sessao: Session) -> None:
    _retrato(sessao, 5, processado=True)
    assert falhas_de_coleta(sessao, AGORA, ["sanepar"]) == []


def test_fonte_parada_ha_mais_de_36h_e_atrasada(sessao: Session) -> None:
    _retrato(sessao, 37, processado=True)
    assert falhas_de_coleta(sessao, AGORA, ["sanepar"]) == [
        FalhaColeta("sanepar", "atrasado", AGORA - timedelta(hours=37))
    ]


def test_36h_exatas_nao_e_atrasada(sessao: Session) -> None:
    _retrato(sessao, 36, processado=True)
    assert falhas_de_coleta(sessao, AGORA, ["sanepar"]) == []


def test_fonte_que_nunca_rodou_e_atrasada_sem_data(sessao: Session) -> None:
    assert falhas_de_coleta(sessao, AGORA, ["compesa"]) == [
        FalhaColeta("compesa", "atrasado", None)
    ]


def test_ultima_leitura_em_falha_com_dado_fresco(sessao: Session) -> None:
    _retrato(sessao, 2, processado=True)
    _retrato(sessao, 1, processado=True, status="falha")
    assert falhas_de_coleta(sessao, AGORA, ["sanepar"]) == [
        FalhaColeta("sanepar", "falha", AGORA - timedelta(hours=2))
    ]


def test_retrato_em_andamento_nao_conta_como_falha(sessao: Session) -> None:
    _retrato(sessao, 2, processado=True)
    _retrato(sessao, 1, processado=False, status="em_andamento")
    assert falhas_de_coleta(sessao, AGORA, ["sanepar"]) == []


def test_pncp_com_ultima_janela_em_falha(sessao: Session) -> None:
    _janela(sessao, date(2026, 11, 26), 5, pendente=False)
    falha = ColetaJanela(
        endpoint="atualizacao",
        data_ini=date(2026, 11, 27),
        data_fim=date(2026, 11, 27),
        modalidade=6,
        status="falha",
        paginas=0,
        registros=0,
        concluida_em=AGORA - timedelta(hours=1),
    )
    sessao.add(falha)
    sessao.flush()
    assert falhas_de_coleta(sessao, AGORA, ["pncp"]) == [
        FalhaColeta("pncp", "falha", AGORA - timedelta(hours=5))
    ]
