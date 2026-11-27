"""Um edital do começo ao fim: documentos, extração, comparação e gravação."""

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from radar.analise.armazem import Armazem
from radar.analise.arquivos_compesa import DocumentosCompesa
from radar.analise.arquivos_pncp import (
    ArquivoPNCP,
    ClienteArquivosPNCP,
    ErroArquivos,
    GrandeDemais,
    chave_pncp,
    escolher_documentos,
)
from radar.analise.arquivos_sanepar import DocumentosSanepar
from radar.analise.cache import versao_prompt
from radar.analise.comparacao import comparar
from radar.analise.custos import custo
from radar.analise.exigencias import Exigencias, esquema_resposta
from radar.analise.extracao import (
    ErroExtracao,
    TextoExtraido,
    eh_docx,
    eh_pdf,
    extrair_paginas,
    extrair_paginas_docx,
    status_do_texto,
)
from radar.analise.extrator import SISTEMA, extrair_exigencias
from radar.analise.modelos import AnaliseEdital, DocumentoEdital, StatusDocumento
from radar.analise.perfil import Perfil
from radar.analise.provedor import ConfigIA, Provedor, Resposta

logger = logging.getLogger(__name__)


class Baixador(Protocol):
    def baixar(self, url: str) -> bytes: ...


class SemDocumentos(Exception):
    """A fonte ainda não tem edital nem termo de referência: nada é gravado, fica para a
    próxima rodada (a Compesa sobe a pasta da obra dias depois do aviso, por exemplo)."""


@dataclass(frozen=True)
class AlvoAnalise:
    fonte: str
    id_na_fonte: str
    valor_estimado: Decimal | None
    # Registro bruto da fonte, para quem tira os documentos dele (Sanepar: `detalhe.anexos`).
    # Quem monta o alvo lê do banco (`radar.editais.bruto_do_edital`): analise não importa editais.
    bruto: dict[str, Any] | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Dependencias:
    cliente_arquivos: ClienteArquivosPNCP
    armazem: Armazem
    provedor: Provedor
    config: ConfigIA
    perfil: Perfil
    modelo: str
    # Documentos da Sanepar: outro cliente, preso a www.sanepar.com.br. Sem ele, edital da
    # Sanepar falha com ValueError (a CLI sempre passa; testes do PNCP não precisam).
    documentos_sanepar: DocumentosSanepar | None = None
    # Compesa: portal SCL (porta 8743) e nuvem.
    documentos_compesa: DocumentosCompesa | None = None


FONTES_ANALISADAS = ("pncp", "sanepar", "compesa")
# Padrão de `radar analisar` sem `--fontes`; a agenda da AWS pede as três explicitamente.
FONTES_PADRAO = ("pncp", "sanepar")


@dataclass(frozen=True)
class AnaliseLida:
    analise: AnaliseEdital
    documentos: list[DocumentoEdital]


@dataclass(frozen=True)
class _Documento:
    """O que o pipeline precisa de um documento, sem objeto ORM (que recarregaria do banco)."""

    sequencial: int
    sha256: str | None
    status: str


@dataclass
class _Documentos:
    linhas: list[_Documento] = field(default_factory=list)
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


def _upsert_documento(sessao: Session, valores: dict[str, Any]) -> None:
    """Transação curta própria: grava e commita antes de qualquer rede ou chamada paga."""
    atualizar = {k: v for k, v in valores.items() if k not in ("fonte", "id_na_fonte")}
    sessao.execute(
        insert(DocumentoEdital)
        .values(**valores)
        .on_conflict_do_update(constraint="uq_documento_edital", set_=atualizar)
    )
    sessao.commit()


def _texto_guardado(
    sessao: Session, alvo: AlvoAnalise, arquivo: ArquivoPNCP, armazem: Armazem, pagina: int
) -> tuple[_Documento, list[str]] | None:
    """Documento já extraído antes e com o texto no armazém: não baixa de novo."""
    anterior = sessao.execute(
        select(DocumentoEdital.id, DocumentoEdital.status, DocumentoEdital.sha256,
               DocumentoEdital.url, DocumentoEdital.pagina_inicial)
        .where(
            DocumentoEdital.fonte == alvo.fonte,
            DocumentoEdital.id_na_fonte == alvo.id_na_fonte,
            DocumentoEdital.sequencial == arquivo.sequencial,
        )
    ).first()  # fmt: skip
    sessao.commit()  # fecha a leitura antes de ir ao armazém (S3)
    if anterior is None or anterior.status != "extraido" or not anterior.sha256:
        return None
    if anterior.url != arquivo.url:
        return None
    bruto = armazem.ler(_chave_texto(anterior.sha256))
    if bruto is None:
        return None
    paginas: list[str] = json.loads(bruto)
    if anterior.pagina_inicial != pagina:
        sessao.execute(
            update(DocumentoEdital)
            .where(DocumentoEdital.id == anterior.id)
            .values(pagina_inicial=pagina)
        )
        sessao.commit()
    return _Documento(arquivo.sequencial, anterior.sha256, "extraido"), paginas


def _processar_documento(
    sessao: Session,
    alvo: AlvoAnalise,
    arquivo: ArquivoPNCP,
    dep: Dependencias,
    agora: datetime,
    pagina: int,
    cliente: Baixador,
) -> tuple[_Documento, list[str]]:
    """Baixa, extrai e grava um documento. `pagina` = onde ele começaria no texto juntado."""
    guardado = _texto_guardado(sessao, alvo, arquivo, dep.armazem, pagina)
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
        "pagina_inicial": None,
        "erro": None,
        "baixado_em": agora,
    }
    paginas: list[str] = []
    status: StatusDocumento
    try:
        dados = cliente.baixar(arquivo.url)
    except GrandeDemais as erro:
        status, valores["erro"] = "grande_demais", str(erro)
    except ErroArquivos as erro:
        status, valores["erro"] = "falha", str(erro)
    else:
        sha = hashlib.sha256(dados).hexdigest()
        valores.update(sha256=sha, tamanho=len(dados))
        extrator = _extrator(dados)
        if extrator is None:
            status = "nao_pdf"
        else:
            try:
                texto = extrator(dados)
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
                    valores["pagina_inicial"] = pagina
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
    _upsert_documento(sessao, valores)
    return _Documento(arquivo.sequencial, valores["sha256"], status), paginas


def _extrator(dados: bytes) -> Callable[[bytes], TextoExtraido] | None:
    """PDF ou Word (.docx, edital de obra da Compesa); o resto fica como `nao_pdf`."""
    if eh_pdf(dados):
        return extrair_paginas
    if eh_docx(dados):
        return extrair_paginas_docx
    return None


def _arquivos_da_fonte(alvo: AlvoAnalise, dep: Dependencias) -> tuple[Baixador, list[ArquivoPNCP]]:
    """Quem baixa e o quê: API do PNCP, anexos guardados da Sanepar, portal/nuvem da Compesa."""
    if alvo.fonte == "pncp":
        chave = chave_pncp(alvo.id_na_fonte)
        if chave is None:
            raise ValueError(f"id do PNCP fora do formato: {alvo.id_na_fonte}")
        return dep.cliente_arquivos, escolher_documentos(dep.cliente_arquivos.listar(*chave))
    if alvo.fonte == "sanepar":
        if dep.documentos_sanepar is None:
            raise ValueError("sem cliente de documentos da Sanepar")
        return dep.documentos_sanepar, dep.documentos_sanepar.listar(alvo.bruto)
    if alvo.fonte == "compesa":
        if dep.documentos_compesa is None:
            raise ValueError("sem cliente de documentos da Compesa")
        return dep.documentos_compesa, dep.documentos_compesa.listar(alvo.bruto)
    raise ValueError(f"fonte sem análise: {alvo.fonte}~{alvo.id_na_fonte}")


def _documentos(
    sessao: Session, alvo: AlvoAnalise, dep: Dependencias, agora: datetime
) -> _Documentos:
    cliente, escolhidos = _arquivos_da_fonte(alvo, dep)
    if not escolhidos:
        raise SemDocumentos(f"{alvo.fonte}~{alvo.id_na_fonte}")
    resultado = _Documentos()
    for arquivo in escolhidos:
        inicio = len(resultado.paginas) + 1
        documento, paginas = _processar_documento(
            sessao, alvo, arquivo, dep, agora, inicio, cliente
        )
        resultado.linhas.append(documento)
        resultado.paginas.extend(paginas)
    return resultado


def _custo_da_rodada(provedor: Provedor) -> Decimal | None:
    valor = getattr(provedor, "custo_usd", None)
    return valor if isinstance(valor, Decimal) else None


def analisar_edital(
    sessao: Session, alvo: AlvoAnalise, dep: Dependencias, agora: datetime
) -> AnaliseEdital:
    """Analisa e grava. Nenhuma transação da sessão fica aberta durante rede ou Bedrock.

    Cada passo que toca o banco é uma transação curta commitada na hora (o Neon derruba
    transação ociosa por `idle_in_transaction_session_timeout`): leitura do documento
    anterior, gravação de cada documento, gravação da análise. As respostas pagas vão para
    `resposta_llm` pelo `CacheBanco`, em conexão própria, logo após cada chamada: uma falha
    no meio do edital (inclusive `TetoAtingido`, que propaga) não apaga gasto nem cache.
    Quem chama não deve ter mudanças pendentes na sessão (elas seriam commitadas). O objeto
    devolvido já foi commitado (com `expire_on_commit`, ler um atributo abre transação nova).
    """
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
    except Exception:
        sessao.rollback()
        raise
    logger.info(
        "analise_concluida",
        extra={
            "fonte": alvo.fonte,
            "id_na_fonte": alvo.id_na_fonte,
            "modelo": dep.modelo,
            "veredito": comparacao.veredito,
            "incompleta": incompleta,
            "trechos": trechos,
            "custo_usd": str(custo_usd),
        },
    )
    return analise


def pagina_no_documento(
    documentos: list[DocumentoEdital], pagina: int | None
) -> tuple[DocumentoEdital, int] | None:
    """A página do texto juntado → (documento, página dentro do PDF dele)."""
    if pagina is None:
        return None
    for documento in documentos:
        inicio, total = documento.pagina_inicial, documento.paginas
        if inicio is not None and total and inicio <= pagina < inicio + total:
            return documento, pagina - inicio + 1
    return None


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
