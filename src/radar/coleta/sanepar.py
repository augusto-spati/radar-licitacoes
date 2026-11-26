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

_DATA = re.compile(r"(\d{2})/(\d{2})/(\d{4})(?:\s+às\s+(\d{1,2})[h:](\d{2}))?")
_DETALHE_URL = re.compile(r"/licitacao/detalhe/(\d+)/(\d+)")
_SITUACAO: dict[str, str] = {"abertura": "aberta", "julgamento": "em_julgamento"}


def _limpo(texto: str | None) -> str:
    return " ".join((texto or "").split())


def parse_data(texto: str | None, exige_hora: bool = False) -> datetime | None:
    """`dd/mm/aaaa [às HHhMM]`. Em intervalo ("a até b") vale o fim. Sem data: None."""
    achadas = _DATA.findall(texto or "")
    if not achadas:
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

    def coletar(self, cliente: ClienteEducado) -> Iterator[PaginaColetada]:
        caminhos: dict[str, None] = {}  # ordem de chegada, sem repetir
        for situacao in SITUACOES:
            vistos: set[str] = set()
            for pagina in range(MAX_PAGINAS):
                url = _url_lista(situacao, pagina)
                html = cliente.get(url).text
                linhas = ler_lista(html).linhas
                if not linhas:
                    yield PaginaColetada(url, "html", html)
                    break
                ids = {linha.numero for linha in linhas}
                if ids <= vistos:
                    logger.warning("lista_repetida", extra={"url": url})
                    break
                vistos |= ids
                yield PaginaColetada(url, "html", html)
                for linha in linhas:
                    caminhos.setdefault(linha.caminho_detalhe)
            else:
                raise ErroFonte(f"lista sem fim: mais de {MAX_PAGINAS} páginas em {situacao}")
        for caminho in caminhos:
            url = f"{BASE}{caminho}"
            yield PaginaColetada(url, "html", cliente.get(url).text)

    def extrair(self, paginas: list[PaginaColetada]) -> list[dict[str, Any]]:
        linhas: dict[str, LinhaLista] = {}  # a última lista que traz o edital vence
        detalhes: dict[str, tuple[str, Detalhe]] = {}
        for pagina in paginas:
            if _e_lista(pagina.url):
                for linha in ler_lista(pagina.texto).linhas:
                    linhas[linha.numero] = linha
                continue
            achado = _DETALHE_URL.search(pagina.url)
            if achado:
                numero = f"{achado.group(1)}/{achado.group(2)}"
                detalhes[numero] = (pagina.url, ler_detalhe(pagina.texto))
        return [self._normalizar(linha, detalhes.get(numero)) for numero, linha in linhas.items()]

    @staticmethod
    def _normalizar(linha: LinhaLista, achado: tuple[str, Detalhe] | None) -> dict[str, Any]:
        url, detalhe = achado if achado else (f"{BASE}{linha.caminho_detalhe}", None)
        situacao = linha.situacao or (detalhe.situacao if detalhe else "")
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
            "id_na_fonte": f"{TIPO}-{linha.numero}",
            "objeto": (detalhe.objeto if detalhe else "") or linha.objeto,
            "orgao_nome": "SANEPAR",
            "orgao_cnpj": CNPJ,
            "uf": "PR",
            "municipio": None,
            "modalidade_nome": (detalhe.modalidade if detalhe else "") or linha.modalidade,
            "situacao_original": situacao,
            "situacao_normalizada": _SITUACAO.get(situacao.casefold(), "outra"),
            "valor_estimado": None,
            "data_publicacao": publicacao,
            "data_abertura_proposta": abertura,
            "data_encerramento_proposta": encerramento,
            "link_origem": url,
            "anexos": detalhe.anexos if detalhe else [],
            "bruto": bruto,
        }
