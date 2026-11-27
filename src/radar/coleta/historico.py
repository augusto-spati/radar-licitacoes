"""Carga histórica do PNCP por UF, mês e modalidade, retomável e gravando só o que casa.

Cada combinação (UF, modalidade, mês) é uma linha de `historico_carga`. O checkpoint
(`proxima_pagina`) avança no mesmo commit da gravação de cada página: uma carga interrompida
recomeça na página seguinte, sem regravar as concluídas. Nada de página bruta vai ao banco.
"""

import calendar
import logging
import re
import time
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.coleta.modelos import HistoricoCarga
from radar.coleta.pncp import ClientePNCP, ErroPNCP
from radar.coleta.trava import liberar_trava, obter_trava

logger = logging.getLogger(__name__)

FONTE_TRAVA = "historico"
DURACAO_TRAVA = timedelta(minutes=30)  # renovada a cada página
PAUSA_PADRAO_S = 1.0  # cortesia com o PNCP: ≥ 1 s entre requisições


@dataclass
class ResumoHistorico:
    combinacoes: int = 0
    concluidas: int = 0
    falhas: int = 0
    paginas: int = 0
    lidos: int = 0
    gravados: int = 0


# Palavras vazias que o `phraseto_tsquery('portuguese', ...)` também ignora; somem dos dois
# lados, então "rede para agua" passa por "rede de agua" (o filtro de verdade decide depois).
_VAZIAS = frozenset(
    "a o as os e de da do das dos em na no nas nos para por com ao aos um uma".split()
)
# Plural → singular grosseiro; a ordem importa (a primeira terminação que casa vence).
_PLURAIS = (("oes", "ao"), ("aes", "ao"), ("ais", "al"), ("eis", "el"), ("ois", "ol"), ("ns", "m"))


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).lower()


def _singular(palavra: str) -> str:
    if len(palavra) <= 3:
        return palavra
    for plural, singular in _PLURAIS:
        if palavra.endswith(plural):
            return palavra[: -len(plural)] + singular
    if palavra.endswith("s"):
        palavra = palavra[:-1]
    # "redes" → "rede" e "torres" → "torre"; tirar o "e" final iguala também "medidores".
    return palavra[:-1] if len(palavra) > 3 and palavra.endswith("e") else palavra


def _normalizar(texto: str) -> str:
    """Sem acento, minúsculo, só letras e números, sem palavras vazias, cada palavra no singular."""
    palavras = re.findall(r"[a-z0-9]+", _sem_acento(texto))
    return " ".join(_singular(p) for p in palavras if p not in _VAZIAS)


def prefiltrar(registros: list[dict[str, Any]], termos: list[str]) -> list[dict[str, Any]]:
    """Mantém o registro cujo `objetoCompra` contém algum termo, os dois normalizados.

    É um pré-filtro: precisa ser um superconjunto do filtro do banco (busca de frase do
    Postgres em português, que reduz plurais e ignora palavras vazias). Por isso objeto e termo
    passam pela mesma normalização (`_normalizar`) e o teste é de substring.
    """
    normalizados = [t for t in (_normalizar(termo) for termo in termos) if t]
    mantidos = []
    for registro in registros:
        objeto = registro.get("objetoCompra")
        if not isinstance(objeto, str):
            continue
        texto = _normalizar(objeto)
        if any(termo in texto for termo in normalizados):
            mantidos.append(registro)
    return mantidos


def meses_ate(hoje: date, n: int) -> list[date]:
    """1º dia dos `n` meses anteriores ao corrente, do mais recente ao mais antigo."""
    ano, mes = hoje.year, hoje.month
    meses = []
    for _ in range(n):
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
        meses.append(date(ano, mes, 1))
    return meses


def _fim_do_mes(mes: date) -> date:
    return date(mes.year, mes.month, calendar.monthrange(mes.year, mes.month)[1])


def _garantir_linhas(
    sessao: Session, ufs: list[str], modalidades: list[int], meses: list[date], refazer: set[date]
) -> None:
    existentes = {
        (c.uf, c.modalidade, c.mes): c
        for c in sessao.scalars(select(HistoricoCarga).where(HistoricoCarga.mes.in_(meses)))
    }
    agora = datetime.now().astimezone()
    for uf in ufs:
        for modalidade in modalidades:
            for mes in meses:
                carga = existentes.get((uf, modalidade, mes))
                if carga is None:
                    sessao.add(
                        HistoricoCarga(
                            uf=uf,
                            modalidade=modalidade,
                            mes=mes,
                            status="pendente",
                            proxima_pagina=1,
                            registros_lidos=0,
                            registros_gravados=0,
                            atualizada_em=agora,
                        )
                    )
                elif mes in refazer:
                    carga.status = "pendente"
                    carga.proxima_pagina = 1
                    carga.total_paginas = None
                    carga.registros_lidos = 0
                    carga.registros_gravados = 0
                    carga.erro = None
                    carga.atualizada_em = agora
    sessao.commit()


def _processar(
    sessao: Session,
    cliente: ClientePNCP,
    carga_id: int,
    termos: list[str],
    gravar: Callable[[Session, list[dict[str, Any]]], int],
    resumo: ResumoHistorico,
    renovar: Callable[[], bool],
    pausar: Callable[[], None],
) -> bool:
    """Percorre as páginas de uma combinação; True se concluiu, False se falhou."""
    while True:
        carga = sessao.get(HistoricoCarga, carga_id)
        assert carga is not None
        pagina = carga.proxima_pagina
        uf, modalidade, mes = carga.uf, carga.modalidade, carga.mes
        if not renovar():
            raise RuntimeError("trava historico perdida para outro processo")
        pausar()
        try:
            resposta = cliente.buscar(
                "publicacao", mes, _fim_do_mes(mes), modalidade, pagina, uf=uf
            )
        except ErroPNCP as erro:
            sessao.rollback()
            carga = sessao.get(HistoricoCarga, carga_id)
            assert carga is not None
            carga.status = "falha"
            carga.erro = str(erro)[:500]
            carga.atualizada_em = datetime.now().astimezone()
            sessao.commit()
            logger.warning(
                "historico_falha",
                extra={"uf": uf, "modalidade": modalidade, "mes": str(mes), "pagina": pagina},
            )
            return False
        casados = prefiltrar(resposta.registros, termos)
        gravados = gravar(sessao, casados) if casados else 0
        carga = sessao.get(HistoricoCarga, carga_id)
        assert carga is not None
        carga.total_paginas = resposta.total_paginas
        carga.registros_lidos += len(resposta.registros)
        carga.registros_gravados += gravados
        carga.proxima_pagina = pagina + 1
        carga.erro = None
        carga.atualizada_em = datetime.now().astimezone()
        concluida = pagina >= resposta.total_paginas
        carga.status = "concluida" if concluida else "em_andamento"
        sessao.commit()
        resumo.paginas += 1
        resumo.lidos += len(resposta.registros)
        resumo.gravados += gravados
        if concluida:
            return True


def carregar_historico(
    sessao: Session,
    cliente: ClientePNCP,
    ufs: list[str],
    modalidades: list[int],
    meses: list[date],
    termos: list[str],
    gravar: Callable[[Session, list[dict[str, Any]]], int],
    refazer: set[date] | None = None,
    pausa_s: float = PAUSA_PADRAO_S,
    dormir: Callable[[float], None] = time.sleep,
) -> ResumoHistorico:
    """Carrega o histórico; `refazer` reabre os meses dados. Retomável entre chamadas."""
    resumo = ResumoHistorico()
    dono = uuid.uuid4()
    if not obter_trava(sessao, FONTE_TRAVA, dono, DURACAO_TRAVA):
        raise RuntimeError("outra carga histórica está em andamento")
    primeira = [True]

    def renovar() -> bool:
        return obter_trava(sessao, FONTE_TRAVA, dono, DURACAO_TRAVA)

    def pausar() -> None:
        if not primeira[0] and pausa_s > 0:
            dormir(pausa_s)
        primeira[0] = False

    try:
        _garantir_linhas(sessao, ufs, modalidades, meses, refazer or set())
        ordem = {mes: i for i, mes in enumerate(meses)}
        cargas = sessao.scalars(
            select(HistoricoCarga).where(
                HistoricoCarga.uf.in_(ufs),
                HistoricoCarga.modalidade.in_(modalidades),
                HistoricoCarga.mes.in_(meses),
            )
        ).all()
        ids = [c.id for c in sorted(cargas, key=lambda c: (ordem[c.mes], c.uf, c.modalidade))]
        resumo.combinacoes = len(ids)
        for carga_id in ids:
            carga = sessao.get(HistoricoCarga, carga_id)
            assert carga is not None
            if carga.status == "concluida":
                resumo.concluidas += 1
                continue
            if _processar(sessao, cliente, carga_id, termos, gravar, resumo, renovar, pausar):
                resumo.concluidas += 1
            else:
                resumo.falhas += 1
    except Exception:
        sessao.rollback()  # a página em curso não avança o checkpoint
        raise
    finally:
        liberar_trava(sessao, FONTE_TRAVA, dono)
    return resumo
