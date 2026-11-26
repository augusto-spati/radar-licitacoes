"""Derivação idempotente dos editais a partir dos registros brutos do PNCP."""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import Boolean, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from radar.editais.modelos import Edital

logger = logging.getLogger(__name__)


@dataclass
class ResultadoRegistro:
    inseridos: int = 0
    atualizados: int = 0
    ignorados: int = 0
    invalidos: int = 0


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
        return {
            "numero_controle_pncp": self.numero_controle_pncp,
            "objeto": self.objeto,
            "orgao_cnpj": self.orgao.cnpj,
            "orgao_nome": self.orgao.razao_social,
            "uf": self.unidade.uf_sigla if self.unidade else None,
            "municipio": self.unidade.municipio_nome if self.unidade else None,
            "modalidade_id": self.modalidade_id,
            "situacao_id": self.situacao_id,
            "situacao_nome": self.situacao_nome,
            "valor_estimado": self.valor_estimado,
            "data_publicacao": self.data_publicacao,
            "data_abertura_proposta": self.data_abertura_proposta,
            "data_encerramento_proposta": self.data_encerramento_proposta,
            "data_atualizacao": self.data_atualizacao,
            "link_origem": self.link_origem,
            "bruto": bruto,
        }


def _validar(
    registros: list[dict[str, Any]], resultado: ResultadoRegistro
) -> dict[str, tuple[RegistroPNCP, dict[str, Any]]]:
    """Valida e deduplica o lote, mantendo o de data_atualizacao mais nova por chave."""
    vencedores: dict[str, tuple[RegistroPNCP, dict[str, Any]]] = {}
    for bruto in registros:
        try:
            parsed = RegistroPNCP.model_validate(bruto)
        except ValidationError as erro:
            resultado.invalidos += 1
            logger.warning(
                "registro_invalido",
                extra={
                    "chave": str(bruto.get("numeroControlePNCP")),
                    "motivo": "; ".join(
                        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in erro.errors()
                    ),
                },
            )
            continue
        atual = vencedores.get(parsed.numero_controle_pncp)
        if atual is None:
            vencedores[parsed.numero_controle_pncp] = (parsed, bruto)
            continue
        resultado.ignorados += 1
        if parsed.data_atualizacao > atual[0].data_atualizacao:
            vencedores[parsed.numero_controle_pncp] = (parsed, bruto)
    return vencedores


def registrar(sessao: Session, registros: list[dict[str, Any]]) -> ResultadoRegistro:
    """Grava os registros como editais num único upsert. Não faz commit."""
    resultado = ResultadoRegistro()
    validos = _validar(registros, resultado)
    if not validos:
        return resultado

    linhas = [parsed.para_linha(bruto) for parsed, bruto in validos.values()]
    insercao = insert(Edital).values(linhas)
    colunas_novas = {
        c.name: insercao.excluded[c.name]
        for c in Edital.__table__.columns
        if c.name != "numero_controle_pncp"
    }
    comando = insercao.on_conflict_do_update(
        index_elements=["numero_controle_pncp"],
        set_=colunas_novas,
        where=Edital.data_atualizacao < insercao.excluded.data_atualizacao,
    ).returning(literal_column("(xmax = 0)", Boolean).label("inserido"))

    gravados = sessao.execute(comando).scalars().all()
    resultado.inseridos = sum(1 for inserido in gravados if inserido)
    resultado.atualizados = len(gravados) - resultado.inseridos
    resultado.ignorados += len(linhas) - len(gravados)
    return resultado
