"""Janelas de coleta: planejamento, carga incremental com retomada e auditoria.

Invariante central: uma janela que falha fica com status "falha" (e o erro), nunca como
"concluida" com zero registros. Um dia sem editais e uma falha são coisas diferentes.
"""

import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, and_, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from radar.coleta.fatias import coletar_fatia, lease_livre
from radar.coleta.modelos import ColetaJanela, PaginaBruta
from radar.coleta.pncp import ClientePNCP
from radar.comum import Outbox, enfileirar

logger = logging.getLogger(__name__)

FUSO = "America/Sao_Paulo"  # as datas do PNCP são em horário de Brasília
# Falha com tantas reivindicações não volta à fila pelo planejador: fica para o laço local
# (`radar processar`) ou para uma decisão manual. Cada fatia conta uma reivindicação.
MAXIMO_TENTATIVAS = 5


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
    # (dia, modalidade, erro, proxima_pagina, total_paginas): onde a janela parou
    falhas: list[tuple[date, int, str, int, int | None]]


@dataclass(frozen=True)
class JanelaParada:
    dia: date
    modalidade: int
    proxima_pagina: int
    total_paginas: int | None


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
    """Coleta no próprio processo todas as janelas elegíveis e devolve o resumo.

    Cada janela é uma fatia sem `lote` e sem orçamento (vai até o fim). Duas execuções em
    paralelo, ou uma Lambda viva, não coletam a mesma janela: o lease decide, e a sobra de
    uma execução interrompida volta quando o lease dela vence. `agora` fica pela assinatura;
    os carimbos usam `relogio` (injetável nos testes) ou o relógio do banco.
    """
    resumo = ResumoColeta()
    tentadas: set[int] = set()  # uma janela que falhou não é repetida na mesma chamada
    while True:
        janela_id = _proxima_janela(sessao, tentadas)
        if janela_id is None:
            return resumo
        tentadas.add(janela_id)
        _reabrir(sessao, ColetaJanela.id == janela_id)
        sessao.commit()
        try:
            resultado = coletar_fatia(
                sessao,
                cliente,
                janela_id,
                lote=None,
                token=uuid4().hex,
                orcamento_s=None,
                relogio=relogio,
            )
        except Exception:  # já marcada como falha; Ctrl+C (BaseException) continua parando
            resumo.falhas += 1
            continue
        if resultado.desfecho == "concluida":
            resumo.concluidas += 1
            resumo.registros += resultado.registros
        elif resultado.desfecho == "falha":
            resumo.falhas += 1
        elif resultado.desfecho == "dividida":
            resumo.divididas += 1


def enfileirar_elegiveis(sessao: Session, endpoint: str) -> int:
    """Reabre as concluídas cedo demais e enfileira `coletar_janela` das elegíveis; um commit.

    Elegível: pendente, falha ou em_andamento, com o lease livre; falha com
    `MAXIMO_TENTATIVAS` ou mais fica de fora (log `janela_desistida`). A mensagem leva o
    `lote` atual. Uma mensagem a mais para janela que já tem continuação na fila é
    inofensiva: uma das duas sai `ocupada` ou `obsoleta`. Devolve quantas mensagens.
    """
    _reabrir(sessao, ColetaJanela.endpoint == endpoint)
    candidatas = sessao.execute(
        select(ColetaJanela.id, ColetaJanela.lote, ColetaJanela.status, ColetaJanela.tentativas)
        .where(
            ColetaJanela.endpoint == endpoint,
            ColetaJanela.status.in_(("pendente", "falha", "em_andamento")),
            lease_livre(),
        )
        .order_by(ColetaJanela.data_ini, ColetaJanela.modalidade, ColetaJanela.id)
    ).all()
    enfileiradas = 0
    for janela_id, lote, status, tentativas in candidatas:
        if status == "falha" and tentativas >= MAXIMO_TENTATIVAS:
            logger.warning(
                "janela_desistida", extra={"janela_id": janela_id, "tentativas": tentativas}
            )
            continue
        enfileirar(sessao, "coletar_janela", {"janela_id": janela_id, "lote": lote})
        enfileiradas += 1
    sessao.commit()
    return enfileiradas


def janelas_paradas(sessao: Session, parada_desde: datetime) -> list[JanelaParada]:
    """Janelas em_andamento, sem lease vivo, sem página nova desde o corte: a cadeia parou.

    O progresso é a página mais recente (o checkpoint); sem página, o início da janela. Uma
    retentativa saudável de janela antiga não aparece.
    """
    ultima_pagina = (
        select(func.max(PaginaBruta.coletada_em))
        .where(PaginaBruta.janela_id == ColetaJanela.id)
        .scalar_subquery()
    )
    linhas = sessao.execute(
        select(
            ColetaJanela.data_ini,
            ColetaJanela.modalidade,
            ColetaJanela.proxima_pagina,
            ColetaJanela.total_paginas,
        )
        .where(
            ColetaJanela.status == "em_andamento",
            lease_livre(),
            func.coalesce(ultima_pagina, ColetaJanela.iniciada_em) < parada_desde,
        )
        .order_by(ColetaJanela.data_ini, ColetaJanela.modalidade, ColetaJanela.id)
    ).all()
    return [JanelaParada(*linha) for linha in linhas]


def janelas_com_lease_vivo(sessao: Session) -> int:
    """Quantas janelas alguém (a Lambda coletora ou outro processo) está coletando agora."""
    quantas = sessao.scalar(select(func.count()).where(~lease_livre()))
    return int(quantas or 0)


def ler_paginas_nao_processadas(
    sessao: Session, limite: int = 100, excluir: Collection[int] = ()
) -> list[tuple[int, list[dict[str, Any]]]]:
    """Pares (pagina_id, registros) das páginas do PNCP ainda não processadas, em ordem de id.

    `excluir` são ids a pular (páginas que falharam nesta execução). As páginas de retrato
    ficam de fora: não são JSON do PNCP, e quem as processa é o consumidor do retrato.
    """
    consulta = select(PaginaBruta.id, PaginaBruta.conteudo).where(
        PaginaBruta.processada_em.is_(None), PaginaBruta.janela_id.is_not(None)
    )
    if excluir:
        consulta = consulta.where(PaginaBruta.id.not_in(excluir))
    linhas = sessao.execute(consulta.order_by(PaginaBruta.id).limit(limite)).all()
    return [(pagina_id, (conteudo or {}).get("data") or []) for pagina_id, conteudo in linhas]


@dataclass(frozen=True)
class PaginaPNCP:
    processada: bool
    registros: list[dict[str, Any]]


def ler_pagina_para_processar(sessao: Session, pagina_id: int) -> PaginaPNCP | None:
    """A página do PNCP, travada (`FOR UPDATE`) até o commit; None se não existe.

    A trava serializa duas entregas da mesma mensagem: a segunda espera a primeira commitar
    e então vê `processada`.
    """
    linha = sessao.execute(
        select(PaginaBruta.processada_em, PaginaBruta.conteudo)
        .where(PaginaBruta.id == pagina_id, PaginaBruta.janela_id.is_not(None))
        .with_for_update()
    ).first()
    if linha is None:
        return None
    processada_em, conteudo = linha
    return PaginaPNCP(
        processada=processada_em is not None, registros=(conteudo or {}).get("data") or []
    )


def paginas_publicadas_sem_processar(sessao: Session, publicadas_antes_de: datetime) -> int:
    """Páginas do PNCP cuja mensagem saiu antes do corte e que ainda não foram processadas.

    Mensagem publicada e página parada é o consumidor que não está dando conta (ou a fila de
    falhas acumulando).
    """
    pagina_id = Outbox.carga["pagina_id"].as_integer()
    return int(
        sessao.scalar(
            select(func.count(func.distinct(PaginaBruta.id)))
            .join(Outbox, pagina_id == PaginaBruta.id)
            .where(
                Outbox.tipo == "pagina_coletada",
                Outbox.publicada_em < publicadas_antes_de,
                PaginaBruta.processada_em.is_(None),
                PaginaBruta.janela_id.is_not(None),
            )
        )
        or 0
    )


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
    falhas: list[tuple[date, int, str, int, int | None]] = []
    for janela in janelas:
        cobertos = {
            (dia, janela.modalidade)
            for dia in _dias(max(janela.data_ini, inicio), min(janela.data_fim, fim))
        }
        if janela.status == "concluida":
            concluidos |= cobertos
        elif janela.status == "falha":
            em_falha |= cobertos
            falhas.append(
                (
                    janela.data_ini,
                    janela.modalidade,
                    janela.erro or "",
                    janela.proxima_pagina,
                    janela.total_paginas,
                )
            )
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


def _concluida_cedo() -> ColumnElement[bool]:
    """Janela de atualizacao concluída antes de o dia dela acabar (em Brasília).

    Ficou incompleta: o PNCP ainda podia alterar editais naquele dia.
    """
    fim_do_dia = func.timezone(FUSO, cast(ColetaJanela.data_fim, DateTime) + timedelta(days=1))
    return and_(
        ColetaJanela.status == "concluida",
        ColetaJanela.endpoint == "atualizacao",
        ColetaJanela.concluida_em < fim_do_dia,
    )


def _reabrir(sessao: Session, *filtros: ColumnElement[bool]) -> list[int]:
    """Concluída cedo demais e sem lease vira pendente do zero, num lote novo; sem commit.

    O lote novo invalida qualquer mensagem velha da janela (sai `obsoleta`).
    """
    reabertas = sessao.scalars(
        update(ColetaJanela)
        .where(_concluida_cedo(), lease_livre(), *filtros)
        .values(
            status="pendente",
            proxima_pagina=1,
            total_paginas=None,
            esperados=None,
            lote=ColetaJanela.lote + 1,
        )
        .returning(ColetaJanela.id)
    ).all()
    return list(reabertas)


def _proxima_janela(sessao: Session, tentadas: set[int]) -> int | None:
    elegivel = or_(
        ColetaJanela.status.in_(("pendente", "falha", "em_andamento")),
        _concluida_cedo(),
    )
    janela_id = sessao.scalars(
        select(ColetaJanela.id)
        .where(elegivel, lease_livre(), ColetaJanela.id.not_in(tentadas))
        .order_by(
            ColetaJanela.data_ini, ColetaJanela.modalidade, ColetaJanela.endpoint, ColetaJanela.id
        )
        .limit(1)
    ).first()
    return janela_id
