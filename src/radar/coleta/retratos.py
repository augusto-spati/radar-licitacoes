"""Motor de retratos: a lista inteira de uma fonte, lida a cada rodada, com cortesia.

Invariante central: um retrato só vale como "a lista completa" quando termina `concluido`.
Qualquer página em falha, ou menos registros que o mínimo da fonte, deixa o retrato em
`falha`, e nada é gravado em editais nem reconciliado. Este módulo não conhece `editais`:
o retrato concluído vira uma mensagem `retrato_coletado` na outbox, no mesmo commit, e o
consumidor relê as páginas (`paginas_do_retrato`), extrai, grava e reconcilia.

Um único retrato por fonte de cada vez: um lease por fonte na tabela `trava_fonte`
(`coleta.trava`), separado do lease do coletor do PNCP. Assim uma rodada agendada e uma
manual não se sobrepõem na mesma fonte, mas fontes diferentes (e o PNCP) não se bloqueiam.
A cli segura a trava (`trava_fonte`) durante a coleta do retrato.
"""

import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

import httpx
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import SpanKind, StatusCode
from sqlalchemy import delete, desc, func, literal, select, tuple_, update
from sqlalchemy.orm import Session

from radar.coleta.modelos import ColetaRetrato, PaginaBruta
from radar.coleta.trava import DURACAO_PADRAO, liberar_trava, obter_trava
from radar.comum import enfileirar, metricas
from radar.comum.telemetria import tracer

logger = logging.getLogger(__name__)

USER_AGENT = "radar-licitacoes-estudo (github.com/augusto-spati/radar-licitacoes)"
INTERVALO_S = 3.0  # entre requisições à mesma fonte
TIMEOUT_SEGUNDOS = 60.0
FRACAO_ENCOLHEU = 0.5
LIMITE_ERRO = 500
MANTER_CONCLUIDOS = 3  # retratos processados cujas páginas brutas ficam guardadas
MANTER_FALHAS = 10  # retratos em falha idem (para diagnóstico)

Formato = Literal["json", "html", "js"]
StatusRetrato = Literal["concluido", "falha", "ocupado"]


class ErroFonte(Exception):
    """A fonte recusou, bloqueou ou respondeu algo inesperado. O retrato para aqui.

    `status` é o código HTTP quando o erro foi uma resposta de erro (403, 404, 500...); fica
    None em falha de transporte, bloqueio por marcador e erro de layout. Assim o coletor pode
    tolerar um 404 isolado sem tolerar um 403.
    """

    def __init__(self, mensagem: str, status: int | None = None) -> None:
        super().__init__(mensagem)
        self.status = status


@dataclass(frozen=True)
class PaginaColetada:
    url: str
    formato: Formato
    texto: str


def criar_http_educado() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT_SEGUNDOS, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )


class ClienteEducado:
    """GET com cortesia: intervalo fixo entre requisições, User-Agent próprio, sem retentativa.

    Não há POST de propósito. 403, 429, qualquer outro status de erro, falha de transporte
    ou corpo que casa com `marcador_bloqueio` (captcha, página de bloqueio) viram `ErroFonte`.

    Telemetria por GET: span `GET` (CLIENT), log `requisicao_http`, `PaginaComErro` (0/1) e,
    se `fonte` está no catálogo de métricas, `LatenciaRequisicao{fonte}` medida só em volta do
    `http.get` (a pausa de cortesia fica de fora). Nenhum cabeçalho de trace sai para a fonte.
    """

    def __init__(
        self,
        http: httpx.Client,
        intervalo_s: float = INTERVALO_S,
        dormir: Callable[[float], None] = time.sleep,
        marcador_bloqueio: str | None = None,
        *,
        fonte: str = "desconhecida",
        cronometro: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._fonte = fonte
        self._cronometro = cronometro
        self._http.headers["User-Agent"] = USER_AGENT
        self._intervalo_s = intervalo_s
        self._dormir = dormir
        self._bloqueio = re.compile(marcador_bloqueio) if marcador_bloqueio else None
        self._primeira = True

    @property
    def cookies(self) -> httpx.Cookies:
        return self._http.cookies

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """`headers` somam-se aos do cliente só nesta requisição (o User-Agent fica)."""
        if not self._primeira:
            self._dormir(self._intervalo_s)
        self._primeira = False
        try:
            resposta = self._get_medido(url, params, headers)
        except ErroFonte:
            metricas.registrar("PaginaComErro", 1)
            raise
        metricas.registrar("PaginaComErro", 0)
        return resposta

    def _get_medido(
        self, url: str, params: dict[str, Any] | None, headers: dict[str, str] | None
    ) -> httpx.Response:
        host = self._http.base_url.join(url).host or "desconhecido"
        with tracer().start_as_current_span(
            "GET",
            kind=SpanKind.CLIENT,
            attributes={
                "http.request.method": "GET",
                "server.address": host,
                "radar.fonte": self._fonte,
            },
        ) as span:
            inicio = self._cronometro()
            status: int | None = None
            try:
                resposta = self._http.get(url, params=params, headers=headers)
                status = resposta.status_code
            except httpx.HTTPError as erro:
                span.set_attribute("error.type", type(erro).__name__)
                raise ErroFonte(f"{type(erro).__name__} em {url}: {erro}") from erro
            finally:
                self._medir(host, status, self._cronometro() - inicio)
            span.set_attribute("http.response.status_code", resposta.status_code)
            if resposta.status_code >= 400:
                span.set_attribute("error.type", str(resposta.status_code))
                span.set_status(StatusCode.ERROR)
                raise ErroFonte(
                    f"HTTP {resposta.status_code} em {url}", status=resposta.status_code
                )
            if self._bloqueio is not None and self._bloqueio.search(resposta.text):
                span.set_attribute("error.type", "bloqueio")
                raise ErroFonte(f"bloqueio em {url}")
            return resposta

    def _medir(self, host: str, status: int | None, duracao: float) -> None:
        if self._fonte in metricas.CATALOGO["LatenciaRequisicao"].valores_dimensao["fonte"]:
            metricas.registrar("LatenciaRequisicao", duracao, {"fonte": self._fonte})
        logger.info(
            "requisicao_http",
            extra={
                "fonte": self._fonte,
                "host": host,
                "status": status,
                "duracao_s": round(duracao, 3),
                "tentativa": 1,  # o ClienteEducado não repete
            },
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ClienteEducado":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class FonteRetrato(Protocol):
    """Uma fonte lida por retrato. `marcador_bloqueio` é um regex opcional (None = sem).

    `rotulo_saida` é a `situacao_original` gravada no edital que sai de um retrato completo
    ("saiu da lista" na Sanepar; "fora da janela" na Compesa, cujo retrato é uma janela).
    """

    nome: str
    minimo_registros: int
    marcador_bloqueio: str | None
    rotulo_saida: str

    def coletar(self, cliente: ClienteEducado) -> Iterator[PaginaColetada]:
        """Páginas brutas em ordem; lança `ErroFonte` na primeira falha."""
        ...

    def extrair(self, paginas: list[PaginaColetada]) -> list[dict[str, Any]]:
        """Dicionários normalizados do retrato inteiro."""
        ...


@dataclass
class ResumoRetrato:
    retrato_id: int | None
    status: StatusRetrato
    registros: int = 0
    erro: str | None = None


@dataclass(frozen=True)
class InfoRetrato:
    fonte: str
    status: str
    iniciado_em: datetime
    concluido_em: datetime | None
    registros: int
    erro: str | None


def _agora_real() -> datetime:
    return datetime.now(UTC)


def ultimo_retrato(sessao: Session, fonte: str) -> InfoRetrato | None:
    """O último retrato terminado (concluido ou falha) da fonte; em andamento não conta."""
    retrato = sessao.scalars(
        select(ColetaRetrato)
        .where(ColetaRetrato.fonte == fonte, ColetaRetrato.status != "em_andamento")
        .order_by(desc(ColetaRetrato.iniciado_em), desc(ColetaRetrato.id))
        .limit(1)
    ).first()
    if retrato is None:
        return None
    return InfoRetrato(
        fonte=retrato.fonte,
        status=retrato.status,
        iniciado_em=retrato.iniciado_em,
        concluido_em=retrato.concluido_em,
        registros=retrato.registros,
        erro=retrato.erro,
    )


def executar_retrato(
    sessao: Session,
    fonte: FonteRetrato,
    cliente: ClienteEducado,
    agora: datetime,
    relogio: Callable[[], datetime] | None = None,
    trava_mantida: bool = False,
) -> ResumoRetrato:
    """Lê a lista inteira da fonte e devolve o resumo. Não grava editais (é do consumidor).

    Com `trava_mantida=True` quem chama já segura `trava_fonte` (e cuida de renová-la).
    Sem isso, o lease da fonte é renovado a cada página; se outro processo o tomou, o
    retrato termina em falha ("perdeu a trava da fonte").
    `agora` é o carimbo `iniciado_em` (o que a auditoria compara); `concluido_em` e
    `coletada_em` usam o relógio real (`relogio`, injetável nos testes).
    """
    relogio = relogio or _agora_real
    if trava_mantida:
        return _executar(sessao, fonte, cliente, agora, relogio, lambda: True)
    dono = uuid.uuid4()
    if not obter_trava(sessao, fonte.nome, dono, DURACAO_PADRAO):
        logger.warning("retrato_em_andamento_noutro_processo", extra={"fonte": fonte.nome})
        return ResumoRetrato(retrato_id=None, status="ocupado")

    def renovar() -> bool:
        return obter_trava(sessao, fonte.nome, dono, DURACAO_PADRAO)

    try:
        return _executar(sessao, fonte, cliente, agora, relogio, renovar)
    finally:
        liberar_trava(sessao, fonte.nome, dono)


def _executar(
    sessao: Session,
    fonte: FonteRetrato,
    cliente: ClienteEducado,
    agora: datetime,
    relogio: Callable[[], datetime],
    renovar: Callable[[], bool],
) -> ResumoRetrato:
    """O retrato dentro do span `coleta.retrato` (raiz: a unidade de trabalho é o retrato).

    A mensagem `retrato_coletado` nasce dentro do span e leva o contexto dele. As métricas da
    fonte são descarregadas no fim, com sucesso ou não.
    """
    try:
        with tracer().start_as_current_span(
            "coleta.retrato", context=Context(), attributes={"radar.fonte": fonte.nome}
        ) as span:
            resumo = _executar_retrato(sessao, fonte, cliente, agora, relogio, renovar)
            span.set_attribute("radar.desfecho", resumo.status)
            span.set_attribute("radar.retrato.registros", resumo.registros)
            if resumo.status != "concluido":
                span.set_status(StatusCode.ERROR, (resumo.erro or "")[:200])
            return resumo
    finally:
        metricas.descarregar()


def _executar_retrato(
    sessao: Session,
    fonte: FonteRetrato,
    cliente: ClienteEducado,
    agora: datetime,
    relogio: Callable[[], datetime],
    renovar: Callable[[], bool],
) -> ResumoRetrato:
    # Com a trava da fonte, um retrato em_andamento só pode ser sobra de execução interrompida.
    sessao.execute(
        update(ColetaRetrato)
        .where(ColetaRetrato.fonte == fonte.nome, ColetaRetrato.status == "em_andamento")
        .values(status="falha", erro="interrompido")
    )
    anterior = sessao.scalar(
        select(ColetaRetrato.registros)
        .where(ColetaRetrato.fonte == fonte.nome, ColetaRetrato.status == "concluido")
        .order_by(desc(ColetaRetrato.iniciado_em), desc(ColetaRetrato.id))
        .limit(1)
    )
    retrato = ColetaRetrato(
        fonte=fonte.nome, status="em_andamento", iniciado_em=agora, paginas=0, registros=0
    )
    sessao.add(retrato)
    sessao.commit()
    retrato_id = retrato.id
    inicio = time.monotonic()
    dados = {"fonte": fonte.nome, "retrato_id": retrato_id}
    span = trace.get_current_span()
    span.set_attribute("radar.retrato.id", retrato_id)
    logger.info("retrato_iniciado", extra=dados)

    try:
        paginas: list[PaginaColetada] = []
        for numero, pagina in enumerate(fonte.coletar(cliente), start=1):
            if not renovar():
                logger.warning("retrato_trava_perdida", extra=dados)
                raise ErroFonte("perdeu a trava da fonte")
            _gravar_pagina(sessao, retrato, numero, pagina, relogio())
            paginas.append(pagina)
        normalizados = fonte.extrair(paginas)
    except ErroFonte as erro:
        span.record_exception(erro)
        return _falhar(sessao, retrato, str(erro), inicio)
    except Exception as erro:  # parser quebrado etc.; Ctrl+C continua parando o processo
        span.record_exception(erro)
        sessao.rollback()
        logger.exception("retrato_erro_inesperado", extra=dados)
        return _falhar(sessao, retrato, repr(erro), inicio)

    registros = len(normalizados)
    ids_listados = {
        str(n["id_na_fonte"]) for n in normalizados if n.get("id_na_fonte") not in (None, "")
    }
    # O mínimo vale para os ids distintos: pega também um seletor de id quebrado (todas as
    # linhas sem id, ou com o mesmo id), não só uma lista vazia.
    if len(ids_listados) < fonte.minimo_registros:
        logger.warning(
            "retrato_abaixo_do_minimo",
            extra={
                **dados,
                "registros": registros,
                "ids_listados": len(ids_listados),
                "minimo": fonte.minimo_registros,
            },
        )
        return _falhar(
            sessao,
            retrato,
            f"abaixo do minimo: {len(ids_listados)} < {fonte.minimo_registros}",
            inicio,
        )
    if anterior is not None and registros < anterior * FRACAO_ENCOLHEU:
        logger.warning(
            "retrato_encolheu", extra={**dados, "registros": registros, "anterior": anterior}
        )

    quando = relogio()
    retrato.status = "concluido"
    retrato.registros = registros
    retrato.concluido_em = quando
    span.set_attribute("radar.retrato.paginas", retrato.paginas)
    # Mesmo commit que conclui: ou o retrato fica concluído com a mensagem, ou nenhum dos dois.
    enfileirar(sessao, "retrato_coletado", {"retrato_id": retrato_id})
    sessao.commit()
    logger.info(
        "retrato_concluido",
        extra={
            **dados,
            "paginas": retrato.paginas,
            "registros": registros,
            "duracao_s": round(time.monotonic() - inicio, 3),
        },
    )
    return ResumoRetrato(retrato_id=retrato_id, status="concluido", registros=registros)


def paginas_do_retrato(sessao: Session, retrato_id: int) -> list[PaginaColetada]:
    """As páginas brutas do retrato, em ordem, como a fonte as entregou a `extrair`.

    O JSON volta re-serializado (`json.dumps`): o texto pode diferir do original em espaços
    e escapes, mas `extrair` lê o mesmo conteúdo.
    """
    paginas = sessao.scalars(
        select(PaginaBruta)
        .where(PaginaBruta.retrato_id == retrato_id)
        .order_by(PaginaBruta.numero_pagina)
    )
    return [_reconstruir(pagina) for pagina in paginas]


def _reconstruir(pagina: PaginaBruta) -> PaginaColetada:
    # O CHECK da tabela garante o formato e o conteúdo certo para cada um.
    formato = cast(Formato, pagina.formato)
    if formato == "json":
        texto = json.dumps(pagina.conteudo, ensure_ascii=False)
    else:
        texto = pagina.conteudo_texto or ""
    return PaginaColetada(url=pagina.url or "", formato=formato, texto=texto)


@dataclass(frozen=True)
class EstadoRetrato:
    id: int
    fonte: str
    status: str
    iniciado_em: datetime
    processado_em: datetime | None
    paginas: int


def estado_retrato(sessao: Session, retrato_id: int) -> EstadoRetrato | None:
    """O que o consumidor precisa saber do retrato (None se o id não existe)."""
    retrato = sessao.get(ColetaRetrato, retrato_id, populate_existing=True)
    if retrato is None:
        return None
    return EstadoRetrato(
        id=retrato.id,
        fonte=retrato.fonte,
        status=retrato.status,
        iniciado_em=retrato.iniciado_em,
        processado_em=retrato.processado_em,
        paginas=retrato.paginas,
    )


def ha_retrato_mais_novo_processado(sessao: Session, retrato: EstadoRetrato) -> bool:
    """Existe retrato da mesma fonte, mais novo e já processado? Mesma ordem de `limpar_brutos`."""
    mais_novo = sessao.scalar(
        select(ColetaRetrato.id)
        .where(
            ColetaRetrato.fonte == retrato.fonte,
            ColetaRetrato.processado_em.is_not(None),
            tuple_(ColetaRetrato.iniciado_em, ColetaRetrato.id)
            > tuple_(literal(retrato.iniciado_em), literal(retrato.id)),
        )
        .limit(1)
    )
    return mais_novo is not None


def marcar_retrato_processado(sessao: Session, retrato_id: int, quando: datetime) -> None:
    """`processado_em` no retrato e `processada_em` nas páginas dele. Sem commit."""
    sessao.execute(
        update(ColetaRetrato).where(ColetaRetrato.id == retrato_id).values(processado_em=quando)
    )
    sessao.execute(
        update(PaginaBruta).where(PaginaBruta.retrato_id == retrato_id).values(processada_em=quando)
    )


def retratos_sem_processar(sessao: Session) -> int:
    """Retratos concluídos que o consumidor ainda não gravou (sem `processado_em`)."""
    return int(
        sessao.scalar(
            select(func.count())
            .select_from(ColetaRetrato)
            .where(ColetaRetrato.status == "concluido", ColetaRetrato.processado_em.is_(None))
        )
        or 0
    )


def marcar_retrato_falha(sessao: Session, retrato_id: int, erro: str) -> None:
    """Rebaixa um retrato para `falha` (o registro dos editais falhou depois). Com commit."""
    sessao.execute(
        update(ColetaRetrato)
        .where(ColetaRetrato.id == retrato_id)
        .values(status="falha", erro=erro[:LIMITE_ERRO])
    )
    sessao.commit()


def limpar_brutos(
    sessao: Session,
    fonte: str,
    manter_concluidos: int = MANTER_CONCLUIDOS,
    manter_falhas: int = MANTER_FALHAS,
) -> int:
    """Apaga as páginas brutas dos retratos antigos da fonte; devolve quantas. Com commit.

    Ficam as páginas dos `manter_concluidos` retratos processados (`processado_em`) mais
    novos e dos `manter_falhas` em falha mais novos (a falha é o que se quer diagnosticar).
    Um retrato concluído ainda não processado nunca perde as páginas (o consumidor vai
    lê-las), nem conta entre os mantidos. As linhas de `coleta_retrato` ficam todas: a
    auditoria e o histórico de contagens continuam valendo. Um retrato em andamento nunca é
    tocado.
    """

    def excedentes(manter: int, *condicoes: Any) -> Any:
        return (
            select(ColetaRetrato.id)
            .where(ColetaRetrato.fonte == fonte, *condicoes)
            .order_by(desc(ColetaRetrato.iniciado_em), desc(ColetaRetrato.id))
            .offset(manter)
        )

    processados = excedentes(
        manter_concluidos,
        ColetaRetrato.status == "concluido",
        ColetaRetrato.processado_em.is_not(None),
    )
    falhas = excedentes(manter_falhas, ColetaRetrato.status == "falha")
    comando = delete(PaginaBruta).where(
        PaginaBruta.retrato_id.in_(processados) | PaginaBruta.retrato_id.in_(falhas)
    )
    removidas: int = sessao.execute(comando).rowcount  # type: ignore[attr-defined]
    sessao.commit()
    logger.info("brutos_removidos", extra={"fonte": fonte, "paginas": removidas})
    return removidas


def _gravar_pagina(
    sessao: Session, retrato: ColetaRetrato, numero: int, pagina: PaginaColetada, quando: datetime
) -> None:
    conteudo: Any = None
    texto: str | None = pagina.texto
    if pagina.formato == "json":
        try:
            conteudo = json.loads(pagina.texto)
        except ValueError as erro:
            raise ErroFonte(f"corpo não é JSON em {pagina.url}: {pagina.texto[:200]!r}") from erro
        texto = None
    sessao.add(
        PaginaBruta(
            retrato_id=retrato.id,
            numero_pagina=numero,
            formato=pagina.formato,
            conteudo=conteudo,
            conteudo_texto=texto,
            url=pagina.url,
            coletada_em=quando,
        )
    )
    retrato.paginas = numero
    sessao.commit()  # checkpoint: a página sobrevive a uma falha posterior


def _falhar(sessao: Session, retrato: ColetaRetrato, erro: str, inicio: float) -> ResumoRetrato:
    erro = erro[:LIMITE_ERRO]
    sessao.execute(
        update(ColetaRetrato)
        .where(ColetaRetrato.id == retrato.id)
        .values(status="falha", erro=erro)
    )
    sessao.commit()
    logger.warning(
        "retrato_falhou",
        extra={
            "fonte": retrato.fonte,
            "retrato_id": retrato.id,
            "erro": erro,
            "duracao_s": round(time.monotonic() - inicio, 3),
        },
    )
    return ResumoRetrato(retrato_id=retrato.id, status="falha", erro=erro)
