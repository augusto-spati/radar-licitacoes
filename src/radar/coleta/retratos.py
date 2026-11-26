"""Motor de retratos: a lista inteira de uma fonte, lida a cada rodada, com cortesia.

Invariante central: um retrato só vale como "a lista completa" quando termina `concluido`.
Qualquer página em falha, ou menos registros que o mínimo da fonte, deixa o retrato em
`falha`, e quem chama (a cli) não grava editais nem reconcilia. Este módulo não conhece
`editais`: devolve dicionários normalizados e os ids listados.

Um único retrato por fonte de cada vez: um advisory lock por fonte (chave dupla
`CHAVE_RETRATOS`, `chave_da_fonte(nome)`), separado do lock do coletor do PNCP. Assim uma
rodada agendada e uma manual não se sobrepõem na mesma fonte, mas fontes diferentes (e o
PNCP) não se bloqueiam. A cli segura o lock (`trava_fonte`) durante o retrato e o registro
dos editais, então o ciclo inteiro da fonte é exclusivo.
"""

import json
import logging
import re
import time
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import httpx
from sqlalchemy import Engine, delete, desc, func, select, update
from sqlalchemy.orm import Session

from radar.coleta.modelos import ColetaRetrato, PaginaBruta

logger = logging.getLogger(__name__)

USER_AGENT = "radar-licitacoes-estudo (github.com/augusto-spati/radar-licitacoes)"
INTERVALO_S = 3.0  # entre requisições à mesma fonte
TIMEOUT_SEGUNDOS = 60.0
CHAVE_RETRATOS = 7_240_002  # primeira metade do advisory lock; a segunda é a fonte
FRACAO_ENCOLHEU = 0.5
LIMITE_ERRO = 500
MANTER_CONCLUIDOS = 3  # retratos concluídos cujas páginas brutas ficam guardadas
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
    """

    def __init__(
        self,
        http: httpx.Client,
        intervalo_s: float = INTERVALO_S,
        dormir: Callable[[float], None] = time.sleep,
        marcador_bloqueio: str | None = None,
    ) -> None:
        self._http = http
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
            resposta = self._http.get(url, params=params, headers=headers)
        except httpx.HTTPError as erro:
            raise ErroFonte(f"{type(erro).__name__} em {url}: {erro}") from erro
        if resposta.status_code >= 400:
            raise ErroFonte(f"HTTP {resposta.status_code} em {url}", status=resposta.status_code)
        if self._bloqueio is not None and self._bloqueio.search(resposta.text):
            raise ErroFonte(f"bloqueio em {url}")
        return resposta

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
    normalizados: list[dict[str, Any]] = field(default_factory=list)
    # Todos os ids que a fonte listou, inclusive os que depois falham na validação: é o que
    # a reconciliação considera "ainda na lista".
    ids_listados: set[str] = field(default_factory=set)


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


def chave_da_fonte(nome: str) -> int:
    """Segunda metade do advisory lock: um int4 estável derivado do nome da fonte."""
    return zlib.crc32(nome.encode("utf-8")) & 0x7FFFFFFF


def ids_do_retrato(resumo: ResumoRetrato) -> set[str]:
    return set(resumo.ids_listados)


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


@contextmanager
def trava_fonte(sessao: Session, fonte: str) -> Iterator[bool]:
    """Advisory lock da fonte; rende True se obtido. Não espera: False = outro processo."""
    bind = sessao.get_bind()
    # Conexão dedicada: o lock é da conexão, e a sessão devolve a dela ao pool a cada commit.
    conexao = bind.connect() if isinstance(bind, Engine) else bind
    chaves = (CHAVE_RETRATOS, chave_da_fonte(fonte))
    try:
        obtida = bool(conexao.scalar(select(func.pg_try_advisory_lock(*chaves))))
        try:
            yield obtida
        finally:
            if obtida:
                conexao.scalar(select(func.pg_advisory_unlock(*chaves)))
    finally:
        if conexao is not bind:
            conexao.close()


def executar_retrato(
    sessao: Session,
    fonte: FonteRetrato,
    cliente: ClienteEducado,
    agora: datetime,
    relogio: Callable[[], datetime] | None = None,
    trava_mantida: bool = False,
) -> ResumoRetrato:
    """Lê a lista inteira da fonte e devolve o resumo. Não grava editais (isso é da cli).

    Com `trava_mantida=True` quem chama já segura `trava_fonte` (a cli segura durante o
    retrato e o registro, para que o ciclo inteiro da fonte não se sobreponha).
    `agora` é o carimbo `iniciado_em` (o que a auditoria compara); `concluido_em` e
    `coletada_em` usam o relógio real (`relogio`, injetável nos testes).
    """
    relogio = relogio or _agora_real
    if trava_mantida:
        return _executar(sessao, fonte, cliente, agora, relogio)
    with trava_fonte(sessao, fonte.nome) as obtida:
        if not obtida:
            logger.warning("retrato_em_andamento_noutro_processo", extra={"fonte": fonte.nome})
            return ResumoRetrato(retrato_id=None, status="ocupado")
        return _executar(sessao, fonte, cliente, agora, relogio)


def _executar(
    sessao: Session,
    fonte: FonteRetrato,
    cliente: ClienteEducado,
    agora: datetime,
    relogio: Callable[[], datetime],
) -> ResumoRetrato:
    # Com o lock da fonte, um retrato em_andamento só pode ser sobra de execução interrompida.
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
    logger.info("retrato_iniciado", extra=dados)

    try:
        paginas: list[PaginaColetada] = []
        for numero, pagina in enumerate(fonte.coletar(cliente), start=1):
            _gravar_pagina(sessao, retrato, numero, pagina, relogio())
            paginas.append(pagina)
        normalizados = fonte.extrair(paginas)
    except ErroFonte as erro:
        return _falhar(sessao, retrato, str(erro), inicio)
    except Exception as erro:  # parser quebrado etc.; Ctrl+C continua parando o processo
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
    # As páginas do retrato já foram "processadas" por ele: fora da fila do PNCP.
    sessao.execute(
        update(PaginaBruta).where(PaginaBruta.retrato_id == retrato_id).values(processada_em=quando)
    )
    retrato.status = "concluido"
    retrato.registros = registros
    retrato.concluido_em = quando
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
    return ResumoRetrato(
        retrato_id=retrato_id,
        status="concluido",
        registros=registros,
        normalizados=normalizados,
        ids_listados=ids_listados,
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

    Ficam as páginas dos `manter_concluidos` retratos concluídos mais novos e dos
    `manter_falhas` em falha mais novos (a falha é o que se quer diagnosticar). As linhas de
    `coleta_retrato` ficam todas: a auditoria e o histórico de contagens continuam valendo.
    Um retrato em andamento nunca é tocado.
    """

    def excedentes(status: str, manter: int) -> Any:
        return (
            select(ColetaRetrato.id)
            .where(ColetaRetrato.fonte == fonte, ColetaRetrato.status == status)
            .order_by(desc(ColetaRetrato.iniciado_em), desc(ColetaRetrato.id))
            .offset(manter)
        )

    comando = delete(PaginaBruta).where(
        PaginaBruta.retrato_id.in_(excedentes("concluido", manter_concluidos))
        | PaginaBruta.retrato_id.in_(excedentes("falha", manter_falhas))
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
