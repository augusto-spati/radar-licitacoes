"""Gravação idempotente dos editais: registros brutos do PNCP e dicionários normalizados."""

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import Boolean, ColumnElement, literal_column, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import ColumnClause

from radar.editais.modelos import Edital, Fonte

logger = logging.getLogger(__name__)

FUSO = ZoneInfo("America/Sao_Paulo")
# Linhas por comando: ~22 colunas x 1000 fica bem abaixo do limite de 65535 parâmetros.
_LOTE = 1000

Situacao = Literal["aberta", "em_julgamento", "encerrada", "outra"]

# PNCP: 1 Divulgada, 2 Revogada, 3 Anulada, 4 Suspensa. Código desconhecido vira "outra".
_SITUACAO_PNCP: dict[int, Situacao] = {1: "aberta", 2: "encerrada", 3: "encerrada", 4: "outra"}


@dataclass
class ResultadoRegistro:
    inseridos: int = 0
    atualizados: int = 0
    ignorados: int = 0
    invalidos: int = 0


class Anexo(BaseModel):
    nome: str
    id: str


def _brasilia_sem_fuso(valor: datetime | None) -> datetime | None:
    """As datas dos editais ficam como horário de Brasília sem fuso (o formato do PNCP)."""
    if valor is None or valor.tzinfo is None:
        return valor
    return valor.astimezone(FUSO).replace(tzinfo=None)


class EditalNormalizado(BaseModel):
    """O edital no formato comum a todas as fontes. É o que entra no hash."""

    fonte: Fonte
    id_na_fonte: str = Field(min_length=1)
    objeto: str
    orgao_nome: str
    orgao_cnpj: str | None = None
    uf: str = Field(min_length=2, max_length=2)
    municipio: str | None = None
    modalidade_nome: str
    situacao_original: str
    situacao_normalizada: Situacao
    valor_estimado: Decimal | None = None
    data_publicacao: datetime | None = None
    data_abertura_proposta: datetime | None = None
    data_encerramento_proposta: datetime | None = None
    link_origem: str | None = None
    anexos: list[Anexo] = Field(default_factory=list)
    bruto: dict[str, Any]

    @field_validator("data_publicacao", "data_abertura_proposta", "data_encerramento_proposta")
    @classmethod
    def _sem_fuso(cls, valor: datetime | None) -> datetime | None:
        return _brasilia_sem_fuso(valor)

    def para_linha(self) -> dict[str, Any]:
        """Linha da tabela `edital`, sem os campos só do PNCP (que ficam nulos)."""
        return {
            "fonte": self.fonte.value,
            "id_na_fonte": self.id_na_fonte,
            "objeto": self.objeto,
            "orgao_cnpj": self.orgao_cnpj,
            "orgao_nome": self.orgao_nome,
            "uf": self.uf,
            "municipio": self.municipio,
            "modalidade_id": None,
            "modalidade_nome": self.modalidade_nome,
            "situacao_id": None,
            "situacao_original": self.situacao_original,
            "situacao_normalizada": self.situacao_normalizada,
            "valor_estimado": self.valor_estimado,
            "data_publicacao": self.data_publicacao,
            "data_abertura_proposta": self.data_abertura_proposta,
            "data_encerramento_proposta": self.data_encerramento_proposta,
            "link_origem": self.link_origem,
            "hash_conteudo": _hash(self.model_dump(exclude={"bruto"})),
            "bruto": self.bruto,
        }


class _Orgao(BaseModel):
    cnpj: str
    razao_social: str = Field(alias="razaoSocial")

    @field_validator("razao_social")
    @classmethod
    def _sem_espacos(cls, valor: str) -> str:
        return valor.strip()


class _Unidade(BaseModel):
    uf_sigla: str | None = Field(default=None, alias="ufSigla")
    municipio_nome: str | None = Field(default=None, alias="municipioNome")


class RegistroPNCP(BaseModel):
    """Um registro da consulta de contratações do PNCP, já validado."""

    model_config = ConfigDict(populate_by_name=True)

    numero_controle_pncp: str = Field(alias="numeroControlePNCP", min_length=1)
    objeto: str = Field(alias="objetoCompra")
    orgao: _Orgao = Field(alias="orgaoEntidade")
    unidade: _Unidade | None = Field(default=None, alias="unidadeOrgao")
    modalidade_id: int = Field(alias="modalidadeId")
    modalidade_nome: str = Field(alias="modalidadeNome")
    situacao_id: int = Field(alias="situacaoCompraId")
    situacao_nome: str = Field(alias="situacaoCompraNome")
    valor_estimado: Decimal | None = Field(default=None, alias="valorTotalEstimado")
    data_publicacao: datetime = Field(alias="dataPublicacaoPncp")
    data_abertura_proposta: datetime | None = Field(default=None, alias="dataAberturaProposta")
    data_encerramento_proposta: datetime | None = Field(
        default=None, alias="dataEncerramentoProposta"
    )
    data_atualizacao: datetime = Field(alias="dataAtualizacaoGlobal")
    link_origem: str | None = Field(default=None, alias="linkSistemaOrigem")

    @model_validator(mode="before")
    @classmethod
    def _data_atualizacao_com_queda(cls, dados: Any) -> Any:
        if isinstance(dados, dict) and dados.get("dataAtualizacaoGlobal") is None:
            dados = {**dados, "dataAtualizacaoGlobal": dados.get("dataAtualizacao")}
        return dados

    def para_linha(self, bruto: dict[str, Any]) -> dict[str, Any]:
        # Linha montada à mão (e não via EditalNormalizado) porque a UF do PNCP pode faltar.
        linha = {
            "fonte": Fonte.pncp.value,
            "id_na_fonte": self.numero_controle_pncp,
            "objeto": self.objeto,
            "orgao_cnpj": self.orgao.cnpj,
            "orgao_nome": self.orgao.razao_social,
            "uf": self.unidade.uf_sigla if self.unidade else None,
            "municipio": self.unidade.municipio_nome if self.unidade else None,
            "modalidade_id": self.modalidade_id,
            "modalidade_nome": self.modalidade_nome,
            "situacao_id": self.situacao_id,
            "situacao_original": self.situacao_nome,
            "situacao_normalizada": _SITUACAO_PNCP.get(self.situacao_id, "outra"),
            "valor_estimado": self.valor_estimado,
            "data_publicacao": self.data_publicacao,
            "data_abertura_proposta": self.data_abertura_proposta,
            "data_encerramento_proposta": self.data_encerramento_proposta,
            "data_atualizacao": self.data_atualizacao,
            "link_origem": self.link_origem,
            "bruto": bruto,
        }
        # Mesmos campos do normalizado; a consulta do PNCP não lista anexos.
        campos = set(EditalNormalizado.model_fields) - {"bruto", "anexos"}
        linha["hash_conteudo"] = _hash({c: linha[c] for c in campos} | {"anexos": []})
        return linha


def _valor_json(valor: object) -> str:
    if isinstance(valor, datetime):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return str(valor)
    raise TypeError(f"sem forma canônica para {type(valor).__name__}")


def _hash(conteudo: dict[str, Any]) -> str:
    """sha256 do JSON canônico (chaves ordenadas) dos campos normalizados e dos anexos.

    Não entra nada volátil (bruto, visto_em, data_atualizacao).
    """
    canonico = json.dumps(
        conteudo, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=_valor_json
    )
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def _avisar_invalido(chave: object, erro: ValidationError) -> None:
    logger.warning(
        "registro_invalido",
        extra={
            "chave": str(chave),
            "motivo": "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in erro.errors()
            ),
        },
    )


def _validar_pncp(
    registros: list[dict[str, Any]], resultado: ResultadoRegistro
) -> dict[str, tuple[RegistroPNCP, dict[str, Any]]]:
    """Valida e deduplica o lote, mantendo o de data_atualizacao mais nova por chave."""
    vencedores: dict[str, tuple[RegistroPNCP, dict[str, Any]]] = {}
    for bruto in registros:
        try:
            parsed = RegistroPNCP.model_validate(bruto)
        except ValidationError as erro:
            resultado.invalidos += 1
            _avisar_invalido(bruto.get("numeroControlePNCP"), erro)
            continue
        atual = vencedores.get(parsed.numero_controle_pncp)
        if atual is None:
            vencedores[parsed.numero_controle_pncp] = (parsed, bruto)
            continue
        resultado.ignorados += 1
        if parsed.data_atualizacao > atual[0].data_atualizacao:
            vencedores[parsed.numero_controle_pncp] = (parsed, bruto)
    return vencedores


type _CondicaoUpsert = Callable[[Any], ColumnElement[bool]]


def _upsert(
    sessao: Session, linhas: list[dict[str, Any]], atualizar_se: _CondicaoUpsert
) -> tuple[int, set[tuple[str, str]]]:
    """Upsert por (fonte, id_na_fonte), em lotes. Devolve os inseridos e as chaves gravadas.

    `atualizar_se(excluded)` é o WHERE do ON CONFLICT: a linha existente só é sobrescrita
    quando ele é verdadeiro. A diferença entre o total e os gravados são os ignorados.
    """
    inseridos = 0
    gravadas: set[tuple[str, str]] = set()
    for inicio in range(0, len(linhas), _LOTE):
        insercao = insert(Edital).values(linhas[inicio : inicio + _LOTE])
        colunas_novas: dict[str, ColumnClause[Any]] = {
            c.name: insercao.excluded[c.name]
            for c in Edital.__table__.columns
            if not c.primary_key and c.computed is None
        }
        comando = insercao.on_conflict_do_update(
            index_elements=["fonte", "id_na_fonte"],
            set_=colunas_novas,
            where=atualizar_se(insercao.excluded),
        ).returning(
            Edital.fonte,
            Edital.id_na_fonte,
            literal_column("(xmax = 0)", Boolean).label("inserido"),
        )
        for fonte, id_na_fonte, inserido in sessao.execute(comando):
            gravadas.add((fonte, id_na_fonte))
            inseridos += int(inserido)
    return inseridos, gravadas


def _contar(
    resultado: ResultadoRegistro, total: int, inseridos: int, gravadas: set[tuple[str, str]]
) -> None:
    resultado.inseridos = inseridos
    resultado.atualizados = len(gravadas) - inseridos
    resultado.ignorados += total - len(gravadas)


def registrar(sessao: Session, registros: list[dict[str, Any]]) -> ResultadoRegistro:
    """Grava registros brutos do PNCP. Vence o de dataAtualizacaoGlobal mais nova. Sem commit."""
    resultado = ResultadoRegistro()
    validos = _validar_pncp(registros, resultado)
    if not validos:
        return resultado
    linhas = [parsed.para_linha(bruto) for parsed, bruto in validos.values()]
    inseridos, gravadas = _upsert(
        sessao, linhas, lambda novo: Edital.data_atualizacao < novo.data_atualizacao
    )
    _contar(resultado, len(linhas), inseridos, gravadas)
    return resultado


def registrar_normalizados(sessao: Session, registros: list[dict[str, Any]]) -> ResultadoRegistro:
    """Grava editais já normalizados (fontes de retrato). Sem commit.

    Atualiza só quando o hash do conteúdo muda; `visto_em` é renovado em todos os válidos.
    No mesmo lote, a última ocorrência de uma chave vence (as anteriores contam como ignoradas).
    """
    resultado = ResultadoRegistro()
    vencedores: dict[tuple[str, str], EditalNormalizado] = {}
    for dados in registros:
        try:
            edital = EditalNormalizado.model_validate(dados)
        except ValidationError as erro:
            resultado.invalidos += 1
            _avisar_invalido(f"{dados.get('fonte')}~{dados.get('id_na_fonte')}", erro)
            continue
        chave = (edital.fonte.value, edital.id_na_fonte)
        if chave in vencedores:
            resultado.ignorados += 1
        vencedores[chave] = edital
    if not vencedores:
        return resultado

    visto_em = datetime.now(UTC)
    mudou_em = _brasilia_sem_fuso(visto_em)
    linhas = [
        e.para_linha() | {"visto_em": visto_em, "data_atualizacao": mudou_em}
        for e in vencedores.values()
    ]
    inseridos, gravadas = _upsert(
        sessao, linhas, lambda novo: Edital.hash_conteudo.is_distinct_from(novo.hash_conteudo)
    )
    _contar(resultado, len(linhas), inseridos, gravadas)

    sem_mudanca = set(vencedores) - gravadas
    if sem_mudanca:
        sessao.execute(
            update(Edital)
            .where(tuple_(Edital.fonte, Edital.id_na_fonte).in_(sem_mudanca))
            .values(visto_em=visto_em)
        )
    return resultado


def reconciliar_retrato(sessao: Session, fonte: str, vistos: set[str], quando: datetime) -> int:
    """Encerra os editais abertos ou em julgamento da fonte que saíram de um retrato completo.

    Só deve ser chamada depois de um retrato bem-sucedido. O hash é zerado para que o edital,
    se voltar à lista igual ao que era, seja regravado e reaberto. Sem commit.
    """
    if fonte == Fonte.pncp:
        raise ValueError("o pncp é coletado por janelas, não por retrato")
    comando = (
        update(Edital)
        .where(
            Edital.fonte == fonte,
            Edital.situacao_normalizada.in_(["aberta", "em_julgamento"]),
            Edital.id_na_fonte.not_in(vistos),
        )
        .values(
            situacao_normalizada="encerrada",
            situacao_original="saiu da lista",
            hash_conteudo=None,
            data_atualizacao=_brasilia_sem_fuso(quando),
        )
    )
    return sessao.execute(comando).rowcount  # type: ignore[attr-defined, no-any-return]
