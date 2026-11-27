"""Do achado à exigência: ancoragem no texto, normalização e junção. Código puro, sem LLM."""

import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from radar.analise.exigencias import (
    DOCUMENTOS,
    Achado,
    Atestado,
    CategoriaEvidencia,
    Exigencias,
    ValorEvidencia,
)

logger = logging.getLogger(__name__)

_MARCADOR = re.compile(r"\[\[pagina (\d+)\]\]")
_ESPACOS = re.compile(r"\s+")


@dataclass(frozen=True)
class AchadoAncorado:
    achado: Achado
    pagina: int


def sem_acento(texto: str) -> str:
    """Minúsculo, sem acento e sem espaços nas pontas."""
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).lower().strip()


def _colapsar(texto: str) -> str:
    return _ESPACOS.sub(" ", texto).strip().casefold()


# Trecho mais curto que isso ancora em qualquer lugar ("de", "R$") e não é lastro de nada.
MINIMO_TRECHO = 20
# Campos em que `numero` é o valor exigido: ele precisa estar escrito no trecho citado.
CAMPOS_NUMERICOS = frozenset(
    {
        "atestado_tecnico",
        "capital_social_minimo",
        "patrimonio_liquido_minimo",
        "garantia_proposta",
        "garantia_contratual",
        "prazo_execucao",
    }
)
_NUMERO_NO_TEXTO = re.compile(r"(\d[\d.,]*)(\s*(?:mil|milh(?:ao|oes)|bilh(?:ao|oes))\b)?")
_MULTIPLICADORES = {"mil": 1000, "milh": 1_000_000, "bilh": 1_000_000_000}
_MILHAR = re.compile(r"^\d{1,3}(\.\d{3})+$")


def _leituras(numeral: str) -> set[Decimal]:
    """Os valores que um numeral do texto pode ter ("250.000" é milhar ou decimal)."""
    bruto = numeral.rstrip(".,")
    valores: set[Decimal] = set()
    if "," in bruto:
        candidatos = [bruto.replace(".", "").replace(",", ".")]
    elif _MILHAR.match(bruto):
        candidatos = [bruto.replace(".", ""), bruto]
    else:
        candidatos = [bruto]
    for candidato in candidatos:
        try:
            valores.add(Decimal(candidato))
        except ArithmeticError:
            continue
    return valores


def numeros_do_texto(texto: str) -> set[Decimal]:
    """Todo número escrito com algarismos no texto: "R$ 250.000,00", "250 mil", "6 (seis)"."""
    achados: set[Decimal] = set()
    for casamento in _NUMERO_NO_TEXTO.finditer(sem_acento(texto)):
        leituras = _leituras(casamento.group(1))
        achados |= leituras
        if casamento.group(2):
            palavra = casamento.group(2).strip()
            fator = next(v for k, v in _MULTIPLICADORES.items() if palavra.startswith(k))
            achados |= {valor * fator for valor in leituras}
    return achados


def _numero_tem_lastro(achado: Achado) -> bool:
    """O número (cru ou já na unidade canônica) aparece escrito no trecho citado."""
    if achado.numero is None or achado.campo not in CAMPOS_NUMERICOS:
        return True
    numero = Decimal(str(achado.numero))
    aceitos = {numero}
    if achado.campo == "atestado_tecnico" and achado.unidade.strip():
        aceitos.add(numero * normalizar_unidade(achado.unidade)[1])
    escritos = numeros_do_texto(achado.trecho)
    return any(a == e for a in aceitos for e in escritos)


def ancorar(
    achados: list[Achado], texto_trecho: str, pagina_inicial: int
) -> tuple[list[AchadoAncorado], int]:
    """Mantém só os achados cujo trecho existe no texto enviado; a página sai da posição.

    A comparação ignora diferenças de espaço e de maiúsculas. Trecho com menos de
    `MINIMO_TRECHO` caracteres é descartado (ancoraria em qualquer lugar). Nos campos
    numéricos, número que não está escrito no trecho vira nulo (a comparação dá `verificar`).
    A página é a do último marcador `[[pagina N]]` antes do trecho (ou `pagina_inicial`),
    nunca a que o modelo disser.
    """
    texto = _colapsar(texto_trecho)
    marcadores = [(m.start(), int(m.group(1))) for m in _MARCADOR.finditer(texto)]
    ancorados: list[AchadoAncorado] = []
    descartados = 0
    for achado in achados:
        trecho = _colapsar(achado.trecho)
        posicao = texto.find(trecho) if len(trecho) >= MINIMO_TRECHO else -1
        if posicao < 0:
            descartados += 1
            motivo = "trecho_curto" if len(trecho) < MINIMO_TRECHO else "trecho_sem_lastro"
            logger.info("achado_descartado", extra={"motivo": motivo, "campo": achado.campo})
            continue
        if not _numero_tem_lastro(achado):
            logger.info("numero_sem_lastro", extra={"campo": achado.campo})
            achado = achado.model_copy(update={"numero": None})
        pagina = pagina_inicial
        for inicio, numero in marcadores:
            if inicio > posicao:
                break
            pagina = numero
        ancorados.append(AchadoAncorado(achado, pagina))
    return ancorados, descartados


_UNIDADES: dict[str, tuple[str, Decimal]] = {
    "m": ("km", Decimal("0.001")),
    "metro": ("km", Decimal("0.001")),
    "metros": ("km", Decimal("0.001")),
    "metros lineares": ("km", Decimal("0.001")),
    "km": ("km", Decimal("1")),
    "quilometro": ("km", Decimal("1")),
    "quilometros": ("km", Decimal("1")),
    "un": ("un", Decimal("1")),
    "und": ("un", Decimal("1")),
    "unidade": ("un", Decimal("1")),
    "unidades": ("un", Decimal("1")),
    "hidrometro": ("un", Decimal("1")),
    "hidrometros": ("un", Decimal("1")),
    "leitura": ("leitura", Decimal("1")),
    "leituras": ("leitura", Decimal("1")),
    "ligacao": ("ligacao", Decimal("1")),
    "ligacoes": ("ligacao", Decimal("1")),
}


def normalizar_unidade(unidade: str) -> tuple[str, Decimal]:
    """Unidade canônica e o fator que converte a quantidade para ela."""
    chave = _ESPACOS.sub(" ", sem_acento(unidade)).rstrip(".")
    return _UNIDADES.get(chave, (chave, Decimal("1")))


def _decimal(numero: float | None) -> Decimal | None:
    return None if numero is None else Decimal(str(numero))


def _texto_numero(numero: Decimal) -> str:
    return f"{numero.normalize():f}"


def _categoria(valor: str) -> str:
    return re.sub(r"[\s-]+", "_", sem_acento(valor))


# Ordem de precedência: o primeiro é o mais restritivo.
_PRECEDENCIA: dict[str, tuple[str, ...]] = {
    "visita_tecnica": ("obrigatoria", "facultativa", "nao_exigida"),
    "consorcio": ("vedado", "permitido"),
    "me_epp": ("exclusiva", "cota", "ampla"),
}


def _prazo_em_dias(numero: Decimal, unidade: str) -> Decimal | None:
    u = sem_acento(unidade)
    if u == "" or u.startswith("dia"):
        return numero
    if u.startswith("mes"):
        return numero * 30
    if u.startswith("ano"):
        return numero * 365
    return None


def _juntar_valores(candidatos: list[ValorEvidencia]) -> ValorEvidencia | None:
    """Fica com o maior número (o mais restritivo); números diferentes marcam conflito."""
    if not candidatos:
        return None
    com_numero = [c for c in candidatos if c.numero is not None]
    if not com_numero:
        return candidatos[0]
    distintos = {c.numero for c in com_numero}
    maior = max(com_numero, key=lambda c: c.numero or Decimal(0))
    return maior.model_copy(update={"conflito": len(distintos) > 1})


def _juntar_categorias(
    campo: str, candidatos: list[CategoriaEvidencia]
) -> CategoriaEvidencia | None:
    if not candidatos:
        return None
    ordem = _PRECEDENCIA[campo]
    vencedor = min(candidatos, key=lambda c: ordem.index(c.valor))
    distintos = {c.valor for c in candidatos}
    return vencedor.model_copy(update={"conflito": len(distintos) > 1})


def _evidencia_monetaria(item: AchadoAncorado, valor_estimado: Decimal | None) -> ValorEvidencia:
    a = item.achado
    numero = _decimal(a.numero)
    percentual = (
        _categoria(a.valor) == "percentual"
        or "%" in a.unidade
        or (sem_acento(a.unidade).startswith("percent"))
    )
    if not percentual or numero is None:
        return ValorEvidencia(numero=numero, trecho=a.trecho, pagina=item.pagina)
    if valor_estimado is None:
        return ValorEvidencia(
            numero=None,
            trecho=a.trecho,
            pagina=item.pagina,
            observacao=f"{_texto_numero(numero)}% do valor estimado, que o edital não informa",
        )
    return ValorEvidencia(
        numero=valor_estimado * numero / 100,
        trecho=a.trecho,
        pagina=item.pagina,
        observacao=f"{_texto_numero(numero)}% do valor estimado",
    )


def juntar(achados: list[AchadoAncorado], valor_estimado: Decimal | None) -> tuple[Exigencias, int]:
    """Junta os achados ancorados de todos os trechos numa `Exigencias`.

    Devolve também quantos foram descartados por valor fora do vocabulário do campo.
    """
    descartados = 0
    monetarios: dict[str, list[ValorEvidencia]] = {
        "capital_social_minimo": [],
        "patrimonio_liquido_minimo": [],
        "garantia_proposta": [],
        "garantia_contratual": [],
        "prazo_execucao": [],
    }
    categorias: dict[str, list[CategoriaEvidencia]] = {c: [] for c in _PRECEDENCIA}
    documentos: dict[str, CategoriaEvidencia] = {}
    atestados: dict[tuple[str, Decimal | None, str | None], Atestado] = {}

    def descartar(item: AchadoAncorado, motivo: str) -> None:
        nonlocal descartados
        descartados += 1
        logger.info("achado_descartado", extra={"motivo": motivo, "campo": item.achado.campo})

    def monetario(item: AchadoAncorado) -> None:
        monetarios[item.achado.campo].append(_evidencia_monetaria(item, valor_estimado))

    def percentual(item: AchadoAncorado) -> None:
        a = item.achado
        monetarios[a.campo].append(
            ValorEvidencia(numero=_decimal(a.numero), trecho=a.trecho, pagina=item.pagina)
        )

    def prazo(item: AchadoAncorado) -> None:
        a = item.achado
        numero = _decimal(a.numero)
        dias = None if numero is None else _prazo_em_dias(numero, a.unidade)
        if numero is not None and dias is None:
            descartar(item, "unidade_desconhecida")
            return
        monetarios["prazo_execucao"].append(
            ValorEvidencia(numero=dias, trecho=a.trecho, pagina=item.pagina)
        )

    def categoria(item: AchadoAncorado) -> None:
        a = item.achado
        valor = _categoria(a.valor)
        if valor not in _PRECEDENCIA[a.campo]:
            descartar(item, "fora_do_vocabulario")
            return
        categorias[a.campo].append(
            CategoriaEvidencia(valor=valor, trecho=a.trecho, pagina=item.pagina)
        )

    def documento(item: AchadoAncorado) -> None:
        a = item.achado
        valor = _categoria(a.valor)
        if valor not in DOCUMENTOS:
            descartar(item, "fora_do_vocabulario")
            return
        documentos.setdefault(
            valor, CategoriaEvidencia(valor=valor, trecho=a.trecho, pagina=item.pagina)
        )

    def atestado(item: AchadoAncorado) -> None:
        a = item.achado
        numero = _decimal(a.numero)
        unidade: str | None = None
        if a.unidade.strip():
            unidade, fator = normalizar_unidade(a.unidade)
            numero = None if numero is None else numero * fator
        chave = (sem_acento(a.valor), numero, unidade)
        atestados.setdefault(
            chave,
            Atestado(
                descricao=a.valor,
                quantidade=numero,
                unidade=unidade,
                trecho=a.trecho,
                pagina=item.pagina,
            ),
        )

    tratadores: dict[str, Callable[[AchadoAncorado], None]] = dict(
        capital_social_minimo=monetario,
        patrimonio_liquido_minimo=monetario,
        garantia_proposta=percentual,
        garantia_contratual=percentual,
        prazo_execucao=prazo,
        visita_tecnica=categoria,
        consorcio=categoria,
        me_epp=categoria,
        documento_habilitacao=documento,
        atestado_tecnico=atestado,
    )
    for item in achados:
        tratadores[item.achado.campo](item)

    exigencias = Exigencias(
        atestados=list(atestados.values()),
        capital_social_minimo=_juntar_valores(monetarios["capital_social_minimo"]),
        patrimonio_liquido_minimo=_juntar_valores(monetarios["patrimonio_liquido_minimo"]),
        garantia_proposta_percentual=_juntar_valores(monetarios["garantia_proposta"]),
        garantia_contratual_percentual=_juntar_valores(monetarios["garantia_contratual"]),
        prazo_execucao_dias=_juntar_valores(monetarios["prazo_execucao"]),
        visita_tecnica=_juntar_categorias("visita_tecnica", categorias["visita_tecnica"]),
        consorcio=_juntar_categorias("consorcio", categorias["consorcio"]),
        me_epp=_juntar_categorias("me_epp", categorias["me_epp"]),
        documentos=list(documentos.values()),
    )
    return exigencias, descartados
