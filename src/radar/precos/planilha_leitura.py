"""Leitura determinística da planilha orçamentária de um edital (texto do PDF, por página).

Uma linha da planilha vale quando tem uma unidade conhecida seguida da quantidade e termina
com 2 a 7 números em que `quantidade × unitário ≈ total` (tolerância de 0,3%). É a guarda
aritmética: linha que a própria conta não confirma não é gravada. Dos pares coerentes, o de
maior total é o preço com BDI; um unitário menor coerente (ou logo antes do com BDI, até 60%
abaixo) é o sem BDI; `%` na linha é o BDI dela.

Layouts medidos no spike (2026-11-27, `docs/superpowers/specs/2026-11-27-etapa-07b2-...`):
- código antes do item ("SINAPI-97132 4.4 ASSENTAMENTO ... m 2.142,00 23,02 28,59 61.239,78");
- código e fonte depois da descrição ("... fornec./assent. SINAPI 90701 m 33,00 90,52 ...");
- código, fonte e data-base da linha ("... HM07752 SABESP jul/26 M 2.866,62 284,07 ... 15,28% ...");
- memorial de cálculo entre a quantidade e os preços ("m³ 1487,20 Trecho linear 845m x ...").
Descrição quebrada em várias linhas: junta as linhas desde o último número de item.
"""

import re
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation

from radar.precos.servicos import normalizar_unidade, sem_acento

TOLERANCIA_RELATIVA = Decimal("0.003")
TOLERANCIA_ABSOLUTA = Decimal("0.05")
# Unitário sem BDI logo antes do com BDI: com ÷ sem em (1; 1,6].
RAZAO_BDI_MAX = Decimal("1.6")
MIN_NUMEROS = 2
MAX_NUMEROS = 7
MAX_LINHAS_DESCRICAO = 6

# Unidades vistas nas planilhas (sem acento, sem ponto nem espaço, como `normalizar_unidade`).
UNIDADES = frozenset(
    {
        "m", "m2", "m3", "ml", "mt", "km", "un", "und", "unid", "ud", "u", "pc", "peca", "pca",
        "kg", "t", "ton", "h", "hr", "hh", "mes", "dia", "vb", "verba", "cj", "conj", "cjto",
        "jg", "l", "gl", "pt", "sc", "ha", "lig", "unmes", "un/mes", "unid/mes", "mxmes",
        "tkm", "txkm", "tonxkm", "m3xkm", "m3km", "kmxm3",
    }
)  # fmt: skip
_MESES = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}  # fmt: skip
FONTES = (
    "sinapi", "sabesp", "cdhu", "sicro", "orse", "sbc", "fde", "cpos", "setop", "sudecap",
    "copasa", "siurb", "emop", "dnit", "der", "iopes", "agesul", "seinfra", "sanepar",
    "cotacao", "composicao", "cpu", "propria",
)  # fmt: skip

_NUMERO = re.compile(r"^(?:\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)$")
_ITEM = re.compile(r"^\d{1,3}(?:\.\d{1,3}){1,4}\.?$")
_INTEIRO_ITEM = re.compile(r"^\d{1,3}\.?$")
_SECAO = re.compile(r"^\s*(\d{1,2})(\.0)?\.?\s+(\D.*)$")
_FONTE_TOKEN = re.compile(r"^(" + "|".join(FONTES) + r")(?:[-/:](.*))?$")
_CODIGO = re.compile(
    r"^(?:\d{4,9}[a-z]?(?:/\d{1,3})?|[a-z]{1,3}-?\d{4,8}|\d{2}\.\d{2}\.\d{3}|cpu?-?\d{1,3}|"
    r"\d{2}\.\d{2}\.?\d{3}|cot-?\d{1,3})$"
)
_DATA_TOKEN = re.compile(r"^(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)[a-z]*[-/.](\d{2,4})$")
_DATA_NUM = re.compile(r"^(\d{1,2})/(\d{4})$")
# Página de composição de preço unitário (CPU): insumo por hora não é serviço comparável.
_COMPOSICAO = re.compile(
    r"total da composi|^composic\w*\s+(?:descri|-)|composic\w* (?:de precos? )?unitari"
    r"|composic\w* de precos?|^cpu",
    re.MULTILINE,
)
_TOTAL = re.compile(r"^(?:sub-?\s?total|total)\b")
_CABECALHO_FONTE = re.compile(
    r"(" + "|".join(FONTES[:19]) + r")\W{0,14}?(?:ref\.?\s*)?"
    r"(?:(\d{1,2})/(\d{4})|(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)[a-z]*\W{1,3}(\d{2,4}))"
)


@dataclass(frozen=True)
class LinhaPlanilha:
    """Uma linha de preço da planilha, já conferida pela conta."""

    item: str | None
    codigo: str | None
    fonte: str | None
    descricao: str
    unidade: str
    quantidade: Decimal
    preco_sem_bdi: Decimal | None
    preco_com_bdi: Decimal
    total: Decimal
    # Fração (0,2484); None quando a linha não diz.
    bdi: Decimal | None
    data_base: date | None
    # Título da seção da planilha em que a linha está ("EXECUÇÃO DE COLETOR DE ESGOTOS").
    secao: str | None
    pagina: int


def numero_br(texto: str) -> Decimal | None:
    """ "2.142,00" → 2142.00; "1487,20" → 1487.20; "6" → 6. Outro formato → None."""
    limpo = texto.strip().removeprefix("R$").strip()
    if not _NUMERO.match(limpo):
        return None
    try:
        return Decimal(limpo.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def coerente(quantidade: Decimal, unitario: Decimal, total: Decimal) -> bool:
    """`quantidade × unitário ≈ total` (0,3% ou 5 centavos). Valores não positivos → False."""
    if quantidade <= 0 or unitario <= 0 or total <= 0:
        return False
    tolerancia = max(TOLERANCIA_ABSOLUTA, total * TOLERANCIA_RELATIVA)
    return abs(quantidade * unitario - total) <= tolerancia


def _ano(texto: str) -> int:
    ano = int(texto)
    return ano + 2000 if ano < 100 else ano


def data_de_token(token: str) -> date | None:
    """ "ago/26", "jul-26", "07/2026" → dia 1 do mês."""
    limpo = sem_acento(token).strip(" ;,.()")
    if m := _DATA_TOKEN.match(limpo):
        return date(_ano(m.group(2)), _MESES[m.group(1)], 1)
    if (m := _DATA_NUM.match(limpo)) and 1 <= int(m.group(1)) <= 12:
        return date(int(m.group(2)), int(m.group(1)), 1)
    return None


def datas_do_cabecalho(texto: str) -> dict[str, date]:
    """Data-base por fonte ("SINAPI - 07/2026", "SINAPI ref. ago/26", "SBC (08/2026)")."""
    datas: dict[str, date] = {}
    for m in _CABECALHO_FONTE.finditer(sem_acento(texto)):
        fonte = m.group(1)
        if m.group(2):
            mes, ano = int(m.group(2)), int(m.group(3))
        else:
            mes, ano = _MESES[m.group(4)], _ano(m.group(5))
        if 1 <= mes <= 12 and 2000 <= ano <= 2100:
            datas.setdefault(fonte, date(ano, mes, 1))
    return datas


def _eh_numero(token: str) -> bool:
    return numero_br(token.removesuffix("%")) is not None


def _cauda(tokens: list[str]) -> int:
    """Índice do primeiro token da cauda de números (com `%` e `-` no meio)."""
    inicio = len(tokens)
    while inicio > 0 and (_eh_numero(tokens[inicio - 1]) or tokens[inicio - 1] == "-"):
        inicio -= 1
    return inicio


def _unidade_antes(tokens: list[str], fim: int) -> int | None:
    """Início da unidade que termina logo antes de `tokens[fim]` (até 3 tokens: "M3 X KM")."""
    for largura in (3, 2, 1):
        inicio = fim - largura
        if inicio >= 0 and normalizar_unidade(" ".join(tokens[inicio:fim])) in UNIDADES:
            return inicio
    return None


@dataclass(frozen=True)
class _Precos:
    quantidade: Decimal
    com_bdi: Decimal
    total: Decimal
    sem_bdi: Decimal | None
    bdi: Decimal | None


def _precos(quantidade: Decimal, tokens: list[str]) -> _Precos | None:
    numeros: list[Decimal] = []
    bdi: Decimal | None = None
    for token in tokens:
        if token.endswith("%"):
            valor = numero_br(token[:-1])
            if valor is not None and bdi is None:
                bdi = valor / 100
        elif (valor := numero_br(token)) is not None:
            numeros.append(valor)
    if not MIN_NUMEROS <= len(numeros) <= MAX_NUMEROS:
        return None
    pares = [
        (numeros[k], j)
        for j in range(len(numeros))
        for k in range(j + 1, len(numeros))
        if coerente(quantidade, numeros[j], numeros[k])
    ]
    if not pares:
        return None
    total, j_com = max(pares)
    com = numeros[j_com]
    sem = None
    menores = [numeros[j] for _, j in pares if numeros[j] < com]
    if menores:
        sem = max(menores)
    elif j_com > 0 and numeros[j_com - 1] < com <= numeros[j_com - 1] * RAZAO_BDI_MAX:
        sem = numeros[j_com - 1]
    return _Precos(quantidade, com, total, sem, bdi)


def _fonte_e_codigo(tokens: list[str]) -> tuple[str | None, str | None, set[int]]:
    """Fonte, código e os índices usados (para tirá-los da descrição)."""
    normalizados = [sem_acento(t).strip(" ;,()") for t in tokens]
    for i, token in enumerate(normalizados):
        m = _FONTE_TOKEN.match(token)
        if m is None:
            continue
        fonte = m.group(1)
        usados = {i}
        if (m.group(2) or "").strip():
            return fonte, tokens[i][len(fonte) + 1 :].strip(" ;,()"), usados
        for vizinho in (i + 1, i - 1):
            if 0 <= vizinho < len(tokens) and _CODIGO.match(normalizados[vizinho]):
                usados.add(vizinho)
                return fonte, tokens[vizinho].strip(" ;,()"), usados
        return fonte, None, usados
    return None, None, set()


def _limpar_descricao(tokens: list[str]) -> tuple[str | None, list[str]]:
    """Tira o código prefixado ("SINAPI-93568"), "Cotação" e o número do item do começo."""
    resto = list(tokens)
    item = None
    for _ in range(3):
        if not resto:
            break
        primeiro = sem_acento(resto[0])
        if item is None and (_ITEM.match(resto[0]) or _INTEIRO_ITEM.match(resto[0])):
            item = resto.pop(0).rstrip(".")
        elif _FONTE_TOKEN.match(primeiro.strip(" ;,()")):
            resto.pop(0)
        else:
            break
    return item, resto


def _tirar_fim(tokens: list[str]) -> list[str]:
    """Tira do fim da descrição o que é fonte, código ou data ("SINAPI 90701", "jul/26")."""
    resto = list(tokens)
    while resto:
        ultimo = sem_acento(resto[-1]).strip(" ;,()")
        if _FONTE_TOKEN.match(ultimo) or _CODIGO.match(ultimo) or data_de_token(resto[-1]):
            resto.pop()
        else:
            break
    return resto


def _comeca_item(linha: str) -> bool:
    tokens = linha.split()
    for token in tokens[:3]:
        if _ITEM.match(token):
            return True
        if not _FONTE_TOKEN.match(sem_acento(token).strip(" ;,()")):
            return False
    return False


@dataclass(frozen=True)
class _Bruta:
    item: str | None
    codigo: str | None
    fonte: str | None
    descricao: str
    unidade: str
    precos: _Precos
    data_linha: date | None


def _ler_linha(linha: str) -> tuple[_Bruta, list[str]] | None:
    """Linha de preço, ou None. Devolve também os tokens da descrição (antes da unidade)."""
    tokens = linha.replace("R$", " ").split()
    cauda = _cauda(tokens)
    if len(tokens) - cauda < MIN_NUMEROS:
        return None
    # A quantidade vem logo depois da unidade; da direita para a esquerda, o primeiro par
    # (unidade, quantidade) cujos números fecham a conta.
    for depois in range(min(cauda, len(tokens) - 2), 0, -1):
        quantidade = numero_br(tokens[depois])
        if quantidade is None or quantidade <= 0:
            continue
        i = _unidade_antes(tokens, depois)
        if i is None:
            continue
        bruta = " ".join(tokens[i:depois])
        numeros = tokens[max(depois + 1, cauda) :]
        precos = _precos(quantidade, numeros)
        if precos is None:
            continue
        meio = tokens[depois + 1 : max(depois + 1, cauda)]
        antes = tokens[:i]
        fonte, codigo, usados = _fonte_e_codigo(antes)
        if fonte is None:
            fonte, codigo, _ = _fonte_e_codigo(meio)
        data_linha = next((d for t in [*antes[-4:], *meio] if (d := data_de_token(t))), None)
        item, descricao = _limpar_descricao([t for j, t in enumerate(antes) if j not in usados])
        texto = " ".join(_tirar_fim(descricao))
        return _Bruta(item, codigo, fonte, texto, bruta, precos, data_linha), antes
    return None


def _titulo_de_secao(linha: str) -> str | None:
    """ "4.0 EXECUÇÃO ADUTORA 2.578.505,98" → "EXECUÇÃO ADUTORA"; "2.0 Coletor" → "Coletor"."""
    m = _SECAO.match(linha)
    if m is None:
        return None
    tokens = m.group(3).split()
    while tokens and _eh_numero(tokens[-1]):
        tokens.pop()
    titulo = " ".join(tokens).strip()
    return titulo if len(titulo) >= 4 and any(c.isalpha() for c in titulo) else None


def pagina_de_composicao(texto: str) -> bool:
    """Página de composições de preço unitário ("TOTAL DA COMPOSIÇÃO", "COMPOSIÇÕES - ...")."""
    return _COMPOSICAO.search(sem_acento(texto)) is not None


def ler_planilha(paginas: list[str]) -> list[LinhaPlanilha]:
    """Linhas de preço coerentes, na ordem do texto; cópias repetidas no documento contam uma.

    Páginas de composição de preço unitário ficam de fora (`pagina_de_composicao`).
    """
    datas = datas_do_cabecalho("\n".join(paginas))
    linhas: list[LinhaPlanilha] = []
    vistas: set[tuple[object, ...]] = set()
    secao: str | None = None
    for numero, pagina in enumerate(paginas, start=1):
        if pagina_de_composicao(pagina):
            continue
        anteriores: list[str] = []
        for crua in pagina.splitlines():
            linha = crua.strip()
            if not linha:
                continue
            lida = _ler_linha(linha)
            if lida is None:
                if _TOTAL.match(sem_acento(linha)):
                    anteriores = []
                    continue
                if (titulo := _titulo_de_secao(linha)) is not None:
                    secao = titulo
                    anteriores = []
                    continue
                anteriores = [*anteriores, linha][-MAX_LINHAS_DESCRICAO:]
                continue
            bruta, antes = lida
            if not _comeca_item(" ".join(antes)) and anteriores:
                inicio = max(
                    (i for i, a in enumerate(anteriores) if _comeca_item(a)),
                    default=len(anteriores) - 1,
                )
                juntas = " ".join([*anteriores[inicio:], bruta.descricao])
                item, tokens = _limpar_descricao(juntas.split())
                _, _, usados = _fonte_e_codigo(tokens[:2])
                tokens = [t for j, t in enumerate(tokens) if j not in usados]
                bruta = replace(
                    bruta, item=bruta.item or item, descricao=" ".join(_tirar_fim(tokens))
                )
            anteriores = []
            if len(bruta.descricao) < 3:
                continue
            data_base = bruta.data_linha or (datas.get(bruta.fonte) if bruta.fonte else None)
            nova = LinhaPlanilha(
                item=bruta.item,
                codigo=bruta.codigo,
                fonte=bruta.fonte,
                descricao=bruta.descricao,
                unidade=bruta.unidade,
                quantidade=bruta.precos.quantidade,
                preco_sem_bdi=bruta.precos.sem_bdi,
                preco_com_bdi=bruta.precos.com_bdi,
                total=bruta.precos.total,
                bdi=bruta.precos.bdi,
                data_base=data_base,
                secao=secao,
                pagina=numero,
            )
            chave = (
                nova.item,
                sem_acento(nova.descricao),
                normalizar_unidade(nova.unidade),
                nova.quantidade,
                nova.preco_com_bdi,
            )
            if chave in vistas:
                continue
            vistas.add(chave)
            linhas.append(nova)
    return linhas


# --- Linhas vindas da IA (texto picotado): a conta e o texto são a ancoragem ---

TOLERANCIA_TOTAL_PLANILHA = Decimal("0.02")
_SEM_ESPACO = re.compile(r"\s+")


def _decimal_ia(valor: object) -> Decimal | None:
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        numero = Decimal(str(valor))
    else:
        texto = str(valor).strip()
        numero_lido = numero_br(texto)
        if numero_lido is None:
            try:
                numero_lido = Decimal(texto)
            except InvalidOperation:
                return None
        numero = numero_lido
    return numero if numero.is_finite() and numero > 0 else None


def formato_br(valor: Decimal) -> str:
    """2142.5 → "2.142,50" (duas casas)."""
    inteiro, _, decimais = f"{valor.quantize(Decimal('0.01')):f}".partition(".")
    grupos = f"{int(inteiro):,}".replace(",", ".")
    return f"{grupos},{decimais or '00'}"


def _no_texto(valor: Decimal, texto_sem_espaco: str) -> bool:
    return formato_br(valor) in texto_sem_espaco


def linhas_da_ia(
    brutas: list[dict[str, object]], texto: str, total_planilha: object = None
) -> list[LinhaPlanilha]:
    """Linhas que a IA devolveu e que passam na guarda; vazia se a soma não bate com o total.

    Cada linha precisa de quantidade, preço com BDI e total com `quantidade × preço ≈ total`,
    e o preço e o total precisam estar no texto (sem espaços: o texto vem picotado, "1.08\n8,69").
    Com `total_planilha` presente no texto, a soma dos totais das linhas válidas tem de ficar
    a ±2% dele; senão nenhuma linha vale (a IA pulou ou inventou linhas).
    """
    corrido = _SEM_ESPACO.sub("", texto).replace("R$", "")
    validas: list[LinhaPlanilha] = []
    for bruta in brutas:
        descricao = str(bruta.get("descricao") or "").strip()
        unidade = str(bruta.get("unidade") or "").strip()
        quantidade = _decimal_ia(bruta.get("quantidade"))
        com = _decimal_ia(bruta.get("preco_com_bdi"))
        sem = _decimal_ia(bruta.get("preco_sem_bdi"))
        total = _decimal_ia(bruta.get("total"))
        if com is None and sem is not None and total is not None and quantidade is not None:
            if coerente(quantidade, sem, total):
                com, sem = sem, None
        if (
            len(descricao) < 3
            or not unidade
            or quantidade is None
            or com is None
            or total is None
            or not coerente(quantidade, com, total)
            or not (_no_texto(com, corrido) and _no_texto(total, corrido))
        ):
            continue
        if sem is not None and not sem < com:
            sem = None
        item = str(bruta.get("item") or "").strip() or None
        codigo = str(bruta.get("codigo") or "").strip() or None
        fonte_bruta = sem_acento(str(bruta.get("fonte") or "")).strip()
        fonte = next((f for f in FONTES if fonte_bruta.startswith(f)), None)
        validas.append(
            LinhaPlanilha(
                item=item,
                codigo=codigo,
                fonte=fonte,
                descricao=descricao,
                unidade=unidade,
                quantidade=quantidade,
                preco_sem_bdi=sem,
                preco_com_bdi=com,
                total=total,
                bdi=None,
                data_base=data_de_token(str(bruta.get("data_base") or "")),
                secao=str(bruta.get("secao") or "").strip() or None,
                pagina=0,  # a IA não diz a página
            )
        )
    declarado = _decimal_ia(total_planilha)
    if declarado is not None and _no_texto(declarado, corrido):
        soma = sum((linha.total for linha in validas), Decimal(0))
        if abs(soma - declarado) > declarado * TOLERANCIA_TOTAL_PLANILHA:
            return []
    return validas
