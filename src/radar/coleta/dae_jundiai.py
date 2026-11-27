"""Coletor da DAE S/A Jundiaí: o Compra Eletrônica (ASP.NET WebForms) lido com postback.

Nota de pré-requisitos: `docs/superpowers/specs/2026-11-27-fonte-dae-jundiai.md`. O site
`daejundiai.com.br` só publica as encerradas; as licitações em andamento estão no Compra
Eletrônica do CIJUN (`compraeletronica.jundiai.sp.gov.br/dae/aberto/`), embutido no Compra Aberta.

Fluxo, tudo no mesmo cookie (`ASP.NET_SessionId`) e com o `ClienteEducado`:
1. GET `ConsultaLicitacao.aspx`: página 1 da grade `gdvCompras`, já filtrada pelo exercício
   corrente (campo `txtNrexer`, que vem preenchido);
2. as páginas seguintes são o postback `__doPostBack('gdvCompras', 'Page$N')`: um POST com o
   estado do formulário da página anterior (`__VIEWSTATE`, `__EVENTVALIDATION` etc.) mais
   `__EVENTTARGET`/`__EVENTARGUMENT`;
3. o exercício anterior vem da busca do próprio formulário (botão `btnPesquisar` com
   `txtNrexer` trocado), também POST, seguida da mesma paginação. A busca mantém o índice de
   página da grade anterior (vem na página 9 se a última lida foi a 9): nesse caso há um
   postback `Page$1` antes de seguir;
4. o detalhe de cada compra do sistema "NE" (`CompraNEWeb.aspx?cdcompra=N`, modos de disputa,
   alienação, chamada pública...) é GET e traz a data de publicação e os anexos tipados.

POST é exceção aprovada pelo Augusto em 27/11/2026 e fica restrita a esses dois postbacks de
navegação (`_postar_pagina` e `_postar_busca`): nenhum outro botão, nenhum download de anexo
(também é postback, fica para a análise).

Janela: os últimos 12 meses pela "Data/Hora inicial" (como na Compesa). Como a lista vem por
exercício, o retrato lê o exercício corrente e o anterior inteiros e `extrair` descarta o que é
anterior ao corte. Um edital que sai da janela ainda aberto no banco é fechado pela reconciliação
com a situação original "fora da janela".

Completude: cada página tem de trazer o formulário com o exercício pedido, a grade (ou a mensagem
"Nenhum registro encontrado!", só aceita na página 1 de um exercício) e, depois da página 1, o
número da página pedida como página corrente do paginador. Página que não traz nenhum id novo é
erro (sessão perdida devolvendo a mesma página). A paginação segue enquanto o paginador tiver o
link `Page$N+1` (inclusive o "..." do pager do ASP.NET), até `MAX_PAGINAS` por exercício.
Detalhe com erro derruba o retrato: são poucos (uns 25) e sem eles a situação não é inferida.

Situação:
- pregões (`mural.aspx`, outra numeração): o ícone da coluna Situação é o estado do pregão
  (`prop_preco_env` = recebendo propostas etc., `ICONES_PREGAO`); o prazo é a "Data/Hora Final";
- sistema NE: o ícone e o `<select>` do detalhe ficam em "Em Configuracao" (ou presos num estado
  antigo, como o MDF 007/26 em negociação já homologado). A situação é inferida pelos anexos e
  pela data de publicação (`inferir_situacao_ne`): aviso de revogação, anulação ou cancelamento
  encerra; com ata, a sessão já aconteceu (em julgamento até `DIAS_JULGAMENTO_COM_ATA`, depois
  encerrada); sem ata, aberta até `DIAS_ABERTA_SEM_ATA` da publicação, depois "outra". A data de
  abertura do NE só existe no PDF do edital: o prazo fica nulo, e por isso o filtro de abertos
  (que exige prazo futuro) ainda não mostra esses editais. Ler o PDF é trabalho da análise.

Formatos de data: lista `dd/mm/aaaa HH:MM:SS` (hora zero no NE), detalhe `dd/mm/aaaa`.
"""

import logging
import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser, LexborNode

from radar.coleta.retratos import ClienteEducado, ErroFonte, PaginaColetada

logger = logging.getLogger(__name__)

BASE = "https://compraeletronica.jundiai.sp.gov.br/dae/aberto/"
URL_LISTA = f"{BASE}ConsultaLicitacao.aspx"
CNPJ = "03582243000173"
FUSO = ZoneInfo("America/Sao_Paulo")
MAX_PAGINAS = 30  # por exercício; 2026 tem 9 páginas de 10 linhas
JANELA_DIAS = 365
DIAS_JULGAMENTO_COM_ATA = 90
DIAS_ABERTA_SEM_ATA = 180
GRADE = "gdvCompras"
SEM_REGISTROS = "Nenhum registro encontrado!"

# Sigla do id de cada modalidade do `ddlModalidade` (todas as 17 de 27/11/2026), pelo nome sem
# acento. O id é `<sigla>-<número>` (`MDF-2026/00014`): o número só é único dentro da modalidade.
SIGLAS: dict[str, str] = {
    "ADESAO ARP": "ARP",
    "ALIENACAO": "ALI",
    "CHAMADA PUBLICA": "CHP",
    "CONCORRENCIA": "CC",
    "CONVITE": "CV",
    "CONVITE-OBRAS": "CVO",
    "EDITAL DE CONVOCACAO": "EDC",
    "EDITAL DE PRE-QUALIFICACAO": "EPQ",
    "LEILAO": "LEI",
    "MODO DE DISPUTA ABERTO": "MDA",
    "MODO DE DISPUTA FECHADO": "MDF",
    "PMI": "PMI",
    "PREGAO ELETRONICO": "PE",
    "PREGAO PRESENCIAL": "PP",
    "RCE": "RCE",
    "REGIME DIFERENC.CONTRAT. RDC": "RDC",
    "TOMADA DE PRECOS": "TP",
}
NOMES_MODALIDADE: dict[str, str] = {
    "ALI": "Alienação",
    "CHP": "Chamada Pública",
    "MDA": "Modo de Disputa Aberto",
    "MDF": "Modo de Disputa Fechado",
    "PE": "Pregão Eletrônico",
    "PP": "Pregão Presencial",
}

# Ícone da coluna Situação dos pregões → (texto, situação normalizada). Vistos em 27/11/2026.
ICONES_PREGAO: dict[str, tuple[str, str]] = {
    "prop_preco_env": ("Recebendo propostas", "aberta"),
    "cp_negociacao": ("Em negociação", "em_julgamento"),
    "intencao_recurso": ("Intenção de recurso", "em_julgamento"),
    "cp_em_adjudicaca": ("Em adjudicação", "em_julgamento"),
    "adjudicacao": ("Adjudicada", "encerrada"),
    "cp_homologada": ("Homologada", "encerrada"),
    "cp_cancelada": ("Cancelada", "encerrada"),
    "cp_suspensa": ("Suspensa", "outra"),
}
ICONE_CONFIGURACAO = "cp_configuracao"
_AVISO_FIM = re.compile(r"revoga|anula|cancela", re.IGNORECASE)

# Página de bloqueio genérica de WAF/proxy (o estudo não viu nenhuma); 403/429 já param.
MARCADOR_BLOQUEIO = r"<title>\s*(?:Request Rejected|Access Denied|Acesso Negado)\s*</title>"

_DATA = re.compile(r"(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?")
_CDCOMPRA = re.compile(r"(CompraNEWeb|mural)\.aspx\?cdcompra=(\d+)", re.IGNORECASE)
_PAGINA = re.compile(r"Page\$(\d+)")
_URL_LISTA = re.compile(r"#exercicio=(\d{4})&pagina=(\d+)$")
# Campos do formulário que a busca zera (a página 1 do GET vem assim).
_BUSCA_LIMPA = {
    "txtObjeto": "",
    "txtnrlici": "",
    "ddlModalidade": "0",
    "ddlUnidadeCompradora": "0",
    "ddlSituacao": "0",
}


def _limpo(texto: str | None) -> str:
    return " ".join((texto or "").split())


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).upper()


def parse_data(texto: str | None) -> datetime | None:
    """`dd/mm/aaaa [HH:MM[:SS]]`. Vazio: None."""
    achado = _DATA.search(texto or "")
    if not achado:
        if _limpo(texto):
            logger.warning("data_nao_reconhecida", extra={"texto": _limpo(texto)})
        return None
    dia, mes, ano, hora, minuto, _ = achado.groups()
    try:
        return datetime(int(ano), int(mes), int(dia), int(hora or 0), int(minuto or 0))
    except ValueError:
        logger.warning("data_invalida", extra={"texto": _limpo(texto)})
        return None


def _iso(valor: datetime | None) -> str | None:
    return valor.isoformat() if valor else None


# --- formulário e postback -------------------------------------------------------------------


def estado_formulario(html: str) -> dict[str, str]:
    """Os campos que o navegador mandaria no postback do `form1`, como estão na página.

    Inputs com nome (menos botões e checkbox/radio desmarcados) e, de cada `<select>`, a opção
    marcada (ou a primeira). Botão nenhum entra: o postback de paginação não clica em botão.
    """
    arvore = LexborHTMLParser(html)
    form = arvore.css_first("form#form1")
    if form is None:
        raise ErroFonte("layout: página sem o formulário form1")
    campos: dict[str, str] = {}
    for entrada in form.css("input[name]"):
        tipo = (entrada.attributes.get("type") or "text").lower()
        if tipo in ("submit", "button", "image", "reset", "file"):
            continue
        if tipo in ("checkbox", "radio") and "checked" not in entrada.attributes:
            continue
        campos[entrada.attributes.get("name") or ""] = entrada.attributes.get("value") or ""
    for selecao in form.css("select[name]"):
        opcoes = selecao.css("option")
        marcada = next(
            (o for o in opcoes if "selected" in o.attributes), opcoes[0] if opcoes else None
        )
        valor = (marcada.attributes.get("value") or "") if marcada is not None else ""
        campos[selecao.attributes.get("name") or ""] = valor
    return campos


def dados_postback(
    html: str, alvo: str, argumento: str, trocar: dict[str, str] | None = None
) -> dict[str, str]:
    """Estado do formulário da página mais o evento; sem `__VIEWSTATE` é erro de layout."""
    dados = estado_formulario(html)
    for chave in ("__VIEWSTATE", "__EVENTVALIDATION"):
        if not dados.get(chave):
            raise ErroFonte(f"layout: página sem {chave}")
    dados["__EVENTTARGET"] = alvo
    dados["__EVENTARGUMENT"] = argumento
    dados.update(trocar or {})
    return dados


# --- lista -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class LinhaLista:
    modalidade: str  # "MODO DE DISPUTA FECHADO", "PREGÃO ELETRÔNICO"
    numero: str  # "2026/00014"
    sistema: str  # "NE" (CompraNEWeb.aspx) ou "PREGAO" (mural.aspx)
    cdcompra: str
    caminho: str  # "CompraNEWeb.aspx?cdcompra=843"
    objeto: str
    inicio: datetime | None
    fim: datetime | None
    icone: str  # "cp_configuracao", sem pasta nem extensão
    titulo_icone: str  # o title do ícone ("SUSPENSA - IMPUGNAÇÃO DO EDITAL"), quase sempre vazio


@dataclass(frozen=True)
class Lista:
    exercicio: str  # o txtNrexer da página
    pagina_atual: int | None  # o <span> do paginador; None quando não há paginador
    paginas: frozenset[int]  # páginas com link no paginador
    vazia: bool  # "Nenhum registro encontrado!"
    linhas: list[LinhaLista] = field(default_factory=list)


def _texto(no: LexborNode | None) -> str:
    return _limpo(no.text()) if no is not None else ""


def _linha(tr: LexborNode) -> LinhaLista | None:
    link = tr.css_first("a[id$=lnkMural]")
    if link is None:
        return None
    caminho = link.attributes.get("href") or ""
    achado = _CDCOMPRA.search(caminho)
    if achado is None:
        raise ErroFonte(f"layout: link de compra desconhecido {caminho!r}")
    icone = tr.css_first("img[id$=imgSituacao]")
    src = (icone.attributes.get("src") or "") if icone is not None else ""
    return LinhaLista(
        modalidade=_texto(tr.css_first("span[id$=lbldsmodal]")),
        numero=_texto(link),
        sistema="NE" if achado.group(1).lower() == "compraneweb" else "PREGAO",
        cdcompra=achado.group(2),
        caminho=f"{achado.group(1)}.aspx?cdcompra={achado.group(2)}",
        objeto=_texto(tr.css_first("span[id$=lblObjeto]")),
        inicio=parse_data(_texto(tr.css_first("span[id$=lblDTInic]"))),
        fim=parse_data(_texto(tr.css_first("span[id$=lblDTFim]"))),
        icone=src.rsplit("/", 1)[-1].rsplit(".", 1)[0],
        titulo_icone=_limpo((icone.attributes.get("title") or "") if icone is not None else ""),
    )


def ler_lista(html: str) -> Lista:
    arvore = LexborHTMLParser(html)
    exercicio = arvore.css_first("input[name=txtNrexer]")
    if exercicio is None:
        raise ErroFonte("layout: lista sem o campo de exercício")
    ano = _limpo(exercicio.attributes.get("value"))
    grade = arvore.css_first(f"table#{GRADE}")
    if grade is None:
        if SEM_REGISTROS in html and arvore.css_first("div#UpdatePanel2") is not None:
            return Lista(exercicio=ano, pagina_atual=None, paginas=frozenset(), vazia=True)
        raise ErroFonte("layout: lista sem a grade")
    linhas: list[LinhaLista] = []
    paginas: set[int] = set()
    atual: int | None = None
    for tr in grade.css("tr"):
        if "pagination-ys" in (tr.attributes.get("class") or ""):
            for link in tr.css("a"):
                achado = _PAGINA.search(link.attributes.get("href") or "")
                if achado:
                    paginas.add(int(achado.group(1)))
            corrente = tr.css_first("td > span")
            if corrente is not None and _texto(corrente).isdigit():
                atual = int(_texto(corrente))
            continue
        linha = _linha(tr)
        if linha is not None:
            linhas.append(linha)
    if not linhas:
        raise ErroFonte("layout: grade sem linhas")
    return Lista(
        exercicio=ano, pagina_atual=atual, paginas=frozenset(paginas), vazia=False, linhas=linhas
    )


# --- detalhe ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Detalhe:
    titulo: str  # "MODO DE DISPUTA FECHADO - 2026/14 - PMJ"
    objeto: str
    situacao_sistema: str  # a opção marcada no <select> (quase sempre "Em Configuracao")
    publicacao: datetime | None
    anexos: list[dict[str, str]] = field(default_factory=list)  # nome, tipo, arquivo


def ler_detalhe(html: str) -> Detalhe:
    arvore = LexborHTMLParser(html)
    titulo = arvore.css_first("span#CompraNEMod1_lblTitulo")
    if titulo is None:
        raise ErroFonte("layout: detalhe sem título")
    selecao = arvore.css_first("select#CompraNEMod1_ddlSituacao option[selected]")
    anexos: list[dict[str, str]] = []
    tabela = arvore.css_first("table#grdArquivos")
    if tabela is not None:
        for tr in tabela.css("tr"):
            celulas = tr.css("td")
            if len(celulas) < 3:
                continue  # cabeçalho
            anexos.append(
                {
                    "nome": _texto(celulas[0]),
                    "tipo": _texto(celulas[1]),
                    "arquivo": _texto(celulas[2]),
                }
            )
    return Detalhe(
        titulo=_texto(titulo),
        objeto=_texto(arvore.css_first("span#CompraNEMod1_lblDsobjeto")),
        situacao_sistema=_texto(selecao),
        publicacao=parse_data(_texto(arvore.css_first("span#CompraNEMod1_lblDtPubl"))),
        anexos=anexos,
    )


# --- situação --------------------------------------------------------------------------------


def inferir_situacao_ne(linha: LinhaLista, detalhe: Detalhe, hoje: date) -> tuple[str, str]:
    """(situação original, normalizada) de uma compra do sistema NE, pelos anexos e datas."""
    if linha.icone == "cp_cancelada":
        return "Cancelada", "encerrada"
    tipos = {anexo["tipo"].upper() for anexo in detalhe.anexos}
    avisos = [a for a in detalhe.anexos if a["tipo"].upper() == "AVISO"]
    if any(_AVISO_FIM.search(f"{a['nome']} {a['arquivo']}") for a in avisos):
        return "Revogada ou anulada (aviso publicado)", "encerrada"
    publicada = detalhe.publicacao or linha.inicio or datetime.combine(hoje, datetime.min.time())
    dias = (hoje - publicada.date()).days
    if "ATA" in tipos:
        if dias > DIAS_JULGAMENTO_COM_ATA:
            return f"Com ata, publicada há mais de {DIAS_JULGAMENTO_COM_ATA} dias", "encerrada"
        return "Com ata (sessão realizada)", "em_julgamento"
    if dias > DIAS_ABERTA_SEM_ATA:
        return f"Sem ata, publicada há mais de {DIAS_ABERTA_SEM_ATA} dias", "outra"
    return "Sem ata (sessão por realizar)", "aberta"


def situacao_pregao(linha: LinhaLista) -> tuple[str, str]:
    texto, situacao = ICONES_PREGAO.get(linha.icone, ("", ""))
    if not situacao:
        logger.warning(
            "situacao_desconhecida", extra={"fonte": "dae_jundiai", "icone": linha.icone}
        )
        return linha.titulo_icone or linha.icone or "sem ícone", "outra"
    return linha.titulo_icone or texto, situacao


def sigla(modalidade: str) -> str:
    chave = _sem_acento(_limpo(modalidade))
    achada = SIGLAS.get(chave)
    if achada is None:
        logger.warning(
            "modalidade_desconhecida", extra={"fonte": "dae_jundiai", "modalidade": modalidade}
        )
        return re.sub(r"[^A-Z0-9]+", "-", chave).strip("-") or "SEM-MODALIDADE"
    return achada


# --- fonte -----------------------------------------------------------------------------------


def _agora_real() -> datetime:
    return datetime.now(UTC)


def _url_pagina(exercicio: str, pagina: int) -> str:
    """URL gravada da página da lista: o POST não muda a URL, o fragmento diz qual é."""
    return f"{URL_LISTA}#exercicio={exercicio}&pagina={pagina}"


class FonteDaeJundiai:
    """Licitações da DAE S/A Jundiaí (SP), em retrato: Compra Eletrônica, últimos 12 meses."""

    nome = "dae_jundiai"
    # 2026 tem uns 90 registros só no exercício (amostra de 27/11/2026); 20 pega seletor quebrado
    # e lista truncada mesmo em janeiro, quando o exercício novo ainda está vazio.
    minimo_registros = 20
    marcador_bloqueio: str | None = MARCADOR_BLOQUEIO
    rotulo_saida = "fora da janela"

    def __init__(self, relogio: Callable[[], datetime] | None = None) -> None:
        self._relogio = relogio or _agora_real
        self._listas: dict[tuple[str, int, int], Lista] = {}

    def _ler_lista(self, url: str, html: str) -> Lista:
        chave = (url, len(html), hash(html))
        lista = self._listas.get(chave)
        if lista is None:
            lista = self._listas[chave] = ler_lista(html)
        return lista

    def hoje(self) -> date:
        return self._relogio().astimezone(FUSO).date()

    def corte(self) -> date:
        """Primeiro dia da janela: hoje em Brasília menos `JANELA_DIAS`."""
        return self.hoje() - timedelta(days=JANELA_DIAS)

    @staticmethod
    def _postar_pagina(cliente: ClienteEducado, html: str, pagina: int) -> str:
        """Postback de paginação da grade (o link "N" ou "..." do paginador)."""
        dados = dados_postback(html, GRADE, f"Page${pagina}")
        return cliente.postback_leitura(URL_LISTA, dados).text

    @staticmethod
    def _postar_busca(cliente: ClienteEducado, html: str, exercicio: int) -> str:
        """Busca do formulário por exercício, com os outros filtros zerados (só leitura)."""
        trocar = {**_BUSCA_LIMPA, "txtNrexer": str(exercicio), "btnPesquisar": "Pesquisar"}
        dados = dados_postback(html, "", "", trocar)
        return cliente.postback_leitura(URL_LISTA, dados).text

    def coletar(self, cliente: ClienteEducado) -> Iterator[PaginaColetada]:
        self._listas = {}
        corte = self.corte()
        exercicios = list(range(self.hoje().year, corte.year - 1, -1))  # o corrente primeiro
        html = cliente.get(URL_LISTA).text
        detalhes: dict[str, None] = {}
        for exercicio in exercicios:
            if self._ler_lista(URL_LISTA, html).exercicio != str(exercicio):
                html = self._postar_busca(cliente, html, exercicio)
                if self._ler_lista(URL_LISTA, html).pagina_atual not in (None, 1):
                    # A busca guarda a página da grade anterior (viewstate): volta para a 1.
                    html = self._postar_pagina(cliente, html, 1)
            for url, pagina in self._paginas_do_exercicio(cliente, html, str(exercicio)):
                html = pagina  # a última página do exercício é o estado para a busca seguinte
                yield PaginaColetada(url, "html", pagina)
                for linha in self._ler_lista(url, pagina).linhas:
                    if linha.sistema == "NE" and not _antiga(linha, corte):
                        detalhes.setdefault(linha.caminho)
        logger.info("dae_jundiai_detalhes", extra={"detalhes": len(detalhes)})
        for caminho in detalhes:
            url = f"{BASE}{caminho}"
            yield PaginaColetada(url, "html", cliente.get(url).text)

    def _paginas_do_exercicio(
        self, cliente: ClienteEducado, html: str, exercicio: str
    ) -> Iterator[tuple[str, str]]:
        vistos: set[str] = set()
        for pagina in range(1, MAX_PAGINAS + 1):
            if pagina > 1:
                html = self._postar_pagina(cliente, html, pagina)
            url = _url_pagina(exercicio, pagina)
            lista = self._ler_lista(url, html)
            if lista.exercicio != exercicio:
                raise ErroFonte(
                    f"layout: exercício {lista.exercicio!r} na página, esperado {exercicio}"
                )
            if lista.vazia:
                if pagina > 1:
                    raise ErroFonte(f"layout: página {pagina} de {exercicio} sem registros")
                logger.info("dae_jundiai_exercicio_vazio", extra={"exercicio": exercicio})
                yield url, html
                return
            if lista.pagina_atual != pagina and (pagina > 1 or lista.pagina_atual is not None):
                raise ErroFonte(
                    f"layout: pedi a página {pagina} de {exercicio}, veio {lista.pagina_atual}"
                )
            chaves = {f"{linha.sistema}-{linha.cdcompra}" for linha in lista.linhas}
            if not chaves - vistos:
                raise ErroFonte(f"layout: página {pagina} de {exercicio} repete a anterior")
            vistos |= chaves
            yield url, html
            if pagina + 1 not in lista.paginas:
                return
        raise ErroFonte(f"lista sem fim: mais de {MAX_PAGINAS} páginas em {exercicio}")

    def extrair(self, paginas: list[PaginaColetada]) -> list[dict[str, Any]]:
        corte, hoje = self.corte(), self.hoje()
        linhas: dict[str, LinhaLista] = {}
        detalhes: dict[str, Detalhe] = {}
        for pagina in paginas:
            if _URL_LISTA.search(pagina.url):
                for linha in self._ler_lista(pagina.url, pagina.texto).linhas:
                    if _antiga(linha, corte):
                        continue
                    id_na_fonte = f"{sigla(linha.modalidade)}-{linha.numero}"
                    if id_na_fonte in linhas:
                        logger.warning(
                            "id_repetido", extra={"fonte": self.nome, "id_na_fonte": id_na_fonte}
                        )
                    linhas[id_na_fonte] = linha
                continue
            achado = _CDCOMPRA.search(pagina.url)
            if achado and achado.group(1).lower() == "compraneweb":
                detalhes[achado.group(2)] = ler_detalhe(pagina.texto)  # ilegível: falha alto
        self._listas = {}
        normalizados = []
        for id_na_fonte, linha in linhas.items():
            detalhe = detalhes.get(linha.cdcompra) if linha.sistema == "NE" else None
            if linha.sistema == "NE" and detalhe is None:
                raise ErroFonte(f"detalhe ausente de {id_na_fonte}")
            normalizados.append(_normalizar(id_na_fonte, linha, detalhe, hoje))
        return normalizados


def _antiga(linha: LinhaLista, corte: date) -> bool:
    return linha.inicio is not None and linha.inicio.date() < corte


def _anexos_normalizados(detalhe: Detalhe | None) -> list[dict[str, str]]:
    # O download é postback posicional (`grdArquivos$ctl02$lnkArquivo`): a chave estável é o
    # nome do arquivo; o tipo vai junto no nome para a análise escolher edital e planilha.
    if detalhe is None:
        return []
    return [{"nome": f"{a['tipo']}: {a['nome']}", "id": a["arquivo"]} for a in detalhe.anexos]


def _normalizar(
    id_na_fonte: str, linha: LinhaLista, detalhe: Detalhe | None, hoje: date
) -> dict[str, Any]:
    codigo = sigla(linha.modalidade)
    if detalhe is not None:
        original, situacao = inferir_situacao_ne(linha, detalhe, hoje)
        publicacao = detalhe.publicacao or linha.inicio
        prazo = None  # a data da sessão só está no PDF do edital
    else:
        original, situacao = situacao_pregao(linha)
        publicacao = linha.inicio
        prazo = linha.fim  # "Data/Hora Final": fim das propostas e início da disputa
    return {
        "fonte": "dae_jundiai",
        "id_na_fonte": id_na_fonte,
        "objeto": (detalhe.objeto if detalhe else "") or linha.objeto,
        "orgao_nome": "DAE S/A - ÁGUA E ESGOTO (JUNDIAÍ)",
        "orgao_cnpj": CNPJ,
        "uf": "SP",
        "municipio": "Jundiaí",
        "modalidade_nome": NOMES_MODALIDADE.get(codigo, linha.modalidade.title()),
        "situacao_original": original,
        "situacao_normalizada": situacao,
        "valor_estimado": None,
        "data_publicacao": publicacao,
        "data_abertura_proposta": prazo,
        "data_encerramento_proposta": prazo,
        "link_origem": f"{BASE}{linha.caminho}",
        "anexos": _anexos_normalizados(detalhe),
        "bruto": {
            "lista": {
                "modalidade": linha.modalidade,
                "numero": linha.numero,
                "sistema": linha.sistema,
                "cdcompra": linha.cdcompra,
                "objeto": linha.objeto,
                "inicio": _iso(linha.inicio),
                "fim": _iso(linha.fim),
                "icone": linha.icone,
                "titulo_icone": linha.titulo_icone,
            },
            "detalhe": None
            if detalhe is None
            else {
                "titulo": detalhe.titulo,
                "objeto": detalhe.objeto,
                "situacao_sistema": detalhe.situacao_sistema,
                "publicacao": _iso(detalhe.publicacao),
                "anexos": detalhe.anexos,
            },
        },
    }
