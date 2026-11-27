"""Um edital do começo ao fim: documentos, extração, comparação e gravação."""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from radar.analise.armazem import Armazem
from radar.analise.arquivos_pncp import (
    ArquivoPNCP,
    ClienteArquivosPNCP,
    ErroArquivos,
    GrandeDemais,
    chave_pncp,
    escolher_documentos,
)
from radar.analise.cache import versao_prompt
from radar.analise.comparacao import comparar
from radar.analise.custos import TetoAtingido, custo
from radar.analise.exigencias import Exigencias, esquema_resposta
from radar.analise.extracao import ErroExtracao, eh_pdf, extrair_paginas, status_do_texto
from radar.analise.extrator import SISTEMA, extrair_exigencias
from radar.analise.modelos import AnaliseEdital, DocumentoEdital, StatusDocumento
from radar.analise.perfil import Perfil
from radar.analise.provedor import ConfigIA, Provedor, Resposta

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlvoAnalise:
    fonte: str
    id_na_fonte: str
    valor_estimado: Decimal | None


@dataclass(frozen=True)
class Dependencias:
    cliente_arquivos: ClienteArquivosPNCP
    armazem: Armazem
    provedor: Provedor
    config: ConfigIA
    perfil: Perfil
    modelo: str


@dataclass(frozen=True)
class AnaliseLida:
    analise: AnaliseEdital
    documentos: list[DocumentoEdital]


@dataclass
class _Documentos:
    linhas: list[DocumentoEdital] = field(default_factory=list)
    paginas: list[str] = field(default_factory=list)


def versao_prompt_atual() -> str:
    return versao_prompt(SISTEMA, esquema_resposta())


def ja_analisado(sessao: Session, alvo: AlvoAnalise, modelo: str, versao: str) -> bool:
    return (
        sessao.scalar(
            select(AnaliseEdital.id).where(
                AnaliseEdital.fonte == alvo.fonte,
                AnaliseEdital.id_na_fonte == alvo.id_na_fonte,
                AnaliseEdital.modelo == modelo,
                AnaliseEdital.versao_prompt == versao,
            )
        )
        is not None
    )


def _chave_pdf(sha: str) -> str:
    return f"documentos/{sha}.pdf"


def _chave_texto(sha: str) -> str:
    return f"textos/{sha}.json"


def _upsert_documento(sessao: Session, valores: dict[str, Any]) -> DocumentoEdital:
    atualizar = {k: v for k, v in valores.items() if k not in ("fonte", "id_na_fonte")}
    comando = (
        insert(DocumentoEdital)
        .values(**valores)
        .on_conflict_do_update(constraint="uq_documento_edital", set_=atualizar)
        .returning(DocumentoEdital)
    )
    linha: DocumentoEdital = sessao.scalars(
        comando, execution_options={"populate_existing": True}
    ).one()
    return linha


def _texto_guardado(
    sessao: Session, alvo: AlvoAnalise, arquivo: ArquivoPNCP, armazem: Armazem
) -> tuple[DocumentoEdital, list[str]] | None:
    """Documento já extraído antes e com o texto no armazém: não baixa de novo."""
    anterior = sessao.scalar(
        select(DocumentoEdital).where(
            DocumentoEdital.fonte == alvo.fonte,
            DocumentoEdital.id_na_fonte == alvo.id_na_fonte,
            DocumentoEdital.sequencial == arquivo.sequencial,
        )
    )
    if anterior is None or anterior.status != "extraido" or not anterior.sha256:
        return None
    if anterior.url != arquivo.url:
        return None
    bruto = armazem.ler(_chave_texto(anterior.sha256))
    if bruto is None:
        return None
    paginas: list[str] = json.loads(bruto)
    return anterior, paginas


def _processar_documento(
    sessao: Session, alvo: AlvoAnalise, arquivo: ArquivoPNCP, dep: Dependencias, agora: datetime
) -> tuple[DocumentoEdital, list[str]]:
    guardado = _texto_guardado(sessao, alvo, arquivo, dep.armazem)
    if guardado is not None:
        return guardado
    valores: dict[str, Any] = {
        "fonte": alvo.fonte,
        "id_na_fonte": alvo.id_na_fonte,
        "sequencial": arquivo.sequencial,
        "titulo": arquivo.titulo,
        "tipo_nome": arquivo.tipo_nome,
        "url": arquivo.url,
        "sha256": None,
        "tamanho": None,
        "paginas": None,
        "paginas_sem_texto": None,
        "erro": None,
        "baixado_em": agora,
    }
    paginas: list[str] = []
    status: StatusDocumento
    try:
        dados = dep.cliente_arquivos.baixar(arquivo.url)
    except GrandeDemais as erro:
        status, valores["erro"] = "grande_demais", str(erro)
    except ErroArquivos as erro:
        status, valores["erro"] = "falha", str(erro)
    else:
        sha = hashlib.sha256(dados).hexdigest()
        valores.update(sha256=sha, tamanho=len(dados))
        if not eh_pdf(dados):
            status = "nao_pdf"
        else:
            try:
                texto = extrair_paginas(dados)
            except ErroExtracao as erro:
                status, valores["erro"] = "falha", str(erro)
            else:
                status = status_do_texto(texto)
                valores.update(
                    paginas=len(texto.paginas), paginas_sem_texto=texto.paginas_sem_texto
                )
                dep.armazem.guardar(_chave_pdf(sha), dados)
                corpo = json.dumps(texto.paginas, ensure_ascii=False).encode("utf-8")
                dep.armazem.guardar(_chave_texto(sha), corpo)
                if status == "extraido":
                    paginas = texto.paginas
    valores["status"] = status
    if status == "falha":
        logger.warning(
            "documento_falhou",
            extra={
                "fonte": alvo.fonte,
                "id_na_fonte": alvo.id_na_fonte,
                "sequencial": arquivo.sequencial,
                "erro": valores["erro"],
            },
        )
    return _upsert_documento(sessao, valores), paginas


def _documentos(
    sessao: Session, alvo: AlvoAnalise, dep: Dependencias, agora: datetime
) -> _Documentos:
    chave = chave_pncp(alvo.id_na_fonte) if alvo.fonte == "pncp" else None
    if chave is None:
        raise ValueError(f"só editais do PNCP nesta etapa: {alvo.fonte}~{alvo.id_na_fonte}")
    escolhidos = escolher_documentos(dep.cliente_arquivos.listar(*chave))
    resultado = _Documentos()
    for arquivo in escolhidos:
        linha, paginas = _processar_documento(sessao, alvo, arquivo, dep, agora)
        resultado.linhas.append(linha)
        resultado.paginas.extend(paginas)
    return resultado


def _custo_da_rodada(provedor: Provedor) -> Decimal | None:
    valor = getattr(provedor, "custo_usd", None)
    return valor if isinstance(valor, Decimal) else None


def analisar_edital(
    sessao: Session, alvo: AlvoAnalise, dep: Dependencias, agora: datetime
) -> AnaliseEdital:
    """Analisa e grava (upsert + commit). `TetoAtingido` desfaz tudo deste edital e propaga."""
    versao = versao_prompt_atual()
    custo_antes = _custo_da_rodada(dep.provedor)
    try:
        documentos = _documentos(sessao, alvo, dep, agora)
        extraidos = [d for d in documentos.linhas if d.status == "extraido"]
        docs_incompletos = len(extraidos) < len(documentos.linhas)
        if not extraidos:
            logger.info(
                "analise_sem_texto",
                extra={
                    "fonte": alvo.fonte,
                    "id_na_fonte": alvo.id_na_fonte,
                    "status": [d.status for d in documentos.linhas],
                },
            )
            exigencias = Exigencias()
            trechos = invalidos = descartados = entrada = saida = 0
            truncado = False
            incompleta = True
        else:
            extracao = extrair_exigencias(
                documentos.paginas, dep.provedor, dep.modelo, dep.config, alvo.valor_estimado
            )
            exigencias = extracao.exigencias
            trechos, invalidos = extracao.trechos, extracao.trechos_invalidos
            descartados, truncado = extracao.achados_descartados, extracao.truncado
            entrada, saida = extracao.tokens_entrada, extracao.tokens_saida
            incompleta = extracao.incompleta or docs_incompletos
        comparacao = comparar(exigencias, dep.perfil, incompleta)
        custo_depois = _custo_da_rodada(dep.provedor)
        if custo_antes is not None and custo_depois is not None:
            custo_usd = custo_depois - custo_antes
        else:
            usados = Resposta(texto="", tokens_entrada=entrada, tokens_saida=saida, latencia_ms=0)
            custo_usd = custo(usados, dep.config.preco(dep.modelo))
        hash_documentos = hashlib.sha256(
            "|".join(f"{d.sequencial}:{d.sha256 or ''}" for d in documentos.linhas).encode()
        ).hexdigest()
        valores: dict[str, Any] = {
            "fonte": alvo.fonte,
            "id_na_fonte": alvo.id_na_fonte,
            "modelo": dep.modelo,
            "versao_prompt": versao,
            "hash_documentos": hash_documentos,
            "veredito": comparacao.veredito,
            "incompleta": incompleta,
            "exigencias": exigencias.model_dump(mode="json"),
            "comparacao": comparacao.model_dump(mode="json"),
            "trechos": trechos,
            "trechos_invalidos": invalidos,
            "achados_descartados": descartados,
            "truncado": truncado,
            "tokens_entrada": entrada,
            "tokens_saida": saida,
            "custo_usd": custo_usd,
            "criada_em": agora,
        }
        atualizar = {
            k: v for k, v in valores.items() if k not in ("fonte", "id_na_fonte", "modelo")
        }
        analise: AnaliseEdital = sessao.scalars(
            insert(AnaliseEdital)
            .values(**valores)
            .on_conflict_do_update(constraint="uq_analise_edital", set_=atualizar)
            .returning(AnaliseEdital),
            execution_options={"populate_existing": True},
        ).one()
        sessao.commit()
    except TetoAtingido:
        sessao.rollback()
        raise
    logger.info(
        "analise_concluida",
        extra={
            "fonte": alvo.fonte,
            "id_na_fonte": alvo.id_na_fonte,
            "modelo": dep.modelo,
            "veredito": analise.veredito,
            "incompleta": incompleta,
            "trechos": trechos,
            "custo_usd": str(custo_usd),
        },
    )
    return analise


def obter_analise(sessao: Session, fonte: str, id_na_fonte: str, modelo: str) -> AnaliseLida | None:
    """A análise mais nova do edital para o modelo, com os documentos."""
    analise = sessao.scalar(
        select(AnaliseEdital)
        .where(
            AnaliseEdital.fonte == fonte,
            AnaliseEdital.id_na_fonte == id_na_fonte,
            AnaliseEdital.modelo == modelo,
        )
        .order_by(AnaliseEdital.criada_em.desc(), AnaliseEdital.id.desc())
        .limit(1)
    )
    if analise is None:
        return None
    documentos = list(
        sessao.scalars(
            select(DocumentoEdital)
            .where(DocumentoEdital.fonte == fonte, DocumentoEdital.id_na_fonte == id_na_fonte)
            .order_by(DocumentoEdital.sequencial)
        )
    )
    return AnaliseLida(analise=analise, documentos=documentos)
