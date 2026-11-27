"""Documentos de um edital da Sanepar: os anexos que a coleta já guardou em `edital.bruto`.

A coleta (`radar.coleta.sanepar`) lê os anexos no detalhe de cada edital e os grava em
`bruto["detalhe"]["anexos"]` como `{"nome", "id"}`. Dois formatos aparecem:

- `/licitacao/arquivo/<id numérico>`: um arquivo só, em geral o PDF do edital
  ("EDITAL LE 345 2026" responde `application/pdf`);
- `/licitacao/documento?nome=<arquivo>.zip`: o pacote "EDITAL_ELEMENTOS.ZIP" (10 MB na
  LE 345/2026), com o termo de referência ("274331_Termo_Ref_Deteccao_Vazamento...pdf") e a
  "Resolução_Habilitação_Econômico_Financeira.pdf" (capital e patrimônio mínimos). Do ZIP saem,
  pela escolha comum de `pacotes`, os termos de referência e a habilitação (e o edital, se não
  veio PDF direto), até completar 3 documentos. Falha no ZIP não derruba o edital: fica o PDF.

O download usa o mesmo cliente do PNCP, preso a https://www.sanepar.com.br e com pausa maior.
"""

import logging
from typing import Any
from urllib.parse import quote

import httpx

from radar.analise.arquivos_pncp import (
    MAX_DOCUMENTOS,
    TIMEOUT_SEGUNDOS,
    USER_AGENT,
    ArquivoPNCP,
    ClienteArquivosPNCP,
    ErroArquivos,
    GrandeDemais,
)
from radar.analise.normalizacao import sem_acento
from radar.analise.pacotes import base_do_nome, escolher_no_pacote, legivel, ler_entrada, ler_pacote

logger = logging.getLogger(__name__)

BASE_SANEPAR = "https://www.sanepar.com.br"
ORIGEM_SANEPAR = ("https", "www.sanepar.com.br")
# Mesma cortesia da coleta do portal: um site institucional, não uma API.
PAUSA_SANEPAR_S = 2.0
LIMITE_ZIP_SANEPAR = 60_000_000
_NOMES_ANALISADOS = ("edital", "termo de referencia")
_NOMES_FORA = ("elementos", "errata", "esclarecimento", "resposta", "ata ", "aviso")


def _anexos(bruto: dict[str, Any] | None) -> list[dict[str, Any]]:
    detalhe = (bruto or {}).get("detalhe")
    anexos = detalhe.get("anexos") if isinstance(detalhe, dict) else None
    return [a for a in anexos if isinstance(a, dict)] if isinstance(anexos, list) else []


def arquivos_sanepar(bruto: dict[str, Any] | None) -> list[ArquivoPNCP]:
    """Edital ou termo de referência com id numérico, até 3, na ordem do id."""
    escolhidos: list[ArquivoPNCP] = []
    for anexo in _anexos(bruto):
        nome = str(anexo.get("nome") or "").strip()
        ident = str(anexo.get("id") or "")
        normalizado = f"{sem_acento(nome)} "
        if not ident.isdigit():
            continue  # pacote ZIP por nome de arquivo
        if not any(t in normalizado for t in _NOMES_ANALISADOS):
            continue
        if any(t in normalizado for t in _NOMES_FORA):
            continue
        escolhidos.append(
            ArquivoPNCP(
                sequencial=int(ident),
                titulo=nome,
                tipo_nome="Edital",
                url=f"{BASE_SANEPAR}/licitacao/arquivo/{ident}",
            )
        )
    return sorted(escolhidos, key=lambda a: a.sequencial)[:MAX_DOCUMENTOS]


def criar_cliente_sanepar() -> ClienteArquivosPNCP:
    """O cliente HTTP da Sanepar; a análise o usa dentro de `DocumentosSanepar`."""
    http = httpx.Client(
        base_url=BASE_SANEPAR,
        timeout=TIMEOUT_SEGUNDOS,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
    )
    return ClienteArquivosPNCP(http, pausa_s=PAUSA_SANEPAR_S, origem=ORIGEM_SANEPAR)


def pacotes_sanepar(bruto: dict[str, Any] | None) -> list[str]:
    """URLs dos ZIP anexados (o id é o nome do arquivo), na ordem da página."""
    return [
        f"{BASE_SANEPAR}/licitacao/documento?nome={quote(str(a.get('id')))}"
        for a in _anexos(bruto)
        if str(a.get("id") or "").lower().endswith(".zip")
    ]


class DocumentosSanepar:
    """PDF direto do edital mais o que o ZIP de elementos tiver de TR e habilitação."""

    def __init__(self, cliente: ClienteArquivosPNCP) -> None:
        self._cliente = cliente
        self._baixados: dict[str, bytes] = {}

    def fechar(self) -> None:
        self._cliente.fechar()

    def baixar(self, url: str) -> bytes:
        if url in self._baixados:
            return self._baixados[url]
        return self._cliente.baixar(url)

    def listar(self, bruto: dict[str, Any] | None) -> list[ArquivoPNCP]:
        self._baixados.clear()  # só os documentos do edital atual ficam na memória
        arquivos = arquivos_sanepar(bruto)
        tem_edital = bool(arquivos)
        for url_zip in pacotes_sanepar(bruto):
            vagas = MAX_DOCUMENTOS - len(arquivos)
            if vagas <= 0:
                break
            try:
                pacote = ler_pacote(self._cliente.baixar(url_zip, LIMITE_ZIP_SANEPAR))
            except (ErroArquivos, GrandeDemais) as erro:
                logger.warning("sanepar_zip_falhou", extra={"url": url_zip, "erro": str(erro)})
                continue
            if pacote is None:
                continue
            escolhidos = [
                e
                for e in escolher_no_pacote(pacote.namelist(), limite=len(pacote.namelist()))
                if not (tem_edital and e.tipo == "Edital")
            ][:vagas]
            for escolhido in escolhidos:
                url = f"{url_zip}#{escolhido.nome}"
                try:
                    self._baixados[url] = ler_entrada(pacote, escolhido.nome)
                except GrandeDemais:
                    continue
                arquivos.append(
                    ArquivoPNCP(
                        # Os ids de anexo da Sanepar são grandes (221507); 1, 2, 3... não colidem.
                        sequencial=len(arquivos) + 1,
                        titulo=base_do_nome(legivel(escolhido.nome)),
                        tipo_nome=escolhido.tipo,
                        url=url,
                    )
                )
        return arquivos
