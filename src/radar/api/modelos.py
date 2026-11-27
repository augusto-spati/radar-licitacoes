"""Formato de saída da API: datas ISO sem fuso (Brasília) e valores como string decimal."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from radar.analise import (
    AnaliseLida,
    SituacaoItem,
    StatusDocumento,
    Veredito,
    pagina_no_documento,
)
from radar.editais import EditalDetalhe, EditalResumo, Fonte, Situacao, id_publico

FUSO = ZoneInfo("America/Sao_Paulo")


class EditalResumoOut(BaseModel):
    fonte: Fonte
    id_publico: str
    objeto: str
    orgao_nome: str
    uf: str | None
    municipio: str | None
    modalidade_id: int | None
    modalidade_nome: str
    valor_estimado: str | None
    data_encerramento_proposta: str | None
    link_origem: str | None
    linhas: list[str]


class EditalDetalheOut(EditalResumoOut):
    orgao_cnpj: str | None
    situacao_nome: str
    situacao_normalizada: Situacao
    data_publicacao: str | None
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
    """Só um endereço http(s) vira link; vazio ou outro esquema sai nulo."""
    aparado = (link or "").strip()
    return aparado if aparado.lower().startswith(("https://", "http://")) else None


_PNCP = re.compile(r"^(\d{14})-\d+-(\d+)/(\d{4})$")
_SANEPAR = re.compile(r"^OE-(\d+)/(\d{2})$")
_COMPESA = "https://servicos.compesa.com.br/licitacoes-e-contratos/"


def link_reserva(fonte: str, id_na_fonte: str) -> str | None:
    """Link para quando a fonte não traz um `link_origem` http(s) válido.

    - PNCP: a página do edital, montada do número de controle (`cnpj-1-seq/ano`);
    - Sanepar: a página do detalhe (`OE-345/26` -> `/licitacao/detalhe/345/26`);
    - Compesa: a página de licitações (o link direto do processo ainda não é conhecido).
    """
    if fonte == Fonte.pncp:
        if m := _PNCP.match(id_na_fonte):
            return f"https://pncp.gov.br/app/editais/{m[1]}/{m[3]}/{int(m[2])}"
        return None
    if fonte == Fonte.sanepar:
        if m := _SANEPAR.match(id_na_fonte):
            return f"https://www.sanepar.com.br/licitacao/detalhe/{m[1]}/{m[2]}"
        return None
    if fonte == Fonte.compesa:
        return _COMPESA
    return None


def resumo_out(edital: EditalResumo) -> EditalResumoOut:
    return EditalResumoOut(
        fonte=Fonte(edital.fonte),
        id_publico=edital.id_publico,
        objeto=edital.objeto,
        orgao_nome=edital.orgao_nome,
        uf=edital.uf,
        municipio=edital.municipio,
        modalidade_id=edital.modalidade_id,
        modalidade_nome=edital.modalidade_nome,
        valor_estimado=None if edital.valor_estimado is None else str(edital.valor_estimado),
        data_encerramento_proposta=_iso(edital.data_encerramento_proposta),
        link_origem=link_http(edital.link_origem) or link_reserva(edital.fonte, edital.id_na_fonte),
        linhas=edital.linhas,
    )


def detalhe_out(edital: EditalDetalhe) -> EditalDetalheOut:
    return EditalDetalheOut(
        **resumo_out(edital).model_dump(),
        orgao_cnpj=edital.orgao_cnpj,
        situacao_nome=edital.situacao_nome,
        situacao_normalizada=edital.situacao_normalizada,
        data_publicacao=_iso(edital.data_publicacao),
        data_abertura_proposta=_iso(edital.data_abertura_proposta),
    )


AVISO_ANALISE = (
    "Segundo o que foi extraído do edital por IA: exigência que a extração perdeu não aparece."
    " Confira cada item no trecho e na página indicados."
)


class ItemAnaliseOut(BaseModel):
    campo: str
    situacao: SituacaoItem
    exigido: str
    perfil: str
    motivo: str
    trecho: str | None
    # Título do documento (PDF) onde está o trecho e a página dentro dele.
    documento: str | None
    pagina: int | None


class DocumentoAnaliseOut(BaseModel):
    titulo: str
    url: str
    paginas: int | None
    status: StatusDocumento


class AnaliseOut(BaseModel):
    id_publico: str
    modelo: str
    versao_prompt: str
    criada_em: str | None
    veredito: Veredito
    incompleta: bool
    aviso: str
    itens: list[ItemAnaliseOut]
    documentos: list[DocumentoAnaliseOut]


def _item_out(item: dict[str, object], lida: AnaliseLida) -> ItemAnaliseOut:
    """A página gravada é a do texto juntado dos documentos; a API devolve a do PDF."""
    pagina = item.get("pagina")
    local = pagina_no_documento(lida.documentos, pagina if isinstance(pagina, int) else None)
    documento, no_pdf = (None, pagina) if local is None else (local[0].titulo, local[1])
    return ItemAnaliseOut.model_validate({**item, "documento": documento, "pagina": no_pdf})


def analise_out(lida: AnaliseLida) -> AnaliseOut:
    analise = lida.analise
    return AnaliseOut(
        id_publico=id_publico(analise.fonte, analise.id_na_fonte),
        modelo=analise.modelo,
        versao_prompt=analise.versao_prompt,
        criada_em=iso_com_fuso(analise.criada_em),
        veredito=analise.veredito,  # type: ignore[arg-type]
        incompleta=analise.incompleta,
        aviso=AVISO_ANALISE,
        itens=[_item_out(i, lida) for i in analise.comparacao.get("itens", [])],
        documentos=[
            DocumentoAnaliseOut(
                titulo=d.titulo,
                url=d.url,
                paginas=d.paginas,
                status=d.status,  # type: ignore[arg-type]
            )
            for d in lida.documentos
        ],
    )
