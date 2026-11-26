"""Janelas de coleta: planejamento, carga incremental com retomada e auditoria.

Invariante central: uma janela que falha fica com status "falha" (e o erro), nunca como
"concluida" com zero registros. Um dia sem editais e uma falha são coisas diferentes.
"""

import logging
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, Engine, and_, cast, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from radar.coleta.modelos import ColetaJanela, PaginaBruta
from radar.coleta.pncp import ClientePNCP, ErroPNCP

logger = logging.getLogger(__name__)

FUSO = "America/Sao_Paulo"  # as datas do PNCP são em horário de Brasília
CHAVE_COLETOR = 7_240_001  # chave do advisory lock que garante um único coletor por vez
LIMITE_ERRO = 500


def _agora_real() -> datetime:
    return datetime.now(UTC)


@dataclass
class ResumoColeta:
    concluidas: int = 0
    falhas: int = 0
    divididas: int = 0
    registros: int = 0


@dataclass
class Auditoria:
    esperadas: int
    concluidas: int
    faltando: list[tuple[date, int]]
    falhas: list[tuple[date, int, str]]


def planejar_janelas(
    sessao: Session, endpoint: str, inicio: date, fim: date, modalidades: list[int]
) -> int:
    """Cria uma janela de um dia por modalidade; devolve quantas eram novas."""
    linhas = [
        {
            "endpoint": endpoint,
            "data_ini": dia,
            "data_fim": dia,
            "modalidade": modalidade,
            "status": "pendente",
            "paginas": 0,
            "registros": 0,
        }
        for dia in _dias(inicio, fim)
        for modalidade in modalidades
    ]
    if not linhas:
        return 0
    comando = (
        pg_insert(ColetaJanela)
        .values(linhas)
        .on_conflict_do_nothing(constraint="uq_coleta_janela_chave")
        .returning(ColetaJanela.id)
    )
    criadas = len(sessao.execute(comando).all())
    sessao.commit()
    return criadas


def ultima_atualizacao(sessao: Session) -> datetime | None:
    """Quando a última janela foi concluída (tz-aware), ou None se nenhuma foi."""
    return sessao.scalar(
        select(func.max(ColetaJanela.concluida_em)).where(ColetaJanela.status == "concluida")
    )


def coletar_pendentes(
    sessao: Session,
    cliente: ClientePNCP,
    agora: datetime,
    relogio: Callable[[], datetime] | None = None,
) -> ResumoColeta:
    """Coleta todas as janelas elegíveis e devolve o resumo.

    `agora` é o instante de referência da execução (regra de elegibilidade das janelas de
    atualização, que é por data); os carimbos iniciada_em, concluida_em e coletada_em usam
    o relógio real (`relogio`, injetável nos testes).
    """
    relogio = relogio or _agora_real
    bind = sessao.get_bind()
    # Conexão dedicada: o lock pertence à conexão do banco, e a sessão devolve a dela ao
    # pool a cada commit.
    conexao = bind.connect() if isinstance(bind, Engine) else bind
    try:
        if not conexao.scalar(select(func.pg_try_advisory_lock(CHAVE_COLETOR))):
            logger.warning("coleta_em_andamento_noutro_processo")
            return ResumoColeta()
        try:
            return _coletar_todas(sessao, cliente, relogio)
        finally:
            conexao.scalar(select(func.pg_advisory_unlock(CHAVE_COLETOR)))
    finally:
        if conexao is not bind:
            conexao.close()


def _coletar_todas(
    sessao: Session, cliente: ClientePNCP, relogio: Callable[[], datetime]
) -> ResumoColeta:
    # Um único coletor por vez, garantido pelo advisory lock em coletar_pendentes; por isso
    # uma janela em_andamento só pode ser sobra de uma execução interrompida.
    sessao.execute(
        update(ColetaJanela).where(ColetaJanela.status == "em_andamento").values(status="pendente")
    )
    sessao.commit()

    resumo = ResumoColeta()
    tentadas: set[int] = set()  # uma janela que falhou não é repetida na mesma chamada
    while True:
        janela = _proxima_janela(sessao, tentadas)
        if janela is None:
            return resumo
        tentadas.add(janela.id)
        janela_id = janela.id
        dados = _dados_log(janela, time.monotonic())
        try:
            _coletar_janela(sessao, cliente, janela, relogio, resumo)
        except Exception as erro:  # Ctrl+C (BaseException) continua parando o processo
            sessao.rollback()
            logger.exception("janela_erro_inesperado", extra=dados)
            sessao.execute(
                update(ColetaJanela)
                .where(ColetaJanela.id == janela_id)
                .values(status="falha", erro=repr(erro)[:LIMITE_ERRO])
            )
            sessao.commit()
            resumo.falhas += 1


def ler_paginas_nao_processadas(
    sessao: Session, limite: int = 100, excluir: Collection[int] = ()
) -> list[tuple[int, list[dict[str, Any]]]]:
    """Pares (pagina_id, registros) das páginas ainda não processadas, em ordem de id.

    `excluir` são ids a pular (páginas que falharam nesta execução).
    """
    consulta = select(PaginaBruta.id, PaginaBruta.conteudo).where(
        PaginaBruta.processada_em.is_(None)
    )
    if excluir:
        consulta = consulta.where(PaginaBruta.id.not_in(excluir))
    linhas = sessao.execute(consulta.order_by(PaginaBruta.id).limit(limite)).all()
    return [(pagina_id, conteudo.get("data") or []) for pagina_id, conteudo in linhas]


def marcar_processada(sessao: Session, pagina_id: int) -> None:
    sessao.execute(
        update(PaginaBruta)
        .where(PaginaBruta.id == pagina_id)
        .values(processada_em=datetime.now(UTC))
    )
    sessao.commit()


def auditar(
    sessao: Session, endpoint: str, inicio: date, fim: date, modalidades: list[int]
) -> Auditoria:
    """Compara os dias x modalidades esperados com o que as janelas realmente cobrem."""
    janelas = sessao.scalars(
        select(ColetaJanela)
        .where(
            ColetaJanela.endpoint == endpoint,
            ColetaJanela.modalidade.in_(modalidades),
            ColetaJanela.data_ini <= fim,
            ColetaJanela.data_fim >= inicio,
        )
        .order_by(ColetaJanela.data_ini, ColetaJanela.modalidade)
    ).all()
    concluidos: set[tuple[date, int]] = set()
    em_falha: set[tuple[date, int]] = set()
    falhas: list[tuple[date, int, str]] = []
    for janela in janelas:
        cobertos = {
            (dia, janela.modalidade)
            for dia in _dias(max(janela.data_ini, inicio), min(janela.data_fim, fim))
        }
        if janela.status == "concluida":
            concluidos |= cobertos
        elif janela.status == "falha":
            em_falha |= cobertos
            falhas.append((janela.data_ini, janela.modalidade, janela.erro or ""))
    esperadas = [(dia, m) for dia in _dias(inicio, fim) for m in modalidades]
    faltando = [par for par in esperadas if par not in concluidos and par not in em_falha]
    return Auditoria(
        esperadas=len(esperadas),
        concluidas=sum(1 for par in esperadas if par in concluidos),
        faltando=faltando,
        falhas=falhas,
    )


def _dias(inicio: date, fim: date) -> list[date]:
    return [inicio + timedelta(days=n) for n in range((fim - inicio).days + 1)]


def _proxima_janela(sessao: Session, tentadas: set[int]) -> ColetaJanela | None:
    # Janelas de atualizacao concluídas antes de o dia acabar (em Brasília) ficaram
    # incompletas: o PNCP ainda podia alterar editais naquele dia.
    fim_do_dia = func.timezone(FUSO, cast(ColetaJanela.data_fim, DateTime) + timedelta(days=1))
    elegivel = or_(
        ColetaJanela.status.in_(("pendente", "falha")),
        and_(
            ColetaJanela.status == "concluida",
            ColetaJanela.endpoint == "atualizacao",
            ColetaJanela.concluida_em < fim_do_dia,
        ),
    )
    return sessao.scalars(
        select(ColetaJanela)
        .where(elegivel, ColetaJanela.id.not_in(tentadas))
        .order_by(
            ColetaJanela.data_ini, ColetaJanela.modalidade, ColetaJanela.endpoint, ColetaJanela.id
        )
        .limit(1)
    ).first()


def _dados_log(janela: ColetaJanela, inicio: float) -> dict[str, Any]:
    return {
        "janela_id": janela.id,
        "endpoint": janela.endpoint,
        "data_ini": janela.data_ini.isoformat(),
        "modalidade": janela.modalidade,
        "duracao_s": round(time.monotonic() - inicio, 3),
    }


def _falhar(
    sessao: Session, janela: ColetaJanela, erro: str, inicio: float, resumo: ResumoColeta
) -> None:
    # As páginas já gravadas ficam; a janela volta na próxima execução.
    janela.status = "falha"
    janela.erro = erro
    sessao.commit()
    resumo.falhas += 1
    logger.warning("janela_falhou", extra={**_dados_log(janela, inicio), "erro": erro})


def _coletar_janela(
    sessao: Session,
    cliente: ClientePNCP,
    janela: ColetaJanela,
    relogio: Callable[[], datetime],
    resumo: ResumoColeta,
) -> None:
    inicio = time.monotonic()
    janela.status = "em_andamento"
    janela.tentativas += 1
    janela.erro = None
    janela.iniciada_em = relogio()
    sessao.commit()
    logger.info("janela_iniciada", extra=_dados_log(janela, inicio))

    numero = 1
    total = 1  # o total vale o da página 1; as seguintes só o confirmam
    esperados: int | None = None
    paginas = 0
    registros = 0
    vistos: set[str] = set()
    while numero <= total:
        try:
            pagina = cliente.buscar(
                janela.endpoint, janela.data_ini, janela.data_fim, janela.modalidade, numero
            )
        except ErroPNCP as erro:
            if numero == 1 and janela.data_ini < janela.data_fim and _sem_paginas(sessao, janela):
                _dividir(sessao, janela, str(erro), inicio)
                resumo.divididas += 1
            else:
                _falhar(sessao, janela, str(erro), inicio, resumo)
            return
        if numero == 1:
            total = pagina.total_paginas
            bruto_total = pagina.bruto.get("totalRegistros")
            esperados = bruto_total if isinstance(bruto_total, int) else None
        elif pagina.total_paginas != total:
            _falhar(
                sessao,
                janela,
                f"pagina {numero} inconsistente: total_paginas={pagina.total_paginas}, "
                f"esperado {total}",
                inicio,
                resumo,
            )
            return
        if total > 0:
            # total_paginas == 0 é dia sem registros: nada a guardar, paginas=0.
            _gravar_pagina(sessao, janela, numero, pagina.bruto, relogio())
            paginas += 1
            registros += len(pagina.registros)
            vistos.update(
                str(r["numeroControlePNCP"]) for r in pagina.registros if "numeroControlePNCP" in r
            )
        numero += 1

    if esperados is not None and len(vistos) < esperados:
        mensagem = f"incompleta: {len(vistos)} de {esperados} registros"
        _falhar(sessao, janela, mensagem, inicio, resumo)
        return

    janela.status = "concluida"
    janela.paginas = paginas
    janela.registros = registros
    janela.concluida_em = relogio()
    sessao.commit()
    resumo.concluidas += 1
    resumo.registros += registros
    logger.info(
        "janela_concluida",
        extra={**_dados_log(janela, inicio), "paginas": paginas, "registros": registros},
    )


def _gravar_pagina(
    sessao: Session, janela: ColetaJanela, numero: int, bruto: dict[str, Any], agora: datetime
) -> None:
    comando = pg_insert(PaginaBruta).values(
        janela_id=janela.id, numero_pagina=numero, conteudo=bruto, coletada_em=agora
    )
    sessao.execute(
        comando.on_conflict_do_update(
            constraint="uq_pagina_bruta_janela_numero",
            set_={
                "conteudo": comando.excluded.conteudo,
                "coletada_em": agora,
                "processada_em": None,
            },
        )
    )
    sessao.commit()  # checkpoint: a página sobrevive a uma falha posterior


def _sem_paginas(sessao: Session, janela: ColetaJanela) -> bool:
    quantas = sessao.scalar(select(func.count()).where(PaginaBruta.janela_id == janela.id))
    return not quantas


def _dividir(sessao: Session, janela: ColetaJanela, erro: str, inicio: float) -> None:
    """Troca a janela por duas metades; só é chamada para janelas de mais de um dia."""
    dados = {**_dados_log(janela, inicio), "erro": erro, "data_fim": janela.data_fim.isoformat()}
    dias = (janela.data_fim - janela.data_ini).days + 1
    fim_primeira = janela.data_ini + timedelta(days=(dias - 1) // 2)
    metades = [
        (janela.data_ini, fim_primeira),
        (fim_primeira + timedelta(days=1), janela.data_fim),
    ]
    base = {
        "endpoint": janela.endpoint,
        "modalidade": janela.modalidade,
        "status": "pendente",
        "paginas": 0,
        "registros": 0,
    }
    sessao.execute(delete(ColetaJanela).where(ColetaJanela.id == janela.id))
    sessao.execute(
        pg_insert(ColetaJanela)
        .values([{**base, "data_ini": ini, "data_fim": fim} for ini, fim in metades])
        .on_conflict_do_nothing(constraint="uq_coleta_janela_chave")
    )
    sessao.commit()
    logger.warning("janela_dividida", extra=dados)
