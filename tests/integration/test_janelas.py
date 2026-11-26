from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fakes import VAZIA, Chave, ClienteFalso, como_cliente, pagina
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from radar.coleta import (
    ErroPNCP,
    auditar,
    coletar_pendentes,
    ler_paginas_nao_processadas,
    marcar_processada,
    planejar_janelas,
)
from radar.coleta.janelas import CHAVE_COLETOR, ResumoColeta
from radar.coleta.modelos import ColetaJanela, PaginaBruta

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
    assert aud.falhas == [(d2, 6, "HTTP 500")]


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


def test_coletor_ja_ativo_noutro_processo_nao_coleta(sessao: Session, engine: Engine) -> None:
    planejar_janelas(sessao, "publicacao", D1, D1, [6])
    falso = ClienteFalso({_chave(1): pagina(1, 1)})
    with engine.connect() as outra:
        assert outra.scalar(text("select pg_try_advisory_lock(:k)"), {"k": CHAVE_COLETOR})
        resumo = coletar_pendentes(sessao, como_cliente(falso), AGORA)
        outra.scalar(text("select pg_advisory_unlock(:k)"), {"k": CHAVE_COLETOR})
    assert falso.chamadas == []
    assert resumo == ResumoColeta()
    assert _unica(sessao).status == "pendente"
    coletar_pendentes(sessao, como_cliente(falso), AGORA)  # lock liberado: coleta normal
    assert _unica(sessao).status == "concluida"


def test_excluir_pula_paginas_na_leitura(sessao: Session) -> None:
    planejar_janelas(sessao, "publicacao", D1, D1, [6])
    falso = ClienteFalso({_chave(n): pagina(n, 2) for n in (1, 2)})
    coletar_pendentes(sessao, como_cliente(falso), AGORA)
    ids = [p for p, _ in ler_paginas_nao_processadas(sessao)]
    assert [p for p, _ in ler_paginas_nao_processadas(sessao, excluir=[ids[0]])] == [ids[1]]
