"""Fatia de uma janela do PNCP: reivindicar, coletar até o orçamento, concluir ou continuar.

Uma fatia é o trabalho de uma invocação da Lambda coletora (ou uma janela inteira, no laço
local da cli). O estado vive em `coleta_janela`, não na memória:

- `proxima_pagina` é o checkpoint, avançado no mesmo commit de cada página;
- `total_paginas` e `esperados` (da página 1) fazem a checagem de completude valer entre fatias;
- `lote` é o fencing token da mensagem `coletar_janela`: só muda quando uma fatia termina sem
  concluir (vai junto da continuação) e quando o planejador reabre a janela;
- `travada_por`/`travada_ate` são o lease de quem coleta agora, com o relógio do banco.

Reivindicar é um `UPDATE ... RETURNING` atômico. Quem não pega a linha confirma a mensagem sem
chamar o PNCP (`inexistente`, `concluida`, `obsoleta`, `ocupada`). Cada commit de página confere
`lote` e `travada_por`: zero linhas é lease perdido, e a página é desfeita.

Nenhum advisory lock: o caminho da Lambda passa pelo pooler do Neon em modo transação.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import StatusCode
from sqlalchemy import Row, case, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from radar.coleta.modelos import ColetaJanela, PaginaBruta
from radar.coleta.pncp import ClientePNCP, ErroPNCP, Pagina
from radar.comum import enfileirar, metricas
from radar.comum.telemetria import link_de, tracer

logger = logging.getLogger(__name__)

LEASE_S = 960  # timeout da Lambda coletora (900 s) + 60 s de folga
ORCAMENTO_LAMBDA_S = 540  # depois disto a fatia não começa página nova
LIMITE_ERRO = 500

DesfechoFatia = Literal[
    "concluida",
    "continua",
    "falha",
    "dividida",
    "ocupada",
    "obsoleta",
    "inexistente",
    "lease_perdida",
]


@dataclass(frozen=True)
class ResultadoFatia:
    desfecho: DesfechoFatia
    paginas: int  # desta fatia
    registros: int  # desta fatia
    erro: str | None = None
    # `outbox_id` das `coletar_janela` que esta fatia gravou (continuação ou metades): quem
    # recebeu a mensagem só a confirma depois de o relay publicá-las.
    continuacoes: tuple[int, ...] = ()


@dataclass(frozen=True)
class _Janela:
    """A janela reivindicada, lida do `RETURNING` (sem objeto ORM preso à sessão)."""

    id: int
    endpoint: str
    data_ini: date
    data_fim: date
    modalidade: int
    lote: int
    proxima_pagina: int
    total_paginas: int | None
    esperados: int | None


class _LeasePerdida(Exception):
    """O commit com fencing não achou a linha: outra invocação assumiu a janela."""


def lease_livre() -> ColumnElement[bool]:
    return or_(ColetaJanela.travada_ate.is_(None), ColetaJanela.travada_ate < func.now())


def _carimbo(relogio: Callable[[], datetime] | None) -> Any:
    return relogio() if relogio is not None else func.now()


def coletar_fatia(
    sessao: Session,
    cliente: ClientePNCP,
    janela_id: int,
    *,
    lote: int | None,
    token: str,
    orcamento_s: float | None,
    relogio: Callable[[], datetime] | None = None,
    cronometro: Callable[[], float] = time.monotonic,
    traceparent: str | None = None,
) -> ResultadoFatia:
    """Coleta uma fatia da janela e devolve o desfecho, dentro do span `coleta.janela`.

    O span é raiz (a unidade de trabalho é a fatia, não a execução de quem chama) e tem link
    para `traceparent` (o contexto da mensagem `coletar_janela`, quando há): as fatias de uma
    janela ficam encadeadas por links. As páginas e a continuação gravadas aqui levam o
    contexto deste span para a outbox. As métricas são descarregadas no fim de cada fatia.
    """
    link = link_de(traceparent)
    try:
        with tracer().start_as_current_span(
            "coleta.janela",
            context=Context(),
            links=[link] if link is not None else None,
            attributes={"radar.janela.id": janela_id},
        ) as span:
            if lote is not None:
                span.set_attribute("radar.janela.lote", lote)
            resultado = _coletar_fatia(
                sessao, cliente, janela_id, lote, token, orcamento_s, relogio, cronometro
            )
            span.set_attribute("radar.desfecho", resultado.desfecho)
            span.set_attribute("radar.janela.paginas", resultado.paginas)
            span.set_attribute("radar.janela.registros", resultado.registros)
            if resultado.desfecho in ("falha", "lease_perdida"):
                span.set_status(StatusCode.ERROR, (resultado.erro or resultado.desfecho)[:200])
            return resultado
    finally:
        metricas.descarregar()


def _coletar_fatia(
    sessao: Session,
    cliente: ClientePNCP,
    janela_id: int,
    lote: int | None,
    token: str,
    orcamento_s: float | None,
    relogio: Callable[[], datetime] | None,
    cronometro: Callable[[], float],
) -> ResultadoFatia:
    """O trabalho da fatia.

    Com `lote` (mensagem da fila), a fatia confere o fencing token e, se o orçamento acabar,
    enfileira a continuação; uma divisão enfileira as duas metades. Sem `lote` (laço da cli),
    reivindica a janela em qualquer lote, vai até o fim e não enfileira nada. Toda fatia pede
    ao menos uma página, para a cadeia sempre andar. Exceção inesperada: a janela vira falha,
    o lease é solto e a exceção propaga.
    """
    if lote is None and orcamento_s is not None:
        raise ValueError("o laço local (sem lote) não tem orçamento: não há quem continue")
    inicio = cronometro()
    janela = _reivindicar(sessao, janela_id, lote, token, relogio)
    if isinstance(janela, str):
        # `janela_concluida` fica reservado à conclusão de verdade (o alarme o conta).
        evento = "janela_ja_concluida" if janela == "concluida" else f"janela_{janela}"
        logger.info(evento, extra={"janela_id": janela_id, "lote": lote})
        return ResultadoFatia(janela, 0, 0)
    dados = _dados_log(janela)
    _atributos_da_janela(janela)
    logger.info("janela_iniciada", extra=dados)
    try:
        return _coletar(
            sessao,
            cliente,
            janela,
            token,
            lote is not None,
            orcamento_s,
            relogio,
            cronometro,
            inicio,
        )
    except _LeasePerdida:
        sessao.rollback()
        logger.warning("lease_perdida", extra=dados)
        return ResultadoFatia("lease_perdida", 0, 0)
    except Exception as erro:  # Ctrl+C (BaseException) continua parando o processo
        sessao.rollback()
        trace.get_current_span().set_attribute("radar.desfecho", "falha")
        logger.exception("janela_erro_inesperado", extra=dados)
        sessao.execute(
            update(ColetaJanela)
            .where(*_fencing(janela, token))
            .values(
                status="falha",
                erro=repr(erro)[:LIMITE_ERRO],
                travada_por=None,
                travada_ate=None,
                falhas=ColetaJanela.falhas + 1,
            )
        )
        sessao.commit()
        raise


def _reivindicar(
    sessao: Session,
    janela_id: int,
    lote: int | None,
    token: str,
    relogio: Callable[[], datetime] | None,
) -> _Janela | DesfechoFatia:
    """Reivindica a janela num `UPDATE ... RETURNING`; sem linha, devolve o motivo."""
    pendente = ColetaJanela.status == "pendente"
    # Janela pendente nunca começou (ou foi reaberta): começa da página 1.
    recomeca = or_(pendente, ColetaJanela.proxima_pagina == 1)
    condicoes = [
        ColetaJanela.id == janela_id,
        ColetaJanela.status.in_(("pendente", "falha", "em_andamento")),
        lease_livre(),
    ]
    if lote is not None:
        condicoes.append(ColetaJanela.lote == lote)
    comando = (
        update(ColetaJanela)
        .where(*condicoes)
        .values(
            status="em_andamento",
            travada_por=token,
            travada_ate=func.now() + timedelta(seconds=LEASE_S),
            tentativas=ColetaJanela.tentativas + 1,
            erro=None,
            proxima_pagina=func.coalesce(_se(pendente, 1), ColetaJanela.proxima_pagina),
            total_paginas=_se(~pendente, ColetaJanela.total_paginas),
            esperados=_se(~pendente, ColetaJanela.esperados),
            iniciada_em=func.coalesce(_se(recomeca, _carimbo(relogio)), ColetaJanela.iniciada_em),
        )
        .returning(
            ColetaJanela.id,
            ColetaJanela.endpoint,
            ColetaJanela.data_ini,
            ColetaJanela.data_fim,
            ColetaJanela.modalidade,
            ColetaJanela.lote,
            ColetaJanela.proxima_pagina,
            ColetaJanela.total_paginas,
            ColetaJanela.esperados,
        )
    )
    linha = sessao.execute(comando).first()
    sessao.commit()
    if linha is not None:
        return _Janela(*linha)
    atual = sessao.execute(
        select(ColetaJanela.status, ColetaJanela.lote).where(ColetaJanela.id == janela_id)
    ).first()
    sessao.rollback()
    if atual is None:
        return "inexistente"
    if atual.status == "concluida":
        return "concluida"
    if lote is not None and atual.lote != lote:
        return "obsoleta"
    return "ocupada"


def _atributos_da_janela(janela: _Janela) -> None:
    trace.get_current_span().set_attributes(
        {
            "radar.janela.endpoint": janela.endpoint,
            "radar.janela.modalidade": janela.modalidade,
            "radar.janela.data_ini": janela.data_ini.isoformat(),
            "radar.janela.data_fim": janela.data_fim.isoformat(),
            "radar.janela.lote": janela.lote,
            # Continuação de uma fatia anterior começa depois da página 1.
            "radar.janela.pagina_inicial": janela.proxima_pagina if janela.total_paginas else 1,
        }
    )


def _se(condicao: ColumnElement[bool], valor: Any) -> Any:
    return case((condicao, valor), else_=None)


def _fencing(janela: _Janela, token: str) -> list[ColumnElement[bool]]:
    return [
        ColetaJanela.id == janela.id,
        ColetaJanela.lote == janela.lote,
        ColetaJanela.travada_por == token,
    ]


def _coletar(
    sessao: Session,
    cliente: ClientePNCP,
    janela: _Janela,
    token: str,
    modo_mensagem: bool,
    orcamento_s: float | None,
    relogio: Callable[[], datetime] | None,
    cronometro: Callable[[], float],
    inicio: float,
) -> ResultadoFatia:
    numero = janela.proxima_pagina
    total = janela.total_paginas if numero > 1 else None
    if total is None:  # checkpoint sem o total da página 1 não vale: recomeça
        numero = 1
    paginas = 0
    registros = 0
    while total is None or numero <= total:
        if paginas and orcamento_s is not None and cronometro() - inicio >= orcamento_s:
            return _continuar(sessao, janela, token, numero, paginas, registros, inicio, cronometro)
        try:
            pagina = cliente.buscar(
                janela.endpoint, janela.data_ini, janela.data_fim, janela.modalidade, numero
            )
        except ErroPNCP as erro:
            if numero == 1 and janela.data_ini < janela.data_fim and _sem_paginas(sessao, janela):
                return _dividir(sessao, janela, token, str(erro), modo_mensagem)
            return _falhar(
                sessao,
                janela,
                token,
                str(erro),
                recomecar=False,
                paginas=paginas,
                registros=registros,
            )
        if total is None:
            total = pagina.total_paginas
        elif pagina.total_paginas != total:
            erro_texto = (
                f"pagina {numero} inconsistente: total_paginas={pagina.total_paginas}, "
                f"esperado {total}"
            )
            return _falhar(
                sessao,
                janela,
                token,
                erro_texto,
                recomecar=True,
                paginas=paginas,
                registros=registros,
            )
        _gravar_pagina(sessao, janela, token, numero, pagina, relogio)
        if total > 0:  # total_paginas == 0 é dia sem registros: nada a guardar
            paginas += 1
            registros += len(pagina.registros)
        numero += 1
    return _concluir(sessao, janela, token, total, paginas, registros, relogio, inicio, cronometro)


def _gravar_pagina(
    sessao: Session,
    janela: _Janela,
    token: str,
    numero: int,
    pagina: Pagina,
    relogio: Callable[[], datetime] | None,
) -> None:
    """Página + mensagem + checkpoint (com fencing) num commit só."""
    if pagina.total_paginas > 0:
        agora = _carimbo(relogio)
        comando = pg_insert(PaginaBruta).values(
            janela_id=janela.id, numero_pagina=numero, conteudo=pagina.bruto, coletada_em=agora
        )
        pagina_id: int = sessao.execute(
            comando.on_conflict_do_update(
                constraint="uq_pagina_bruta_janela_numero",
                set_={
                    "conteudo": comando.excluded.conteudo,
                    "coletada_em": agora,
                    "processada_em": None,
                },
            ).returning(PaginaBruta.id)
        ).scalar_one()
        # Regravada também volta à fila: processada_em voltou a NULL.
        enfileirar(sessao, "pagina_coletada", {"pagina_id": pagina_id})
    valores: dict[str, Any] = {
        "proxima_pagina": numero + 1,
        # Renovar o lease a cada página deixa o laço local (sem orçamento) seguro por mais
        # de 960 s; na Lambda o lease vence no máximo 960 s depois da última página.
        "travada_ate": func.now() + timedelta(seconds=LEASE_S),
    }
    if numero == 1:
        bruto_total = pagina.bruto.get("totalRegistros")
        valores["total_paginas"] = pagina.total_paginas
        valores["esperados"] = (
            bruto_total
            if isinstance(bruto_total, int) and not isinstance(bruto_total, bool)
            else None
        )
    resultado = sessao.execute(
        update(ColetaJanela).where(*_fencing(janela, token)).values(**valores)
    )
    if not resultado.rowcount:  # type: ignore[attr-defined]
        raise _LeasePerdida()
    sessao.commit()  # checkpoint: a página (e a mensagem) sobrevive a uma falha posterior


_CONTAGEM = text(
    """
    SELECT count(*) AS paginas,
           coalesce(sum(jsonb_array_length(coalesce(conteudo->'data', '[]'::jsonb))), 0)
               AS registros,
           (SELECT count(DISTINCT e->>'numeroControlePNCP')
              FROM pagina_bruta p2,
                   jsonb_array_elements(coalesce(p2.conteudo->'data', '[]'::jsonb)) AS e
             WHERE p2.janela_id = :janela AND p2.numero_pagina <= :total) AS distintos
      FROM pagina_bruta
     WHERE janela_id = :janela AND numero_pagina <= :total
    """
)


def _concluir(
    sessao: Session,
    janela: _Janela,
    token: str,
    total: int,
    paginas: int,
    registros: int,
    relogio: Callable[[], datetime] | None,
    inicio: float,
    cronometro: Callable[[], float],
) -> ResultadoFatia:
    """Checagem de completude no banco (distintos de todas as fatias) e conclusão."""
    contagem: Row[Any] = sessao.execute(_CONTAGEM, {"janela": janela.id, "total": total}).one()
    esperados = sessao.scalar(select(ColetaJanela.esperados).where(ColetaJanela.id == janela.id))
    if esperados is not None and contagem.distintos < esperados:
        erro = f"incompleta: {contagem.distintos} de {esperados} registros"
        return _falhar(
            sessao, janela, token, erro, recomecar=True, paginas=paginas, registros=registros
        )
    resultado = sessao.execute(
        update(ColetaJanela)
        .where(*_fencing(janela, token))
        .values(
            status="concluida",
            paginas=contagem.paginas,
            registros=contagem.registros,
            concluida_em=_carimbo(relogio),
            erro=None,
            travada_por=None,
            travada_ate=None,
            falhas=0,
        )
    )
    if not resultado.rowcount:  # type: ignore[attr-defined]
        raise _LeasePerdida()
    sessao.commit()
    logger.info(
        "janela_concluida",
        extra={
            **_dados_log(janela, inicio, cronometro),
            "paginas": contagem.paginas,
            "registros": contagem.registros,
            "paginas_fatia": paginas,
        },
    )
    return ResultadoFatia("concluida", paginas, registros)


def _continuar(
    sessao: Session,
    janela: _Janela,
    token: str,
    numero: int,
    paginas: int,
    registros: int,
    inicio: float,
    cronometro: Callable[[], float],
) -> ResultadoFatia:
    """Orçamento acabou: próximo lote, lease solto e continuação na mesma transação."""
    resultado = sessao.execute(
        update(ColetaJanela)
        .where(*_fencing(janela, token))
        .values(lote=ColetaJanela.lote + 1, travada_por=None, travada_ate=None)
    )
    if not resultado.rowcount:  # type: ignore[attr-defined]
        raise _LeasePerdida()
    linha = enfileirar(sessao, "coletar_janela", {"janela_id": janela.id, "lote": janela.lote + 1})
    sessao.flush()
    continuacao = linha.id
    sessao.commit()
    logger.info(
        "janela_continua",
        extra={
            **_dados_log(janela, inicio, cronometro),
            "proxima_pagina": numero,
            "paginas_fatia": paginas,
            "registros_fatia": registros,
        },
    )
    return ResultadoFatia("continua", paginas, registros, continuacoes=(continuacao,))


def _falhar(
    sessao: Session,
    janela: _Janela,
    token: str,
    erro: str,
    *,
    recomecar: bool,
    paginas: int,
    registros: int,
) -> ResultadoFatia:
    """Falha com o lease solto. `recomecar`: o PNCP mudou no meio, a janela volta à página 1.

    As páginas já gravadas ficam; a mensagem volta para nova tentativa.
    """
    erro = erro[:LIMITE_ERRO]
    valores: dict[str, Any] = {
        "status": "falha",
        "erro": erro,
        "travada_por": None,
        "travada_ate": None,
        "falhas": ColetaJanela.falhas + 1,
    }
    if recomecar:
        valores |= {"proxima_pagina": 1, "total_paginas": None, "esperados": None}
    resultado = sessao.execute(
        update(ColetaJanela).where(*_fencing(janela, token)).values(**valores)
    )
    if not resultado.rowcount:  # type: ignore[attr-defined]
        raise _LeasePerdida()
    sessao.commit()
    logger.warning("janela_falhou", extra={**_dados_log(janela), "erro": erro})
    return ResultadoFatia("falha", paginas, registros, erro)


def _sem_paginas(sessao: Session, janela: _Janela) -> bool:
    quantas = sessao.scalar(select(func.count()).where(PaginaBruta.janela_id == janela.id))
    return not quantas


def _dividir(
    sessao: Session, janela: _Janela, token: str, erro: str, modo_mensagem: bool
) -> ResultadoFatia:
    """Troca a janela (de mais de um dia, sem páginas) por duas metades.

    No modo mensagem, as metades ganham `coletar_janela` na mesma transação.
    """
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
    apagada = sessao.execute(delete(ColetaJanela).where(*_fencing(janela, token)))
    if not apagada.rowcount:  # type: ignore[attr-defined]
        raise _LeasePerdida()
    novas = sessao.scalars(
        pg_insert(ColetaJanela)
        .values([{**base, "data_ini": ini, "data_fim": fim} for ini, fim in metades])
        .on_conflict_do_nothing(constraint="uq_coleta_janela_chave")
        .returning(ColetaJanela.id)
    ).all()
    linhas = [
        enfileirar(sessao, "coletar_janela", {"janela_id": nova, "lote": 0})
        for nova in (sorted(novas) if modo_mensagem else [])
    ]
    sessao.flush()
    continuacoes = tuple(linha.id for linha in linhas)
    sessao.commit()
    logger.warning(
        "janela_dividida",
        extra={**_dados_log(janela), "erro": erro, "data_fim": janela.data_fim.isoformat()},
    )
    return ResultadoFatia("dividida", 0, 0, erro, continuacoes)


def _dados_log(
    janela: _Janela, inicio: float | None = None, cronometro: Callable[[], float] | None = None
) -> dict[str, Any]:
    dados: dict[str, Any] = {
        "janela_id": janela.id,
        "endpoint": janela.endpoint,
        "data_ini": janela.data_ini.isoformat(),
        "modalidade": janela.modalidade,
        "lote": janela.lote,
    }
    if inicio is not None and cronometro is not None:
        dados["duracao_s"] = round(cronometro() - inicio, 3)
    return dados
