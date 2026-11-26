"""Formato de saída da API: datas ISO sem fuso (Brasília) e valores como string decimal."""

from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from radar.editais import EditalDetalhe, EditalResumo

FUSO = ZoneInfo("America/Sao_Paulo")


class EditalResumoOut(BaseModel):
    numero_controle_pncp: str
    objeto: str
    orgao_nome: str
    uf: str | None
    municipio: str | None
    modalidade_id: int
    valor_estimado: str | None
    data_encerramento_proposta: str | None
    link_origem: str | None
    linhas: list[str]


class EditalDetalheOut(EditalResumoOut):
    orgao_cnpj: str
    situacao_nome: str
    data_publicacao: str
    data_abertura_proposta: str | None


class ListaOut(BaseModel):
    atualizado_em: str | None
    editais: list[EditalResumoOut]


class StatusOut(BaseModel):
    atualizado_em: str | None


def _iso(valor: datetime | None) -> str | None:
    return valor.isoformat() if valor else None


def iso_com_fuso(valor: datetime | None) -> str | None:
    """ISO 8601 com o deslocamento de Brasília (para `atualizado_em`)."""
    return valor.astimezone(FUSO).isoformat() if valor else None


def link_http(link: str | None) -> str | None:
    """Só um endereço http(s) vira link; vazio ou outro esquema sai nulo (o front cai no PNCP)."""
    aparado = (link or "").strip()
    return aparado if aparado.lower().startswith(("https://", "http://")) else None


def resumo_out(edital: EditalResumo) -> EditalResumoOut:
    return EditalResumoOut(
        numero_controle_pncp=edital.numero_controle_pncp,
        objeto=edital.objeto,
        orgao_nome=edital.orgao_nome,
        uf=edital.uf,
        municipio=edital.municipio,
        modalidade_id=edital.modalidade_id,
        valor_estimado=None if edital.valor_estimado is None else str(edital.valor_estimado),
        data_encerramento_proposta=_iso(edital.data_encerramento_proposta),
        link_origem=link_http(edital.link_origem),
        linhas=edital.linhas,
    )


def detalhe_out(edital: EditalDetalhe) -> EditalDetalheOut:
    return EditalDetalheOut(
        **resumo_out(edital).model_dump(),
        orgao_cnpj=edital.orgao_cnpj,
        situacao_nome=edital.situacao_nome,
        data_publicacao=edital.data_publicacao.isoformat(),
        data_abertura_proposta=_iso(edital.data_abertura_proposta),
    )
