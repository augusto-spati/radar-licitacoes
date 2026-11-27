"""Evals da extração: gabarito feito à mão, precisão e recall por campo, replay sem custo."""

import json
import re
import statistics
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from radar.analise.cache import RespostaAusente
from radar.analise.custos import custo
from radar.analise.exigencias import DOCUMENTOS, Atestado, Exigencias
from radar.analise.extracao import MINIMO_CARACTERES, TextoExtraido, status_do_texto
from radar.analise.extrator import extrair_exigencias
from radar.analise.normalizacao import normalizar_unidade
from radar.analise.provedor import ConfigIA, Pedido, Provedor, Resposta

Conjunto = Literal["ajuste", "teste"]
TOLERANCIA = Decimal("0.01")
ESCALARES = (
    "capital_social_minimo",
    "patrimonio_liquido_minimo",
    "garantia_proposta_percentual",
    "garantia_contratual_percentual",
    "prazo_execucao_dias",
)
CATEGORICOS = ("visita_tecnica", "consorcio", "me_epp")
CAMPOS_EVAL = (*ESCALARES, *CATEGORICOS, "atestados", "documentos")
INSTRUCAO_GRAVAR = "rode `radar avaliar rodar --modo gravar`"


class DocumentoGabarito(BaseModel):
    sequencial: int
    url: str
    sha256: str


class Gabarito(BaseModel):
    edital: str
    documentos: list[DocumentoGabarito]
    conjunto: Conjunto
    conferido: bool
    conferido_por: str | None
    conferido_em: date | None
    observacoes: str
    exigencias: Exigencias


class TextoEval(BaseModel):
    """`evals/textos/<slug>.json`: as páginas extraídas (dado público) e o valor estimado."""

    edital: str
    valor_estimado: Decimal | None = None
    documentos: list[dict[str, object]] = []
    paginas: list[str]


def slug(id_publico: str) -> str:
    return id_publico.replace("~", "_").replace("/", "_")


@dataclass
class Contagem:
    vp: int = 0
    fp: int = 0
    fn: int = 0
    # Casos em que o gabarito não traz o campo (ou o item); alucinação = FP num desses.
    ausentes: int = 0
    alucinacoes: int = 0

    def somar(self, outra: "Contagem") -> None:
        self.vp += outra.vp
        self.fp += outra.fp
        self.fn += outra.fn
        self.ausentes += outra.ausentes
        self.alucinacoes += outra.alucinacoes

    @property
    def precisao(self) -> float | None:
        return None if self.vp + self.fp == 0 else self.vp / (self.vp + self.fp)

    @property
    def recall(self) -> float | None:
        return None if self.vp + self.fn == 0 else self.vp / (self.vp + self.fn)

    @property
    def f1(self) -> float | None:
        total = 2 * self.vp + self.fp + self.fn
        return None if total == 0 else 2 * self.vp / total

    @property
    def taxa_alucinacao(self) -> float | None:
        return None if self.ausentes == 0 else self.alucinacoes / self.ausentes


def _perto(previsto: Decimal | None, esperado: Decimal | None) -> bool:
    if previsto is None or esperado is None:
        return previsto is esperado
    if esperado == 0:
        return previsto == 0
    return abs(previsto - esperado) <= abs(esperado) * TOLERANCIA


def _contar(presente_m: bool, presente_g: bool, iguais: bool) -> Contagem:
    c = Contagem()
    if not presente_g:
        c.ausentes = 1
    if presente_m and presente_g and iguais:
        c.vp = 1
        return c
    if presente_m:
        c.fp = 1
        if not presente_g:
            c.alucinacoes = 1
    if presente_g:
        c.fn = 1
    return c


def _par_atestado(a: Atestado) -> tuple[str | None, Decimal | None]:
    if a.unidade is None:
        return None, a.quantidade
    unidade, fator = normalizar_unidade(a.unidade)
    return unidade, None if a.quantidade is None else a.quantidade * fator


def _contar_atestados(previstos: list[Atestado], esperados: list[Atestado]) -> Contagem:
    c = Contagem(ausentes=0 if esperados else 1)
    restantes = [_par_atestado(a) for a in esperados]
    for atestado in previstos:
        unidade, quantidade = _par_atestado(atestado)
        par = next((p for p in restantes if p[0] == unidade and _perto(quantidade, p[1])), None)
        if par is None:
            c.fp += 1
        else:
            c.vp += 1
            restantes.remove(par)
    c.fn = len(restantes)
    if not esperados and previstos:
        c.alucinacoes = 1
    return c


def comparar_com_gabarito(previsto: Exigencias, gabarito: Exigencias) -> dict[str, Contagem]:
    contagens: dict[str, Contagem] = {}
    for campo in ESCALARES:
        m, g = getattr(previsto, campo), getattr(gabarito, campo)
        iguais = m is not None and g is not None and _perto(m.numero, g.numero)
        contagens[campo] = _contar(m is not None, g is not None, iguais)
    for campo in CATEGORICOS:
        m, g = getattr(previsto, campo), getattr(gabarito, campo)
        iguais = m is not None and g is not None and m.valor == g.valor
        contagens[campo] = _contar(m is not None, g is not None, iguais)
    contagens["atestados"] = _contar_atestados(previsto.atestados, gabarito.atestados)
    m_docs = {d.valor for d in previsto.documentos}
    g_docs = {d.valor for d in gabarito.documentos}
    docs = Contagem(
        vp=len(m_docs & g_docs),
        fp=len(m_docs - g_docs),
        fn=len(g_docs - m_docs),
        ausentes=len(set(DOCUMENTOS) - g_docs),
        alucinacoes=len(m_docs - g_docs),
    )
    contagens["documentos"] = docs
    return contagens


@dataclass
class Relatorio:
    modelo: str
    por_campo: dict[str, dict[str, Contagem]] = field(default_factory=dict)
    nao_conferidos: dict[str, Contagem] = field(default_factory=dict)
    custo_usd: Decimal = Decimal(0)
    latencias_ms: list[int] = field(default_factory=list)
    achados_descartados: int = 0
    trechos_invalidos: int = 0
    ausentes: list[str] = field(default_factory=list)
    editais: dict[str, int] = field(default_factory=dict)


class _Medidor:
    """Mede o que passa pelo provedor e, em replay, junta as respostas ausentes sem parar."""

    def __init__(self, interno: Provedor, config: ConfigIA, coletar_ausentes: bool) -> None:
        self._interno = interno
        self._config = config
        self._coletar = coletar_ausentes
        self.latencias_ms: list[int] = []
        self.custo_usd = Decimal(0)
        self.ausentes: list[str] = []

    def gerar(self, pedido: Pedido) -> Resposta:
        try:
            resposta = self._interno.gerar(pedido)
        except RespostaAusente as falta:
            if not self._coletar:
                raise
            self.ausentes.append(falta.chave)
            return Resposta(
                texto='{"achados": []}', tokens_entrada=0, tokens_saida=0, latencia_ms=0
            )
        self.latencias_ms.append(resposta.latencia_ms)
        self.custo_usd += custo(resposta, self._config.preco(pedido.modelo))
        return resposta


def _gabaritos(raiz: Path) -> Iterator[tuple[Path, Gabarito]]:
    for caminho in sorted((raiz / "gabarito").glob("*.json")):
        yield caminho, Gabarito.model_validate_json(caminho.read_text(encoding="utf-8"))


def ler_texto(raiz: Path, id_publico: str) -> TextoEval:
    caminho = raiz / "textos" / f"{slug(id_publico)}.json"
    return TextoEval.model_validate_json(caminho.read_text(encoding="utf-8"))


def _sem_texto(paginas: list[str]) -> bool:
    vazias = sum(1 for p in paginas if len(p.strip()) < MINIMO_CARACTERES)
    return status_do_texto(TextoExtraido(paginas=paginas, paginas_sem_texto=vazias)) == "sem_texto"


def avaliar(
    raiz: Path,
    provedor: Provedor,
    modelo: str,
    config: ConfigIA,
    conjunto: Literal["ajuste", "teste", "todos"],
    replay: bool = True,
) -> Relatorio:
    """Roda a extração sobre o corpus e compara com o gabarito.

    Com `replay`, junta todas as respostas ausentes em `ausentes` em vez de parar na primeira
    (o edital com falta fica fora das métricas).
    """
    relatorio = Relatorio(modelo=modelo)
    medidor = _Medidor(provedor, config, coletar_ausentes=replay)
    for _, gabarito in _gabaritos(raiz):
        if conjunto != "todos" and gabarito.conjunto != conjunto:
            continue
        texto = ler_texto(raiz, gabarito.edital)
        if _sem_texto(texto.paginas):
            previsto = Exigencias()  # como no pipeline: sem texto, sem chamada
        else:
            faltas_antes = len(medidor.ausentes)
            extracao = extrair_exigencias(
                texto.paginas, medidor, modelo, config, texto.valor_estimado
            )
            if len(medidor.ausentes) > faltas_antes:
                continue
            previsto = extracao.exigencias
            relatorio.achados_descartados += extracao.achados_descartados
            relatorio.trechos_invalidos += extracao.trechos_invalidos
        contagens = comparar_com_gabarito(previsto, gabarito.exigencias)
        destino = (
            relatorio.por_campo.setdefault(gabarito.conjunto, {})
            if gabarito.conferido
            else relatorio.nao_conferidos
        )
        for campo, contagem in contagens.items():
            destino.setdefault(campo, Contagem()).somar(contagem)
        chave_editais = gabarito.conjunto if gabarito.conferido else "nao_conferido"
        relatorio.editais[chave_editais] = relatorio.editais.get(chave_editais, 0) + 1
    relatorio.custo_usd = medidor.custo_usd
    relatorio.latencias_ms = medidor.latencias_ms
    relatorio.ausentes = medidor.ausentes
    return relatorio


def _fmt(valor: float | None) -> str:
    return "-" if valor is None else f"{valor:.2f}"


def _tabela(contagens: dict[str, Contagem]) -> list[str]:
    linhas = [
        "| campo | VP | FP | FN | precisão | recall | F1 | alucinação |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    total = Contagem()
    for campo in CAMPOS_EVAL:
        c = contagens.get(campo, Contagem())
        total.somar(c)
        linhas.append(
            f"| {campo} | {c.vp} | {c.fp} | {c.fn} | {_fmt(c.precisao)} | {_fmt(c.recall)} "
            f"| {_fmt(c.f1)} | {_fmt(c.taxa_alucinacao)} |"
        )
    linhas.append(
        f"| **total (micro)** | {total.vp} | {total.fp} | {total.fn} | {_fmt(total.precisao)} "
        f"| {_fmt(total.recall)} | {_fmt(total.f1)} | {_fmt(total.taxa_alucinacao)} |"
    )
    return linhas


def _percentil(valores: list[int], p: float) -> str:
    if not valores:
        return "-"
    if len(valores) == 1:
        return str(valores[0])
    return f"{statistics.quantiles(valores, n=100, method='inclusive')[int(p) - 1]:.0f}"


def para_markdown(r: Relatorio) -> str:
    linhas = [
        f"# Evals: {r.modelo}",
        "",
        f"- custo das respostas: US$ {r.custo_usd:.4f}",
        f"- latência por trecho: p50 {_percentil(r.latencias_ms, 50)} ms, "
        f"p95 {_percentil(r.latencias_ms, 95)} ms ({len(r.latencias_ms)} trechos)",
        f"- achados descartados: {r.achados_descartados}; trechos inválidos: {r.trechos_invalidos}",
    ]
    for conjunto in ("ajuste", "teste"):
        if conjunto in r.por_campo:
            linhas += [
                "",
                f"## Conjunto {conjunto} ({r.editais.get(conjunto, 0)} editais conferidos)",
                "",
                *_tabela(r.por_campo[conjunto]),
            ]
    if r.nao_conferidos:
        linhas += [
            "",
            f"## Rascunho não conferido: métrica sem valor "
            f"({r.editais.get('nao_conferido', 0)} editais)",
            "",
            *_tabela(r.nao_conferidos),
        ]
    if r.ausentes:
        linhas += [
            "",
            f"## {len(r.ausentes)} respostas ausentes no cache ({INSTRUCAO_GRAVAR})",
            "",
            *(f"- {chave}" for chave in r.ausentes),
        ]
    return "\n".join(linhas) + "\n"


def _colapsar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip().casefold()


def _evidencias(exigencias: Exigencias) -> Iterator[tuple[str, str, int]]:
    for campo in (*ESCALARES, *CATEGORICOS):
        valor = getattr(exigencias, campo)
        if valor is not None:
            yield campo, valor.trecho, valor.pagina
    for atestado in exigencias.atestados:
        yield "atestados", atestado.trecho, atestado.pagina
    for documento in exigencias.documentos:
        yield f"documentos.{documento.valor}", documento.trecho, documento.pagina


def validar_gabaritos(raiz: Path) -> list[str]:
    """Esquema, slug × `edital`, documentos válidos e todo trecho na página que o gabarito diz."""
    erros: list[str] = []
    for caminho in sorted((raiz / "gabarito").glob("*.json")):
        nome = caminho.name
        try:
            gabarito = Gabarito.model_validate_json(caminho.read_text(encoding="utf-8"))
        except ValidationError as erro:
            erros.append(f"{nome}: esquema inválido: {erro.error_count()} erros: {erro}")
            continue
        if slug(gabarito.edital) != caminho.stem:
            erros.append(f"{nome}: slug não bate com o edital {gabarito.edital}")
        if gabarito.conferido and (not gabarito.conferido_por or not gabarito.conferido_em):
            erros.append(f"{nome}: conferido sem conferido_por/conferido_em")
        for documento in gabarito.exigencias.documentos:
            if documento.valor not in DOCUMENTOS:
                erros.append(f"{nome}: documento desconhecido {documento.valor}")
        try:
            texto = ler_texto(raiz, gabarito.edital)
        except FileNotFoundError:
            erros.append(f"{nome}: falta textos/{caminho.name}")
            continue
        paginas = [_colapsar(p) for p in texto.paginas]
        for campo, trecho, pagina in _evidencias(gabarito.exigencias):
            alvo = _colapsar(trecho)
            if not alvo:
                erros.append(f"{nome}: {campo} com trecho vazio")
            elif not 1 <= pagina <= len(paginas) or alvo not in paginas[pagina - 1]:
                onde = [i for i, p in enumerate(paginas, start=1) if alvo in p]
                dica = f" (está na página {onde[0]})" if onde else " (não existe no texto)"
                erros.append(f"{nome}: {campo}: trecho não está na página {pagina}{dica}")
    return erros


def ler_linha_de_base(caminho: Path) -> dict[str, float]:
    """`{"modelo": ..., "conjunto": "ajuste", "f1": {campo: valor}}` ou `{}`."""
    if not caminho.is_file():
        return {}
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    return {str(k): float(v) for k, v in dados.get("f1", {}).items()}
