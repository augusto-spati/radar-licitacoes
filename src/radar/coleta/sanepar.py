"""Coletor da Sanepar (Obras e Engenharia): lista paginada mais um detalhe por edital.

Parser: `selectolax` (seletores CSS sobre o motor Lexbor, uma dependência leve e rápida, sem
precisar de lxml). O site é Drupal renderizado no servidor, sem JS (ver a nota
`docs/superpowers/specs/2026-11-26-fonte-sanepar.md`).

Formatos de data vistos nas amostras (`tests/dados/fontes/`):
- lista, "Data de Publicação": `24/11/2026`; "Data de Abertura": `18/12/2026 às 10h30`;
- detalhe, "Data de Abertura" e "Propostas": `15/12/2026 às 09h30` (um instante só, nas amostras);
  "Disponibilidade": `16/11/2026 até 15/12/2026 às 09h30` (intervalo; não é lida, mas o parser
  de data pega o FIM de qualquer intervalo, caso "Propostas" passe a vir assim).
Também aceita `10:30` no lugar de `10h30`. Sem hora, vale meia-noite (abertura e propostas
logam `data_sem_hora`; publicação é sempre só data).

A página não traz data de atualização: o sinal de mudança é o hash dos campos e dos anexos
(ver `registrar_normalizados`), e o detalhe é buscado a cada rodada para pegar anexo novo.

A situação de um edital é a da lista que o trouxe (`Situacao=` da URL), e não o selo: cada linha
tem de trazer o selo da situação pedida, senão o site ignorou o filtro e o retrato falha. A lista
de Abertura nunca fica vazia na prática; vazia na página 0 é tratada como site quebrado, para que a
reconciliação não encerre todos os editais abertos por causa de uma página de erro. A de
Julgamento pode estar vazia. O selo do detalhe pode atrasar em relação à lista: divergência só
loga `situacao_desconhecida`.

Um detalhe que responde erro HTTP isolado (404, 500...) não derruba o retrato: o edital fica com
os dados da lista, sem anexos, e loga `detalhe_falhou`. 403, 429, bloqueio e falha de transporte
continuam parando tudo, e erro em mais de 20% dos detalhes também (é o site fora do ar).
"""

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from selectolax.lexbor import LexborHTMLParser, LexborNode

from radar.coleta.retratos import ClienteEducado, ErroFonte, PaginaColetada

logger = logging.getLogger(__name__)

BASE = "https://www.sanepar.com.br"
TIPO = "OE"
SITUACOES = ("Abertura", "Julgamento")  # as duas que alimentam o radar; ordem de coleta
MAX_PAGINAS = 100  # trava contra paginação sem fim (a lista real tem 3 páginas)
CNPJ = "76484013000145"
FRACAO_DETALHES_COM_ERRO = 0.2
_BLOQUEIO = (403, 429)  # status que param o retrato mesmo num detalhe

_DATA = re.compile(r"(\d{2})/(\d{2})/(\d{4})(?:\s+às\s+(\d{1,2})[h:](\d{2}))?")
_DETALHE_URL = re.compile(r"/licitacao/detalhe/(\d+)/(\d+)")
_SITUACAO: dict[str, str] = {"abertura": "aberta", "julgamento": "em_julgamento"}
_SITUACAO_DA_URL = re.compile(r"[?&]Situacao=([^&#]+)")


def _limpo(texto: str | None) -> str:
    return " ".join((texto or "").split())


def parse_data(texto: str | None, exige_hora: bool = False) -> datetime | None:
    """`dd/mm/aaaa [às HHhMM]`. Em intervalo ("a até b") vale o fim. Sem data: None."""
    achadas = _DATA.findall(texto or "")
    if not achadas:
        if _limpo(texto):
            logger.debug("data_nao_reconhecida", extra={"texto": _limpo(texto)})
        return None
    dia, mes, ano, hora, minuto = achadas[-1]
    try:
        if not hora:
            if exige_hora:
                logger.warning("data_sem_hora", extra={"texto": _limpo(texto)})
            return datetime(int(ano), int(mes), int(dia))
        return datetime(int(ano), int(mes), int(dia), int(hora), int(minuto))
    except ValueError:
        logger.warning("data_invalida", extra={"texto": _limpo(texto)})
        return None


def _iso(valor: datetime | None) -> str | None:
    return valor.isoformat() if valor else None


@dataclass(frozen=True)
class LinhaLista:
    numero: str  # "352/26"
    caminho_detalhe: str  # "/licitacao/detalhe/352/26"
    objeto: str
    modalidade: str
    data_publicacao: datetime | None
    data_abertura: datetime | None
    situacao: str


@dataclass(frozen=True)
class Lista:
    total: int | None  # do contador "57 editais encontrados"; None na página vazia
    linhas: list[LinhaLista] = field(default_factory=list)


@dataclass(frozen=True)
class Detalhe:
    titulo: str
    situacao: str
    objeto: str
    modalidade: str
    data_publicacao: datetime | None
    data_abertura: datetime | None
    propostas: datetime | None
    anexos: list[dict[str, str]] = field(default_factory=list)


def _texto(no: LexborNode | None) -> str:
    return _limpo(no.text()) if no is not None else ""


def ler_lista(html: str) -> Lista:
    arvore = LexborHTMLParser(html)
    contador = arvore.css_first(".edt-results-count")
    if contador is None:
        # Depois da última página o site mostra "Nenhum edital encontrado" no lugar do contador.
        if arvore.css_first(".edt-empty") is not None:
            return Lista(total=None)
        raise ErroFonte("layout: lista sem contador")
    achado = re.search(r"\d+", contador.text())
    linhas: list[LinhaLista] = []
    for tr in arvore.css("tbody > tr"):
        link = tr.css_first("a.edt-link")
        if link is None:
            continue
        datas = tr.css("td.edt-col-date")
        linhas.append(
            LinhaLista(
                numero=_texto(link),
                caminho_detalhe=link.attributes.get("href") or "",
                objeto=_texto(tr.css_first("td.edt-col-objeto")),
                modalidade=_texto(tr.css_first("td.edt-col-modalidade")),
                data_publicacao=parse_data(_texto(datas[0])) if len(datas) > 0 else None,
                data_abertura=parse_data(_texto(datas[1]), exige_hora=True)
                if len(datas) > 1
                else None,
                situacao=_texto(tr.css_first("span.edt-badge")),
            )
        )
    return Lista(total=int(achado.group()) if achado else None, linhas=linhas)


def _anexos(arvore: LexborHTMLParser) -> list[dict[str, str]]:
    anexos: list[dict[str, str]] = []
    for link in arvore.css("a.sli-occurrence-link"):
        href = link.attributes.get("href") or ""
        nome = _texto(link)
        achado = re.search(r"/licitacao/arquivo/([^/?#]+)", href)
        if achado:
            anexos.append({"nome": nome, "id": achado.group(1)})
            continue
        # `/licitacao/documento?nome=...zip`: sem id numérico, o nome do arquivo é a chave.
        nomes = parse_qs(urlparse(href).query).get("nome")
        if nomes:
            anexos.append({"nome": nome, "id": nomes[0]})
    return anexos


def ler_detalhe(html: str) -> Detalhe:
    arvore = LexborHTMLParser(html)
    titulo = arvore.css_first("h2.sli-contract-title")
    if titulo is None:
        raise ErroFonte("layout: detalhe sem título")
    campos: dict[str, str] = {}
    for item in arvore.css(".sli-info-item"):
        rotulo = item.css_first(".sli-info-label")
        valor = item.css_first(".sli-info-value")
        if rotulo is not None and valor is not None:
            campos[_texto(rotulo)] = _texto(valor)
    return Detalhe(
        titulo=_texto(titulo),
        situacao=_texto(arvore.css_first(".sli-status-badge")),
        objeto=campos.get("Objeto", ""),
        modalidade=campos.get("Modalidade", ""),
        data_publicacao=parse_data(campos.get("Data de Publicação")),
        data_abertura=parse_data(campos.get("Data de Abertura"), exige_hora=True),
        propostas=parse_data(campos.get("Propostas"), exige_hora=True),
        anexos=_anexos(arvore),
    )


def _url_lista(situacao: str, pagina: int) -> str:
    return f"{BASE}/licitacoes?Tipo={TIPO}&Situacao={situacao}&page={pagina}"


def _e_lista(url: str) -> bool:
    return "/licitacoes?" in url


def _situacao_da_url(url: str) -> str:
    achado = _SITUACAO_DA_URL.search(url)
    if achado is None or achado.group(1).casefold() not in _SITUACAO:
        raise ErroFonte(f"layout: lista sem situação conhecida na URL {url}")
    return achado.group(1)


def _conferir_situacao(lista: Lista, situacao: str) -> None:
    """Toda linha tem o selo da situação pedida; senão o filtro da URL foi ignorado."""
    for linha in lista.linhas:
        if linha.situacao.casefold() != situacao.casefold():
            logger.warning(
                "situacao_inesperada_na_lista",
                extra={"pedida": situacao, "selo": linha.situacao, "numero": linha.numero},
            )
            raise ErroFonte("layout: situação inesperada na lista")


class FonteSanepar:
    """Obras e Engenharia da Sanepar, em retrato (Abertura e Julgamento)."""

    nome = "sanepar"
    # A lista aberta de OE tem 57 editais (amostra de 26/11/2026); 5 pega seletor quebrado e
    # lista truncada sem disparar com uma semana fraca de publicações.
    minimo_registros = 5
    # CleanTalk só injeta cookies e um script detector, e esse script está em toda resposta
    # normal: não há texto de bloqueio conhecido. 403/429 e página sem o contador já param
    # o retrato; um regex aqui só entra quando alguém ver uma página de bloqueio de verdade.
    marcador_bloqueio: str | None = None
    rotulo_saida = "saiu da lista"

    def __init__(self) -> None:
        # Listas já lidas em `coletar`, para `extrair` não ler (e logar) cada página de novo.
        self._listas: dict[tuple[str, int, int], Lista] = {}

    def _ler_lista(self, url: str, html: str) -> Lista:
        chave = (url, len(html), hash(html))
        lista = self._listas.get(chave)
        if lista is None:
            lista = self._listas[chave] = ler_lista(html)
        return lista

    def coletar(self, cliente: ClienteEducado) -> Iterator[PaginaColetada]:
        self._listas = {}
        caminhos: dict[str, None] = {}  # ordem de chegada, sem repetir
        for situacao in SITUACOES:
            vistos: set[str] = set()
            esperado = 0
            for pagina in range(MAX_PAGINAS):
                url = _url_lista(situacao, pagina)
                html = cliente.get(url).text
                lista = self._ler_lista(url, html)
                if not lista.linhas:
                    if lista.total:
                        # Contador sem linhas: seletor quebrado, não fim da lista (truncaria).
                        raise ErroFonte("layout: contador sem linhas")
                    if pagina == 0 and situacao == "Abertura":
                        # Sem isto, uma página de erro aqui encerraria todos os abertos.
                        raise ErroFonte("layout: lista de Abertura vazia")
                    yield PaginaColetada(url, "html", html)
                    break
                _conferir_situacao(lista, situacao)
                esperado = lista.total or 0
                ids = {linha.numero for linha in lista.linhas}
                if ids <= vistos:
                    logger.warning("lista_repetida", extra={"url": url})
                    break
                vistos |= ids
                yield PaginaColetada(url, "html", html)
                for linha in lista.linhas:
                    caminhos.setdefault(linha.caminho_detalhe)
            else:
                raise ErroFonte(f"lista sem fim: mais de {MAX_PAGINAS} páginas em {situacao}")
            if len(vistos) < esperado:
                raise ErroFonte(f"layout: lista incompleta: {len(vistos)} de {esperado}")
        falhas = 0
        for caminho in caminhos:
            url = f"{BASE}{caminho}"
            try:
                resposta = cliente.get(url)
            except ErroFonte as erro:
                if erro.status is None or erro.status in _BLOQUEIO:
                    raise
                falhas += 1
                logger.warning("detalhe_falhou", extra={"url": url, "status": erro.status})
                if falhas > FRACAO_DETALHES_COM_ERRO * len(caminhos):
                    raise ErroFonte(f"detalhes com erro: {falhas} de {len(caminhos)}") from erro
                continue  # o edital fica com os dados da lista, sem anexos
            yield PaginaColetada(url, "html", resposta.text)

    def extrair(self, paginas: list[PaginaColetada]) -> list[dict[str, Any]]:
        # A última lista que traz o edital vence; a situação é a pedida na URL da lista.
        linhas: dict[str, tuple[LinhaLista, str]] = {}
        detalhes: dict[str, tuple[str, Detalhe]] = {}
        for pagina in paginas:
            # Um detalhe ilegível derruba o retrato inteiro de propósito (fail-loud).
            if _e_lista(pagina.url):
                situacao = _situacao_da_url(pagina.url)
                for linha in self._ler_lista(pagina.url, pagina.texto).linhas:
                    linhas[linha.numero] = (linha, situacao)
                continue
            achado = _DETALHE_URL.search(pagina.url)
            if achado:
                numero = f"{achado.group(1)}/{achado.group(2)}"
                detalhes[numero] = (pagina.url, ler_detalhe(pagina.texto))
        self._listas = {}
        return [
            self._normalizar(linha, situacao, detalhes.get(numero))
            for numero, (linha, situacao) in linhas.items()
        ]

    @staticmethod
    def _normalizar(
        linha: LinhaLista, situacao: str, achado: tuple[str, Detalhe] | None
    ) -> dict[str, Any]:
        url, detalhe = achado if achado else (f"{BASE}{linha.caminho_detalhe}", None)
        id_na_fonte = f"{TIPO}-{linha.numero}"
        if detalhe is not None and detalhe.situacao.casefold() != situacao.casefold():
            # O detalhe pode atrasar em relação à lista: vale a lista, e fica o aviso.
            logger.warning(
                "situacao_desconhecida",
                extra={
                    "fonte": "sanepar",
                    "id_na_fonte": id_na_fonte,
                    "situacao": detalhe.situacao,
                    "lista": situacao,
                },
            )
        abertura = (detalhe.data_abertura if detalhe else None) or linha.data_abertura
        publicacao = (detalhe.data_publicacao if detalhe else None) or linha.data_publicacao
        # Prazo: o campo "Propostas" do detalhe; na falta, a abertura.
        encerramento = (detalhe.propostas if detalhe else None) or abertura
        bruto: dict[str, Any] = {
            "lista": {
                "numero": linha.numero,
                "objeto": linha.objeto,
                "modalidade": linha.modalidade,
                "data_publicacao": _iso(linha.data_publicacao),
                "data_abertura": _iso(linha.data_abertura),
                "situacao": linha.situacao,
            },
            "detalhe": None
            if detalhe is None
            else {
                "titulo": detalhe.titulo,
                "situacao": detalhe.situacao,
                "objeto": detalhe.objeto,
                "modalidade": detalhe.modalidade,
                "data_publicacao": _iso(detalhe.data_publicacao),
                "data_abertura": _iso(detalhe.data_abertura),
                "propostas": _iso(detalhe.propostas),
                "anexos": detalhe.anexos,
            },
        }
        return {
            "fonte": "sanepar",
            "id_na_fonte": id_na_fonte,
            "objeto": (detalhe.objeto if detalhe else "") or linha.objeto,
            "orgao_nome": "SANEPAR",
            "orgao_cnpj": CNPJ,
            "uf": "PR",
            "municipio": None,
            "modalidade_nome": (detalhe.modalidade if detalhe else "") or linha.modalidade,
            "situacao_original": situacao,
            "situacao_normalizada": _SITUACAO[situacao.casefold()],
            "valor_estimado": None,
            "data_publicacao": publicacao,
            "data_abertura_proposta": abertura,
            "data_encerramento_proposta": encerramento,
            "link_origem": url,
            "anexos": detalhe.anexos if detalhe else [],
            "bruto": bruto,
        }
