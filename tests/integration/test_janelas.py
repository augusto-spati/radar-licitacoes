import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fakes import VAZIA, Chave, ClienteFalso, como_cliente, pagina
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.coleta import (
    ErroPNCP,
    JanelaParada,
    auditar,
    coletar_pendentes,
    enfileirar_elegiveis,
    janelas_com_lease_vivo,
    janelas_paradas,
    ler_paginas_nao_processadas,
    marcar_processada,
    planejar_janelas,
)
from radar.coleta.janelas import ResumoColeta
from radar.coleta.modelos import ColetaJanela, PaginaBruta
from radar.comum import Outbox

BRT = ZoneInfo("America/Sao_Paulo")
D1 = date(2026, 11, 1)
AGORA = datetime(2026, 11, 20, 12, 0, tzinfo=BRT)


def janela(sessao: Session, **campos: object) -> ColetaJanela:
    base: dict[str, object] = {
        "endpoint": "publicacao",
        "data_ini": D1,
        "data_fim": D1,
        "modalidade": 6,
        "status": "pendente",
        "paginas": 0,
        "registros": 0,
    }
    obj = ColetaJanela(**{**base, **campos})
    sessao.add(obj)
    sessao.flush()
    return obj


def contar(sessao: Session, modelo: type) -> int:
    return sessao.scalar(select(func.count()).select_from(modelo)) or 0


def test_planejar_cria_dia_por_modalidade(sessao: Session) -> None:
    criadas = planejar_janelas(sessao, "publicacao", D1, date(2026, 11, 3), [6, 8])
    assert criadas == 6
    assert planejar_janelas(sessao, "publicacao", D1, date(2026, 11, 3), [6, 8]) == 0
    assert contar(sessao, ColetaJanela) == 6


def test_coleta_tres_paginas(sessao: Session) -> None:
    planejar_janelas(sessao, "publicacao", D1, D1, [6])
    falso = ClienteFalso({("publicacao", D1, D1, 6, n): pagina(n, 3) for n in (1, 2, 3)})
    resumo = coletar_pendentes(sessao, como_cliente(falso), AGORA)
    j = sessao.scalars(select(ColetaJanela)).one()
    assert (j.status, j.paginas, j.registros, j.tentativas) == ("concluida", 3, 6, 1)
    assert j.concluida_em is not None
    assert j.iniciada_em is not None
    assert contar(sessao, PaginaBruta) == 3
    assert (resumo.concluidas, resumo.falhas, resumo.divididas, resumo.registros) == (1, 0, 0, 6)


def test_dia_sem_registros_conclui_com_zero(sessao: Session) -> None:
    planejar_janelas(sessao, "publicacao", D1, D1, [6])
    falso = ClienteFalso({("publicacao", D1, D1, 6, 1): VAZIA})
    coletar_pendentes(sessao, como_cliente(falso), AGORA)
    j = sessao.scalars(select(ColetaJanela)).one()
    assert (j.status, j.registros, j.paginas) == ("concluida", 0, 0)
    assert contar(sessao, PaginaBruta) == 0


def test_falha_em_pagina_profunda(sessao: Session) -> None:
    planejar_janelas(sessao, "publicacao", D1, D1, [6])

    def chave(n: int) -> Chave:
        return ("publicacao", D1, D1, 6, n)

    falso = ClienteFalso({chave(1): pagina(1, 2), chave(2): ErroPNCP("HTTP 500", 500)})
    resumo = coletar_pendentes(sessao, como_cliente(falso), AGORA)
    j = sessao.scalars(select(ColetaJanela)).one()
    assert j.status == "falha"
    assert "HTTP 500" in (j.erro or "")
    assert contar(sessao, PaginaBruta) == 1
    assert (resumo.concluidas, resumo.falhas) == (0, 1)
    assert len(falso.chamadas) == 2  # a falha não é repetida na mesma chamada

    falso.roteiro[chave(2)] = pagina(2, 2)
    coletar_pendentes(sessao, como_cliente(falso), AGORA)
    sessao.refresh(j)
    assert (j.status, j.paginas, j.registros, j.tentativas, j.erro) == ("concluida", 2, 4, 2, None)
    assert contar(sessao, PaginaBruta) == 2


def test_falha_pagina_um_em_janela_de_varios_dias_divide(sessao: Session) -> None:
    fim = date(2026, 11, 4)
    janela(sessao, data_ini=D1, data_fim=fim)
    falso = ClienteFalso(
        {
            ("publicacao", D1, fim, 6, 1): ErroPNCP("HTTP 500", 500),
            ("publicacao", D1, date(2026, 11, 2), 6, 1): VAZIA,
            ("publicacao", date(2026, 11, 3), fim, 6, 1): VAZIA,
        }
    )
    resumo = coletar_pendentes(sessao, como_cliente(falso), AGORA)
    janelas = sessao.scalars(select(ColetaJanela).order_by(ColetaJanela.data_ini)).all()
    assert [(j.data_ini.day, j.data_fim.day, j.status) for j in janelas] == [
        (1, 2, "concluida"),
        (3, 4, "concluida"),
    ]
    assert (resumo.divididas, resumo.concluidas, resumo.falhas) == (1, 2, 0)


def test_em_andamento_e_retomada(sessao: Session) -> None:
    j = janela(sessao, status="em_andamento", tentativas=1)
    falso = ClienteFalso({("publicacao", D1, D1, 6, 1): pagina(1, 1)})
    coletar_pendentes(sessao, como_cliente(falso), AGORA)
    sessao.refresh(j)
    assert (j.status, j.tentativas) == ("concluida", 2)


def test_atualizacao_do_dia_corrente_recoleta(sessao: Session) -> None:
    hoje = date(2026, 11, 10)
    anteontem = hoje - timedelta(days=2)
    dez_hoje = datetime(2026, 11, 10, 10, 0, tzinfo=BRT)
    dez_ontem = datetime(2026, 11, 9, 10, 0, tzinfo=BRT)
    corrente = janela(
        sessao,
        endpoint="atualizacao",
        data_ini=hoje,
        data_fim=hoje,
        status="concluida",
        concluida_em=dez_hoje,
    )
    antiga = janela(
        sessao,
        endpoint="atualizacao",
        data_ini=anteontem,
        data_fim=anteontem,
        status="concluida",
        concluida_em=dez_ontem,
    )
    falso = ClienteFalso({("atualizacao", hoje, hoje, 6, 1): pagina(1, 1)})
    amanha = datetime(2026, 11, 11, 10, 0, tzinfo=BRT)
    resumo = coletar_pendentes(sessao, como_cliente(falso), amanha, relogio=lambda: amanha)
    assert falso.chamadas == [("atualizacao", hoje, hoje, 6, 1)]
    assert resumo.concluidas == 1
    sessao.refresh(corrente)
    sessao.refresh(antiga)
    assert corrente.concluida_em == amanha
    assert antiga.concluida_em == dez_ontem


def test_publicacao_concluida_nao_recoleta(sessao: Session) -> None:
    janela(sessao, status="concluida", concluida_em=AGORA - timedelta(days=30))
    falso = ClienteFalso({})
    coletar_pendentes(sessao, como_cliente(falso), AGORA)
    assert falso.chamadas == []


def test_paginas_nao_processadas_e_marcacao(sessao: Session) -> None:
    planejar_janelas(sessao, "publicacao", D1, D1, [6])
    falso = ClienteFalso({("publicacao", D1, D1, 6, n): pagina(n, 2) for n in (1, 2)})
    coletar_pendentes(sessao, como_cliente(falso), AGORA)
    lidas = ler_paginas_nao_processadas(sessao)
    assert len(lidas) == 2
    assert lidas[0][1] == [{"numeroControlePNCP": "1-0"}, {"numeroControlePNCP": "1-1"}]
    marcar_processada(sessao, lidas[0][0])
    assert [p for p, _ in ler_paginas_nao_processadas(sessao)] == [lidas[1][0]]
    assert ler_paginas_nao_processadas(sessao, limite=0) == []

    # recoletar a janela limpa processada_em
    j = sessao.scalars(select(ColetaJanela)).one()
    j.status = "pendente"
    sessao.commit()
    coletar_pendentes(sessao, como_cliente(falso), AGORA)
    assert len(ler_paginas_nao_processadas(sessao)) == 2


def _mensagens(sessao: Session) -> list[tuple[str, dict[str, int]]]:
    return [(m.tipo, m.carga) for m in sessao.scalars(select(Outbox).order_by(Outbox.id))]


def _ids_das_paginas(sessao: Session) -> list[int]:
    return list(sessao.scalars(select(PaginaBruta.id).order_by(PaginaBruta.numero_pagina)))


def test_pagina_gravada_enfileira_mensagem(sessao: Session) -> None:
    planejar_janelas(sessao, "publicacao", D1, D1, [6])
    falso = ClienteFalso({("publicacao", D1, D1, 6, n): pagina(n, 2) for n in (1, 2)})
    coletar_pendentes(sessao, como_cliente(falso), AGORA)
    ids = _ids_das_paginas(sessao)
    assert _mensagens(sessao) == [("pagina_coletada", {"pagina_id": i}) for i in ids]

    # Regravar a mesma página (upsert) volta processada_em a NULL e enfileira de novo.
    marcar_processada(sessao, ids[0])
    j = sessao.scalars(select(ColetaJanela)).one()
    j.status = "pendente"
    sessao.commit()
    coletar_pendentes(sessao, como_cliente(falso), AGORA)
    assert _ids_das_paginas(sessao) == ids
    assert _mensagens(sessao) == [("pagina_coletada", {"pagina_id": i}) for i in ids * 2]


def test_auditoria_lista_faltando(sessao: Session) -> None:
    d2 = date(2026, 11, 2)
    janela(sessao, data_ini=D1, data_fim=D1, modalidade=6, status="concluida")
    janela(sessao, data_ini=D1, data_fim=D1, modalidade=8, status="concluida")
    janela(sessao, data_ini=d2, data_fim=d2, modalidade=6, status="falha", erro="HTTP 500")
    janela(sessao, data_ini=d2, data_fim=d2, modalidade=8, status="pendente")
    aud = auditar(sessao, "publicacao", D1, d2, [6, 8])
    assert aud.esperadas == 4
    assert aud.concluidas == 2
    assert aud.faltando == [(d2, 8)]
    assert aud.falhas == [(d2, 6, "HTTP 500", 1, None)]


def test_auditoria_considera_janela_multidia(sessao: Session) -> None:
    janela(sessao, data_ini=D1, data_fim=date(2026, 11, 3), modalidade=6, status="concluida")
    aud = auditar(sessao, "publicacao", D1, date(2026, 11, 4), [6])
    assert aud.esperadas == 4
    assert aud.concluidas == 3
    assert aud.faltando == [(date(2026, 11, 4), 6)]


def _chave(n: int, d: date = D1) -> Chave:
    return ("publicacao", d, d, 6, n)


def _unica(sessao: Session) -> ColetaJanela:
    return sessao.scalars(select(ColetaJanela)).one()


def test_pagina_posterior_vazia_marca_falha(sessao: Session) -> None:
    planejar_janelas(sessao, "publicacao", D1, D1, [6])
    falso = ClienteFalso({_chave(1): pagina(1, 3), _chave(2): VAZIA})
    resumo = coletar_pendentes(sessao, como_cliente(falso), AGORA)
    j = _unica(sessao)
    assert j.status == "falha"
    assert "pagina 2 inconsistente: total_paginas=0, esperado 3" in (j.erro or "")
    assert contar(sessao, PaginaBruta) == 1
    assert (resumo.concluidas, resumo.falhas) == (0, 1)


def test_pagina_posterior_com_total_diferente_marca_falha(sessao: Session) -> None:
    planejar_janelas(sessao, "publicacao", D1, D1, [6])
    falso = ClienteFalso({_chave(1): pagina(1, 3), _chave(2): pagina(2, 2)})
    coletar_pendentes(sessao, como_cliente(falso), AGORA)
    j = _unica(sessao)
    assert j.status == "falha"
    assert "total_paginas=2, esperado 3" in (j.erro or "")


def test_menos_registros_que_o_total_marca_falha(sessao: Session) -> None:
    planejar_janelas(sessao, "publicacao", D1, D1, [6])
    falso = ClienteFalso({_chave(n): pagina(n, 2, total_registros=5) for n in (1, 2)})
    resumo = coletar_pendentes(sessao, como_cliente(falso), AGORA)
    j = _unica(sessao)
    assert (j.status, j.erro) == ("falha", "incompleta: 4 de 5 registros")
    assert contar(sessao, PaginaBruta) == 2
    assert resumo.falhas == 1


def test_total_registros_batendo_conclui(sessao: Session) -> None:
    planejar_janelas(sessao, "publicacao", D1, D1, [6])
    falso = ClienteFalso({_chave(n): pagina(n, 3, total_registros=6) for n in (1, 2, 3)})
    coletar_pendentes(sessao, como_cliente(falso), AGORA)
    assert _unica(sessao).status == "concluida"


def test_excecao_inesperada_marca_so_aquela_janela(sessao: Session) -> None:
    d2 = date(2026, 11, 2)
    planejar_janelas(sessao, "publicacao", D1, d2, [6])
    falso = ClienteFalso({_chave(1): RuntimeError("boom"), _chave(1, d2): pagina(1, 1)})
    resumo = coletar_pendentes(sessao, como_cliente(falso), AGORA)
    janelas = sessao.scalars(select(ColetaJanela).order_by(ColetaJanela.data_ini)).all()
    assert [j.status for j in janelas] == ["falha", "concluida"]
    assert "RuntimeError('boom')" in (janelas[0].erro or "")
    assert (resumo.concluidas, resumo.falhas) == (1, 1)


def test_janela_com_lease_vivo_noutro_processo_nao_coleta(sessao: Session) -> None:
    # Antes era o advisory lock global; agora o lease de cada janela (Lambda viva ou outro
    # processo local) faz o laço pular a janela.
    viva = datetime.now(UTC) + timedelta(hours=1)
    j = janela(sessao, status="em_andamento", travada_por="lambda", travada_ate=viva)
    falso = ClienteFalso({_chave(1): pagina(1, 1)})
    resumo = coletar_pendentes(sessao, como_cliente(falso), AGORA)
    assert falso.chamadas == []
    assert resumo == ResumoColeta()
    assert _unica(sessao).status == "em_andamento"
    j.travada_ate = datetime.now(UTC) - timedelta(hours=1)  # lease vencido: sobra retomada
    sessao.commit()
    coletar_pendentes(sessao, como_cliente(falso), AGORA)
    assert _unica(sessao).status == "concluida"


def test_excluir_pula_paginas_na_leitura(sessao: Session) -> None:
    planejar_janelas(sessao, "publicacao", D1, D1, [6])
    falso = ClienteFalso({_chave(n): pagina(n, 2) for n in (1, 2)})
    coletar_pendentes(sessao, como_cliente(falso), AGORA)
    ids = [p for p, _ in ler_paginas_nao_processadas(sessao)]
    assert [p for p, _ in ler_paginas_nao_processadas(sessao, excluir=[ids[0]])] == [ids[1]]


# --- planejador: reabrir e enfileirar; janelas paradas -----------------------------------

HOJE = date(2026, 11, 10)


def _coletar_janela(sessao: Session) -> list[dict[str, int]]:
    return list(
        sessao.scalars(
            select(Outbox.carga).where(Outbox.tipo == "coletar_janela").order_by(Outbox.id)
        )
    )


def test_enfileirar_reabre_concluida_cedo(sessao: Session) -> None:
    cedo = datetime(2026, 11, 10, 10, 0, tzinfo=BRT)  # antes de o dia 10 acabar em Brasília
    j = janela(
        sessao,
        endpoint="atualizacao",
        data_ini=HOJE,
        data_fim=HOJE,
        status="concluida",
        concluida_em=cedo,
        lote=2,
        proxima_pagina=4,
        total_paginas=3,
        esperados=6,
    )

    assert enfileirar_elegiveis(sessao, "atualizacao") == 1

    sessao.refresh(j)
    assert (j.status, j.proxima_pagina, j.total_paginas, j.esperados, j.lote) == (
        "pendente",
        1,
        None,
        None,
        3,
    )
    assert _coletar_janela(sessao) == [{"janela_id": j.id, "lote": 3}]


def test_enfileirar_nao_reabre_concluida_depois_do_dia(sessao: Session) -> None:
    depois = datetime(2026, 11, 11, 0, 30, tzinfo=BRT)
    j = janela(
        sessao,
        endpoint="atualizacao",
        data_ini=HOJE,
        data_fim=HOJE,
        status="concluida",
        concluida_em=depois,
    )

    assert enfileirar_elegiveis(sessao, "atualizacao") == 0

    sessao.refresh(j)
    assert (j.status, j.lote) == ("concluida", 0)
    assert _coletar_janela(sessao) == []


def test_enfileirar_pula_lease_vivo(sessao: Session) -> None:
    viva = datetime.now(UTC) + timedelta(hours=1)
    cedo = datetime(2026, 11, 10, 10, 0, tzinfo=BRT)
    andando = janela(
        sessao,
        endpoint="atualizacao",
        data_ini=HOJE,
        data_fim=HOJE,
        status="em_andamento",
        travada_por="lambda",
        travada_ate=viva,
    )
    # Concluída cedo, mas com lease vivo (estado estranho): reabrir atropelaria quem coleta.
    concluida = janela(
        sessao,
        endpoint="atualizacao",
        data_ini=HOJE - timedelta(days=1),
        data_fim=HOJE - timedelta(days=1),
        status="concluida",
        concluida_em=cedo,
        travada_por="x",
        travada_ate=viva,
    )

    assert enfileirar_elegiveis(sessao, "atualizacao") == 0

    sessao.refresh(andando)
    sessao.refresh(concluida)
    assert (andando.status, andando.lote, concluida.status, concluida.lote) == (
        "em_andamento",
        0,
        "concluida",
        0,
    )
    assert _coletar_janela(sessao) == []


def test_enfileirar_cobre_pendente_falha_e_em_andamento_livre(sessao: Session) -> None:
    vencida = datetime.now(UTC) - timedelta(hours=1)
    ids = [
        janela(
            sessao,
            endpoint="atualizacao",
            data_ini=HOJE - timedelta(days=n),
            data_fim=HOJE - timedelta(days=n),
            **campos,
        ).id
        for n, campos in enumerate(
            [
                {"status": "pendente"},
                {"status": "falha", "lote": 1},
                {"status": "em_andamento", "lote": 4},
                {"status": "em_andamento", "lote": 2, "travada_por": "x", "travada_ate": vencida},
            ]
        )
    ]
    janela(sessao, endpoint="publicacao", data_ini=HOJE, data_fim=HOJE)  # outro endpoint

    assert enfileirar_elegiveis(sessao, "atualizacao") == 4

    assert sorted(_coletar_janela(sessao), key=lambda c: c["janela_id"]) == sorted(
        [
            {"janela_id": ids[0], "lote": 0},
            {"janela_id": ids[1], "lote": 1},
            {"janela_id": ids[2], "lote": 4},
            {"janela_id": ids[3], "lote": 2},
        ],
        key=lambda c: c["janela_id"],
    )


def test_janelas_paradas_so_em_andamento_antes_do_corte(sessao: Session) -> None:
    corte = datetime(2026, 11, 10, 6, 0, tzinfo=BRT)
    antes = corte - timedelta(hours=1)
    janela(
        sessao,
        data_ini=D1,
        data_fim=D1,
        status="em_andamento",
        iniciada_em=antes,
        proxima_pagina=7,
        total_paginas=80,
    )
    janela(
        sessao,
        data_ini=D1,
        data_fim=D1,
        modalidade=8,
        status="em_andamento",
        iniciada_em=corte + timedelta(minutes=5),
    )
    janela(
        sessao,
        data_ini=date(2026, 11, 2),
        data_fim=date(2026, 11, 2),
        status="falha",
        iniciada_em=antes,
    )

    assert janelas_paradas(sessao, corte) == [JanelaParada(D1, 6, 7, 80)]


def test_auditar_mostra_onde_a_falha_parou(sessao: Session) -> None:
    janela(sessao, status="falha", erro="HTTP 500", proxima_pagina=3, total_paginas=5)

    aud = auditar(sessao, "publicacao", D1, D1, [6])

    assert aud.falhas == [(D1, 6, "HTTP 500", 3, 5)]


def test_enfileirar_desiste_de_falha_com_tentativas_demais(
    sessao: Session, caplog: pytest.LogCaptureFixture
) -> None:
    desistida = janela(
        sessao, endpoint="atualizacao", data_ini=HOJE, data_fim=HOJE, status="falha", tentativas=5
    )
    tenta = janela(
        sessao,
        endpoint="atualizacao",
        data_ini=HOJE - timedelta(days=1),
        data_fim=HOJE - timedelta(days=1),
        status="falha",
        tentativas=4,
    )

    with caplog.at_level(logging.INFO, logger="radar.coleta.janelas"):
        assert enfileirar_elegiveis(sessao, "atualizacao") == 1

    assert _coletar_janela(sessao) == [{"janela_id": tenta.id, "lote": 0}]
    desistencias = [r for r in caplog.records if r.getMessage() == "janela_desistida"]
    assert [getattr(r, "janela_id", None) for r in desistencias] == [desistida.id]


def test_janelas_paradas_conta_o_checkpoint_e_o_lease(sessao: Session) -> None:
    corte = datetime(2026, 11, 10, 6, 0, tzinfo=BRT)
    antes = corte - timedelta(hours=10)
    # Retentativa saudável: começou há muito, mas a última página é recente.
    andando = janela(sessao, status="em_andamento", iniciada_em=antes, tentativas=3)
    sessao.add(
        PaginaBruta(
            janela_id=andando.id,
            numero_pagina=1,
            conteudo={"data": []},
            coletada_em=corte + timedelta(minutes=10),
        )
    )
    # Com o lease vivo alguém está coletando agora.
    janela(
        sessao,
        modalidade=8,
        status="em_andamento",
        iniciada_em=antes,
        travada_por="lambda",
        travada_ate=datetime.now(UTC) + timedelta(minutes=10),
    )
    # Parada de verdade: a última página é velha.
    parada = janela(sessao, modalidade=9, status="em_andamento", iniciada_em=antes)
    sessao.add(
        PaginaBruta(
            janela_id=parada.id,
            numero_pagina=1,
            conteudo={"data": []},
            coletada_em=corte - timedelta(hours=1),
        )
    )
    sessao.flush()

    assert janelas_paradas(sessao, corte) == [JanelaParada(D1, 9, 1, None)]


def test_janelas_com_lease_vivo(sessao: Session) -> None:
    janela(sessao, travada_por="x", travada_ate=datetime.now(UTC) - timedelta(minutes=1))
    janela(sessao, modalidade=8)
    assert janelas_com_lease_vivo(sessao) == 0

    janela(
        sessao,
        modalidade=9,
        status="em_andamento",
        travada_por="lambda",
        travada_ate=datetime.now(UTC) + timedelta(minutes=10),
    )

    assert janelas_com_lease_vivo(sessao) == 1
