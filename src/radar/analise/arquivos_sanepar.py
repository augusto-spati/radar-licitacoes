"""Documentos de um edital da Sanepar: os anexos que a coleta já guardou em `edital.bruto`.

A coleta (`radar.coleta.sanepar`) lê os anexos no detalhe de cada edital e os grava em
`bruto["detalhe"]["anexos"]` como `{"nome", "id"}`. Dois formatos aparecem:

- `/licitacao/arquivo/<id numérico>`: um arquivo só, em geral o PDF do edital
  ("EDITAL LE 345 2026" responde `application/pdf`);
- `/licitacao/documento?nome=<arquivo>.zip`: o pacote "EDITAL_ELEMENTOS.ZIP" (projeto,
  planilhas). Fica de fora: abrir ZIP pede limite de tamanho descompactado e de número de
  arquivos, e o edital principal vem em PDF separado.

O download usa o mesmo cliente do PNCP, preso a https://www.sanepar.com.br e com pausa maior.
"""

from typing import Any

import httpx

from radar.analise.arquivos_pncp import (
    MAX_DOCUMENTOS,
    TIMEOUT_SEGUNDOS,
    USER_AGENT,
    ArquivoPNCP,
    ClienteArquivosPNCP,
)
from radar.analise.normalizacao import sem_acento

BASE_SANEPAR = "https://www.sanepar.com.br"
ORIGEM_SANEPAR = ("https", "www.sanepar.com.br")
# Mesma cortesia da coleta do portal: um site institucional, não uma API.
PAUSA_SANEPAR_S = 2.0
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
    http = httpx.Client(
        base_url=BASE_SANEPAR,
        timeout=TIMEOUT_SEGUNDOS,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
    )
    return ClienteArquivosPNCP(http, pausa_s=PAUSA_SANEPAR_S, origem=ORIGEM_SANEPAR)
