"""Coletor da Compesa: a grade pública do portal SCL (WebRun Studio), lida sem navegador.

Fluxo (nota `docs/superpowers/specs/2026-11-26-fonte-compesa.md`), tudo GET e com o mesmo
cookie jar (a paginação é estado da sessão no servidor): `form.jsp` (shell, dá o cookie) →
`openform.do` → `gridEdit.do?param=refresh` (página 1) → para cada página seguinte,
`navigate.do?param=next` e `gridEdit.do?param=refreshpage`. Nunca POST: o WAF da Compesa
rejeita POST fora de contexto (página "Request Rejected", que é o `marcador_bloqueio`).

A resposta é texto JavaScript, não JSON: `data_817140 = [{...}, ...]`, 100 linhas por página.
O array é lido por um tokenizador próprio (`ler_literal`), sem executar JS. Cada linha tem as
colunas visíveis como strings e um botão `d.c_817140.gridButton('<código JS>', ...)` cujo
código cria campos ocultos (`new HTMLHidden('SCL', 12, <id>, '<valor>')` seguido de
`d.c_<id>.field = '<nome>'`): de lá saem `lc_chave`, Custo Estimado, Município etc.

Armadilhas vistas nas amostras (`tests/dados/fontes/compesa-p*-reduzida.js.txt`):
- locale: o portal formata data e número pelo `Accept-Language` da requisição, não pela página.
  Sem o header (o padrão do httpx) tudo vem en-US (`11/24/2026`, `223078.49`); com
  `Accept-Language: pt-BR` tudo vem pt-BR (`24/11/2026`, `2856395,49`), nas duas páginas, apesar
  de a coluna declarar `dd/MM/yyyy` sempre. Por isso toda requisição manda `IDIOMA` e toda página
  é lida em pt-BR. Uma data impossível em dd/MM (mês 13) é lida como MM/dd e loga
  `data_formato_inconsistente`; mais de 20% delas numa página é erro de layout (o locale mudou),
  em vez de trocar dia e mês em silêncio;
- encoding: o header diz ISO-8859-1, mas o corpo é windows-1252 (o travessão vem como 0x96),
  então ISO-8859-1 é lido como windows-1252 (como fazem os navegadores);
- o portal repete linhas idênticas (mesmo `lc_chave`); a última vence.

Janela: a grade é o histórico inteiro (há aberturas de 2012), em ordem decrescente de
abertura, bem mais de 5 000 linhas. O retrato cobre só os últimos 12 meses: `corte()` é a data de
hoje em Brasília menos 365 dias. A paginação para na primeira página cuja abertura mais nova é
anterior ao corte (log `compesa_corte_por_data`; essa página não é rendida), e `extrair` descarta
as linhas anteriores ao corte que vieram na última página. Uma página seguinte com abertura mais
nova que a da anterior quebra a premissa da ordem e é erro de layout. Como a reconciliação só
enxerga o que o retrato lista, um edital da Compesa que saia da janela ainda `aberta` no banco é
fechado por ela, com a situação original "fora da janela" (`rotulo_saida`); é aceitável, porque
um edital com abertura de mais de um ano já não está aberto.

Completude: a resposta não traz o total de registros. Dentro da janela, a coleta segue página a
página enquanto a navegação deixa avançar (`enableNavigation(primeira, anterior, próxima,
última)`) e a página traz algum `lc_chave` novo; uma página cujo `setGridPageIni` não é o fim da
anterior (sessão perdida devolvendo a página 1, por exemplo) é erro, para não truncar a lista em
silêncio. `MAX_PAGINAS` é a última trava contra paginação sem fim.
"""

import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo

from radar.coleta.retratos import ClienteEducado, ErroFonte, PaginaColetada

logger = logging.getLogger(__name__)

BASE = "https://portalscl.compesa.com.br:8743/webrunstudio"
GRADE = "817140"  # componente da grade principal ("TMAKERGRID")
URL_FORM = f"{BASE}/form.jsp?sys=SCL&action=openform&formID=7"
URL_OPENFORM = f"{BASE}/openform.do?sys=SCL&action=openform&formID=7"
URL_GRADE = (
    f"{BASE}/gridEdit.do?sys=SCL&action=gridEdit&param=refresh&formID=7&comID={GRADE}"
    "&gridComID=-1&gt=-1&WFRInput817141="
)
URL_PROXIMA = (
    f"{BASE}/navigate.do?sys=SCL&formID=7&componentID={GRADE}&action=navigate&param=next"
    "&inner=true&gt=-1"
)
URL_PAGINA = (
    f"{BASE}/gridEdit.do?sys=SCL&action=gridEdit&param=refreshpage&formID=7&comID={GRADE}"
    "&gridComID=-1&gt=-1"
)
MAX_PAGINAS = 100  # última trava; a janela de 12 meses cabe em poucas páginas
JANELA_DIAS = 365
# A grade não traz o CNPJ; é o da sede (Recife), o mesmo que o PNCP usaria para a Compesa.
CNPJ = "09769035000164"
FUSO = ZoneInfo("America/Sao_Paulo")

# Colunas visíveis da grade (chaves das linhas de `data_817140`).
_COL_BOTAO = "field817094"  # ANEXOS: o gridButton com os campos ocultos
_COL_NUMERO = "field817074"
_COL_OBJETO = "field817075"
_COL_ABERTURA = "field817072"
_COL_HORA = "field817073"
_COL_SITUACAO = "field817232"

# Sem isto o portal responde em en-US (MM/dd/yyyy e ponto decimal).
IDIOMA = {"Accept-Language": "pt-BR,pt;q=0.9"}

_SITUACAO: dict[str, str] = {
    "abertura": "aberta",
    "habilitação": "em_julgamento",
    "homologação": "encerrada",
    "contrato": "encerrada",
    "fracassada": "encerrada",
    "deserta": "encerrada",
    "revogada": "encerrada",
    "anulada": "encerrada",  # vista na p2 (LC 070/2026 CSL): cancelada é encerrada
    "suspensa": "outra",
}

# Prefixo do número da licitação → nome legível. Só o que tem fonte:
# - LC: o aviso publicado "LICITAÇÃO.COMPESA Nº 207/2026 CPL" (alertalicitacao.com.br,
#   CEPE-PE-0-382922) é o "LC 207/2026 CPL" da grade (amostra p1 completa de 26/11/2026);
# - ECR: o próprio objeto da linha "ECR 001/2026 CEL-1" diz "Ref: ECR 001/2026 - CREDENCIAMENTO"
#   (amostra p2).
# MI ("MI 001/2026 CEL-2", consultoria financiada pela AFD) não foi confirmado: cai no fallback
# `Compesa (MI)` com log `modalidade_desconhecida`.
MODALIDADES_COMPESA: dict[str, str] = {"LC": "Licitação", "ECR": "Credenciamento"}
FRACAO_DATAS_IMPOSSIVEIS = 0.2  # acima disso, a página não veio em dd/MM

# --- literal JavaScript ----------------------------------------------------------------------

_ESPACO = re.compile(r"\s*")
_NUMERO = re.compile(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_NOME = re.compile(r"[A-Za-z_$][\w$]*")
_TRECHO = {"'": re.compile(r"[^'\\\n]*"), '"': re.compile(r'[^"\\\n]*')}
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_CONSTANTES: dict[str, Any] = {"true": True, "false": False, "null": None, "undefined": None}

TipoToken = Literal["str", "num", "nome", "sim", "fim"]


@dataclass(frozen=True)
class Chamada:
    """Uma expressão que não é literal: `a.b(args)`, `new X(args)` ou só `a.b` (args None)."""

    nome: str
    args: list[Any] | None = None


@dataclass(frozen=True)
class _Token:
    tipo: TipoToken
    valor: Any
    fim: int


def _ler_string(texto: str, i: int) -> tuple[str, int]:
    """String JS a partir da aspa em `i`. Escape desconhecido (`\\/`, `\\-`) vale o caractere."""
    aspa = texto[i]
    trecho = _TRECHO[aspa]
    partes: list[str] = []
    i += 1
    while True:
        achado = trecho.match(texto, i)
        assert achado is not None  # o padrão aceita vazio
        partes.append(achado.group())
        i = achado.end()
        if i >= len(texto) or texto[i] == "\n":
            raise ErroFonte("layout: string sem fim no JS")
        if texto[i] == aspa:
            return "".join(partes), i + 1
        escape = texto[i + 1 : i + 2]  # texto[i] é a barra
        i += 2
        if escape in ("x", "u"):
            tamanho = 2 if escape == "x" else 4
            codigo = texto[i : i + tamanho]
            try:
                partes.append(chr(int(codigo, 16)))
            except ValueError as erro:
                raise ErroFonte(f"layout: escape \\{escape}{codigo} inválido no JS") from erro
            i += tamanho
        elif escape == "\n":
            pass  # continuação de linha
        elif escape == "":
            raise ErroFonte("layout: string sem fim no JS")
        else:
            partes.append(_ESCAPES.get(escape, escape))


def _token(texto: str, i: int) -> tuple[_Token, int]:
    """O próximo token a partir de `i`; devolve também onde ele começa."""
    espaco = _ESPACO.match(texto, i)
    assert espaco is not None
    i = espaco.end()
    if i >= len(texto):
        return _Token("fim", None, i), i
    c = texto[i]
    if c in "'\"":
        valor, fim = _ler_string(texto, i)
        return _Token("str", valor, fim), i
    numero = _NUMERO.match(texto, i)
    if numero:
        bruto = numero.group()
        valor_num: int | float = float(bruto) if any(x in bruto for x in ".eE") else int(bruto)
        return _Token("num", valor_num, numero.end()), i
    nome = _NOME.match(texto, i)
    if nome:
        return _Token("nome", nome.group(), nome.end()), i
    return _Token("sim", c, i + 1), i


def tokens(texto: str) -> Iterator[_Token]:
    i = 0
    while True:
        token, _ = _token(texto, i)
        if token.tipo == "fim":
            return
        yield token
        i = token.fim


class _Leitor:
    """Parser de um valor JS (literais, arrays, objetos e chamadas) com um token de espia."""

    def __init__(self, texto: str, pos: int) -> None:
        self._texto = texto
        self._pos = pos
        self.fim = pos  # fim do último token consumido

    def espiar(self) -> _Token:
        return _token(self._texto, self._pos)[0]

    def tomar(self) -> _Token:
        token = self.espiar()
        if token.tipo == "fim":
            raise ErroFonte("layout: JS terminou no meio de um valor")
        self._pos = self.fim = token.fim
        return token

    def esperar(self, simbolo: str) -> None:
        token = self.tomar()
        if (token.tipo, token.valor) != ("sim", simbolo):
            raise ErroFonte(f"layout: esperava {simbolo!r} no JS, veio {token.valor!r}")

    def _e(self, simbolo: str) -> bool:
        token = self.espiar()
        return token.tipo == "sim" and token.valor == simbolo

    def _lista_ate(self, fecha: str) -> list[Any]:
        itens: list[Any] = []
        while not self._e(fecha):
            itens.append(self.valor())
            if not self._e(fecha):
                self.esperar(",")
        self.esperar(fecha)
        return itens

    def valor(self) -> Any:
        token = self.tomar()
        if token.tipo in ("str", "num"):
            return token.valor
        if token.tipo == "sim":
            if token.valor == "-" and self.espiar().tipo == "num":
                return -self.tomar().valor
            if token.valor == "[":
                return self._lista_ate("]")
            if token.valor == "{":
                return self._objeto()
            raise ErroFonte(f"layout: símbolo {token.valor!r} inesperado no JS")
        if token.valor in _CONSTANTES:
            return _CONSTANTES[token.valor]
        return self._expressao(token.valor)

    def _objeto(self) -> dict[str, Any]:
        objeto: dict[str, Any] = {}
        while not self._e("}"):
            chave = self.tomar()
            if chave.tipo not in ("str", "nome", "num"):
                raise ErroFonte(f"layout: chave {chave.valor!r} inválida no JS")
            self.esperar(":")
            objeto[str(chave.valor)] = self.valor()
            if not self._e("}"):
                self.esperar(",")
        self.esperar("}")
        return objeto

    def _expressao(self, primeiro: str) -> Chamada:
        nome = primeiro
        if nome == "new":
            alvo = self.tomar()
            if alvo.tipo != "nome":
                raise ErroFonte("layout: `new` sem nome no JS")
            nome = f"new {alvo.valor}"
        while self._e("."):
            self.tomar()
            parte = self.tomar()
            if parte.tipo != "nome":
                raise ErroFonte("layout: `.` sem nome no JS")
            nome = f"{nome}.{parte.valor}"
        if self._e("("):
            self.tomar()
            return Chamada(nome, self._lista_ate(")"))
        return Chamada(nome)


def ler_literal(texto: str, pos: int = 0) -> tuple[Any, int]:
    """Lê um valor JS a partir de `pos` sem executar nada; devolve o valor e onde ele acaba."""
    leitor = _Leitor(texto, pos)
    return leitor.valor(), leitor.fim


# --- campos ----------------------------------------------------------------------------------


def decodificar(conteudo: bytes, charset: str | None) -> str:
    """Corpo pelo charset do header. ISO-8859-1 é lido como windows-1252 (o que o servidor
    manda de fato); sem header, UTF-8 e, se não for, windows-1252."""
    nome = (charset or "").strip().lower()
    if nome in ("", "iso-8859-1", "iso8859-1", "latin-1", "latin1", "windows-1252", "cp1252"):
        if not nome:
            try:
                return conteudo.decode("utf-8")
            except UnicodeDecodeError:
                pass
        try:
            return conteudo.decode("cp1252")
        except UnicodeDecodeError:  # byte sem par no windows-1252 (0x81, 0x8d...)
            return conteudo.decode("latin-1")
    try:
        return conteudo.decode(nome, errors="replace")
    except LookupError:
        return conteudo.decode("utf-8", errors="replace")


_DATA = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")
_HORA = re.compile(r"(\d{1,2}):(\d{2})")


def data_impossivel(data: str | None) -> bool:
    """A data existe no texto mas não é válida em dd/MM/yyyy (ex.: `11/24/2026`)."""
    achado = _DATA.search(data or "")
    if not achado:
        return False
    try:
        datetime(int(achado.group(3)), int(achado.group(2)), int(achado.group(1)))
    except ValueError:
        return True
    return False


def parse_data(data: str | None, hora: str | None) -> datetime | None:
    """Data da coluna ABERTURA (dd/MM/yyyy) mais a HORA."""
    achado = _DATA.search(data or "")
    if not achado:
        if (data or "").strip():
            logger.warning("data_nao_reconhecida", extra={"texto": data})
        return None
    dia, mes, ano = int(achado.group(1)), int(achado.group(2)), int(achado.group(3))
    relogio = _HORA.search(hora or "")
    if relogio:
        hh, mm = int(relogio.group(1)), int(relogio.group(2))
    elif achado.group(4):
        hh, mm = int(achado.group(4)), int(achado.group(5))
    else:
        logger.debug("data_sem_hora", extra={"texto": data})  # comum: linha sem HORA
        hh = mm = 0
    try:
        return datetime(ano, mes, dia, hh, mm)
    except ValueError:
        pass
    try:
        trocada = datetime(ano, dia, mes, hh, mm)
    except ValueError:
        logger.warning("data_invalida", extra={"texto": data, "hora": hora})
        return None
    logger.warning("data_formato_inconsistente", extra={"texto": data})
    return trocada


def parse_valor(texto: str | None) -> Decimal | None:
    """Número em pt-BR (1.234,56). Vazio: None."""
    limpo = (texto or "").strip()
    if not limpo:
        return None
    try:
        return Decimal(limpo.replace(".", "").replace(",", "."))
    except InvalidOperation:
        logger.warning("valor_invalido", extra={"texto": limpo})
        return None


def _casa(toks: list[_Token], k: int, padrao: list[tuple[TipoToken, Any]]) -> bool:
    if k + len(padrao) > len(toks):
        return False
    for token, (tipo, valor) in zip(toks[k:], padrao, strict=False):
        if token.tipo != tipo or (valor is not None and token.valor != valor):
            return False
    return True


_OCULTO: list[tuple[TipoToken, Any]] = [
    ("nome", "HTMLHidden"),
    ("sim", "("),
    ("str", None),
    ("sim", ","),
    ("num", None),
    ("sim", ","),
    ("num", None),  # id do componente
    ("sim", ","),
    ("str", None),  # valor
    ("sim", ")"),
]


def campos_ocultos(codigo: str) -> dict[str, str]:
    """`{nome: valor}` dos `HTMLHidden` criados pelo código JS do botão da linha."""
    toks = list(tokens(codigo))
    valores: dict[str, str] = {}
    campos: dict[str, str] = {}
    for k, token in enumerate(toks):
        if token.tipo != "nome":
            continue
        if _casa(toks, k, _OCULTO):
            valores[f"c_{toks[k + 6].valor}"] = toks[k + 8].valor
        elif token.valor in valores and _casa(
            toks, k, [("nome", None), ("sim", "."), ("nome", "field"), ("sim", "="), ("str", None)]
        ):
            campos[toks[k + 4].valor] = valores[token.valor]
    return campos


@dataclass(frozen=True)
class LinhaCompesa:
    chave: str  # lc_chave, "2026LC213CEL-1"
    numero: str  # "LC 213/2026 CEL-1"
    objeto: str
    data_texto: str
    hora: str
    abertura: datetime | None
    situacao: str
    custo: Decimal | None
    municipio: str | None
    campos: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Grade:
    inicio: int | None  # setGridPageIni
    fim: int | None  # setGridPageEnd
    tem_proxima: bool
    linhas: list[LinhaCompesa] = field(default_factory=list)


_MARCADOR = re.compile(rf"\bdata_{GRADE}\s*=\s*(?=\[)")
_INICIO = re.compile(rf"\.c_{GRADE}\.setGridPageIni\((\d+)\)")
_FIM = re.compile(rf"\.c_{GRADE}\.setGridPageEnd\((\d+)\)")
_NAVEGACAO = re.compile(rf"\.c_{GRADE}\.enableNavigation\((\w+),\s*(\w+),\s*(\w+),\s*(\w+)\)")


def _texto(linha: dict[str, Any], coluna: str) -> str:
    valor = linha.get(coluna)
    if not isinstance(valor, str):
        raise ErroFonte(f"layout: linha sem a coluna {coluna}")
    return " ".join(valor.split())


def _linha(bruta: Any) -> LinhaCompesa:
    if not isinstance(bruta, dict):
        raise ErroFonte("layout: linha da grade não é objeto")
    botao = bruta.get(_COL_BOTAO)
    if not (isinstance(botao, Chamada) and botao.args and isinstance(botao.args[0], str)):
        raise ErroFonte("layout: linha sem o botão com os campos ocultos")
    campos = campos_ocultos(botao.args[0])
    chave = campos.get("lc_chave", "").strip()
    if not chave:
        raise ErroFonte("layout: linha sem lc_chave")
    data_texto, hora = _texto(bruta, _COL_ABERTURA), _texto(bruta, _COL_HORA)
    municipio = " ".join(campos.get("Município", "").split())
    return LinhaCompesa(
        chave=chave,
        numero=_texto(bruta, _COL_NUMERO),
        objeto=_texto(bruta, _COL_OBJETO),
        data_texto=data_texto,
        hora=hora,
        abertura=parse_data(data_texto, hora),
        situacao=_texto(bruta, _COL_SITUACAO),
        custo=parse_valor(campos.get("Custo Estimado")),
        municipio=municipio or None,
        campos=campos,
    )


def ler_grade(texto: str) -> Grade:
    marcador = _MARCADOR.search(texto)
    if marcador is None:
        raise ErroFonte(f"layout: resposta sem data_{GRADE}")
    linhas, _ = ler_literal(texto, marcador.end())
    if not isinstance(linhas, list):
        raise ErroFonte(f"layout: data_{GRADE} não é array")
    inicio, fim = _INICIO.search(texto), _FIM.search(texto)
    navegacao = _NAVEGACAO.findall(texto)
    # Sem a chamada de navegação não dá para saber: segue, e o fim vem pela falta de chave nova.
    tem_proxima = navegacao[-1][2] == "true" if navegacao else True
    # Uma data impossível em dd/MM solta é lida como MM/dd (com log); muitas são sinal de que a
    # página veio em outro locale, e trocar dia e mês em silêncio daria datas erradas.
    datas = [bruta.get(_COL_ABERTURA) for bruta in linhas if isinstance(bruta, dict)]
    impossiveis = sum(data_impossivel(d) for d in datas if isinstance(d, str))
    if datas and impossiveis > FRACAO_DATAS_IMPOSSIVEIS * len(datas):
        raise ErroFonte(
            f"layout: estilo de data inconsistente ({impossiveis} de {len(datas)} "
            "impossíveis em dd/MM)"
        )
    return Grade(
        inicio=int(inicio.group(1)) if inicio else None,
        fim=int(fim.group(1)) if fim else None,
        tem_proxima=tem_proxima,
        linhas=[_linha(bruta) for bruta in linhas],
    )


def _agora_real() -> datetime:
    return datetime.now(UTC)


def _mais_nova(grade: Grade) -> datetime | None:
    datas = [linha.abertura for linha in grade.linhas if linha.abertura is not None]
    return max(datas) if datas else None


def _antiga(linha: LinhaCompesa, corte: date) -> bool:
    return linha.abertura is not None and linha.abertura.date() < corte


class FonteCompesa:
    """Licitações da Compesa (PE), em retrato: a grade pública dos últimos 12 meses."""

    nome = "compesa"
    # A página 1 sozinha tem 98 lc_chave distintos e a lista passa de 200 linhas (amostras de
    # 26/11/2026): 50 pega seletor quebrado e grade truncada sem disparar à toa.
    minimo_registros = 50
    # A página de bloqueio do WAF vista no estudo (POST rejeitado).
    marcador_bloqueio: str | None = "<title>Request Rejected</title>"
    rotulo_saida = "fora da janela"

    def __init__(self, relogio: Callable[[], datetime] | None = None) -> None:
        self._relogio = relogio or _agora_real
        # Grades já lidas em `coletar`, para `extrair` não ler (e logar) cada página de novo.
        self._grades: dict[tuple[str, int, int], Grade] = {}

    def _ler_grade(self, url: str, texto: str) -> Grade:
        chave = (url, len(texto), hash(texto))
        grade = self._grades.get(chave)
        if grade is None:
            grade = self._grades[chave] = ler_grade(texto)
        return grade

    def corte(self) -> date:
        """Primeiro dia da janela: hoje em Brasília menos `JANELA_DIAS`."""
        return self._relogio().astimezone(FUSO).date() - timedelta(days=JANELA_DIAS)

    def coletar(self, cliente: ClienteEducado) -> Iterator[PaginaColetada]:
        corte = self.corte()
        self._grades = {}
        anterior: datetime | None = None  # abertura mais nova da página anterior
        cliente.get(URL_FORM, headers=IDIOMA)  # cookie de sessão (JSESSIONID e os do WAF)
        cliente.get(URL_OPENFORM, headers=IDIOMA)
        vistos: set[str] = set()
        esperado: int | None = None
        for pagina in range(MAX_PAGINAS):
            if pagina:
                cliente.get(URL_PROXIMA, headers=IDIOMA)
            url = URL_PAGINA if pagina else URL_GRADE
            resposta = cliente.get(url, headers=IDIOMA)
            texto = decodificar(resposta.content, resposta.charset_encoding)
            grade = self._ler_grade(url, texto)
            if esperado is not None and grade.inicio is not None and grade.inicio != esperado:
                raise ErroFonte(
                    f"layout: página fora de ordem: início {grade.inicio}, esperado {esperado}"
                )
            mais_nova = _mais_nova(grade)
            if mais_nova is not None:
                if anterior is not None and mais_nova > anterior:
                    raise ErroFonte("layout: lista fora de ordem por data")
                if mais_nova.date() < corte:
                    logger.info(
                        "compesa_corte_por_data",
                        extra={"pagina": pagina + 1, "corte": corte.isoformat()},
                    )
                    return
                anterior = mais_nova
            chaves = {linha.chave for linha in grade.linhas}
            if pagina and not chaves - vistos:
                logger.info("compesa_fim_sem_chave_nova", extra={"pagina": pagina + 1})
                return
            vistos |= chaves
            yield PaginaColetada(url, "js", texto)
            if not grade.tem_proxima:
                return
            esperado = grade.fim
        raise ErroFonte(f"lista sem fim: mais de {MAX_PAGINAS} páginas")

    def extrair(self, paginas: list[PaginaColetada]) -> list[dict[str, Any]]:
        corte = self.corte()
        linhas: dict[str, LinhaCompesa] = {}  # a última linha com a chave vence
        for pagina in paginas:
            for linha in self._ler_grade(pagina.url, pagina.texto).linhas:
                if not _antiga(linha, corte):  # a janela é exata: fora dela não entra
                    linhas[linha.chave] = linha
        self._grades = {}
        desconhecidas: set[str] = set()
        modalidades_vistas: set[str] = set()  # um log por código desconhecido por retrato
        normalizados = []
        for linha in linhas.values():
            situacao = _SITUACAO.get(linha.situacao.casefold())
            if situacao is None:
                situacao = "outra"
                if linha.situacao not in desconhecidas:
                    desconhecidas.add(linha.situacao)
                    logger.warning(
                        "situacao_desconhecida",
                        extra={"fonte": self.nome, "situacao": linha.situacao},
                    )
            codigo = linha.numero.split(" ", 1)[0]
            modalidade = MODALIDADES_COMPESA.get(codigo)
            if modalidade is None:
                modalidade = f"Compesa ({codigo})"
                if codigo not in modalidades_vistas:
                    modalidades_vistas.add(codigo)
                    logger.warning(
                        "modalidade_desconhecida", extra={"fonte": self.nome, "codigo": codigo}
                    )
            normalizados.append(_normalizar(linha, situacao, modalidade))
        return normalizados


def _normalizar(linha: LinhaCompesa, situacao: str, modalidade: str) -> dict[str, Any]:
    return {
        "fonte": "compesa",
        "id_na_fonte": linha.chave,
        "objeto": linha.objeto,
        "orgao_nome": "COMPESA",
        "orgao_cnpj": CNPJ,
        "uf": "PE",
        "municipio": linha.municipio,
        "modalidade_nome": modalidade,  # do prefixo do número, por MODALIDADES_COMPESA
        "situacao_original": linha.situacao,
        "situacao_normalizada": situacao,
        "valor_estimado": linha.custo,
        "data_publicacao": None,
        "data_abertura_proposta": linha.abertura,
        "data_encerramento_proposta": linha.abertura,  # prazo = ABERTURA + HORA
        "link_origem": None,  # a grade não tem link por edital: a API usa o de reserva
        "anexos": [],
        "bruto": {
            "numero": linha.numero,
            "objeto": linha.objeto,
            "abertura": linha.data_texto,
            "hora": linha.hora,
            "situacao": linha.situacao,
            "campos": linha.campos,
        },
    }
