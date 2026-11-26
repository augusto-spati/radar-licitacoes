"""Fixture de exemplo para o front: editais reais do banco, no formato do detalhe."""

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from radar.api.modelos import detalhe_out, iso_com_fuso
from radar.coleta import ultima_atualizacao
from radar.editais import Criterios, filtrar, listar_abertos, obter_edital


def exportar_exemplo(
    sessao: Session, criterios: Criterios, agora: datetime, maximo: int
) -> dict[str, Any]:
    """Abertos que casam com alguma linha; se faltar para `maximo`, completa com outros abertos
    (linhas vazias). Ordenados por prazo, como a lista da API."""
    escolhidos = [
        edital
        for resumo in filtrar(sessao, criterios, agora, limite=maximo)
        if (edital := obter_edital(sessao, resumo.fonte, resumo.id_na_fonte, criterios))
    ]
    if len(escolhidos) < maximo:
        presentes = {e.id_publico for e in escolhidos}
        outros = listar_abertos(sessao, criterios, agora, limite=maximo + len(presentes))
        escolhidos += [e for e in outros if e.id_publico not in presentes][
            : maximo - len(escolhidos)
        ]
    escolhidos.sort(key=lambda e: e.data_encerramento_proposta or datetime.max)
    return {
        "atualizado_em": iso_com_fuso(ultima_atualizacao(sessao)),
        "editais": [detalhe_out(e).model_dump(mode="json") for e in escolhidos],
    }
