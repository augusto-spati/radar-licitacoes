"""Consumidor idempotente das mensagens da outbox (entrega pelo menos uma vez).

Cada mensagem pode chegar repetida, fora de ordem ou em paralelo; o consumidor absorve:

- `pagina_coletada`: página já processada confirma e sai; senão `registrar` + marca, num
  commit só, com a página travada (`FOR UPDATE`) para duas entregas simultâneas não correrem.
- `retrato_coletado`, sob a trava da fonte: já processado confirma; rebaixado a falha é
  descartado; mais velho que outro já processado da fonte só ganha `processado_em`
  (reconciliar com ele reabriria ou encerraria editais errado); senão relê as páginas,
  grava, reconcilia e marca, numa transação.

Desfechos: `ok` (confirma), `falha_definitiva` (confirma também: repetir não muda nada; o log
`processamento_falhou` alimenta o alarme), `MensagemAdiada` (volta para a fila) e qualquer
outra exceção (nova tentativa e, depois de 5, a fila de falhas).
"""

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from radar.coleta import (
    EstadoRetrato,
    FonteRetrato,
    estado_retrato,
    ha_retrato_mais_novo_processado,
    ler_pagina_para_processar,
    limpar_brutos,
    marcar_processada,
    marcar_retrato_falha,
    marcar_retrato_processado,
    paginas_do_retrato,
    trava_fonte,
)
from radar.comum import Mensagem
from radar.editais import reconciliar_retrato, registrar, registrar_normalizados

logger = logging.getLogger(__name__)

# Acima disto, os inválidos não são editais esquisitos: é o parser lendo errado. O retrato falha.
FRACAO_INVALIDOS = 0.2

Desfecho = Literal["ok", "falha_definitiva"]


class MensagemAdiada(Exception):
    """A trava da fonte está ocupada (coleta ou outro consumidor): a mensagem deve voltar."""


def processar_mensagem(
    sessao: Session, mensagem: Mensagem, fontes: Mapping[str, FonteRetrato], agora: datetime
) -> Desfecho:
    try:
        if mensagem.tipo == "pagina_coletada":
            return _processar_pagina(sessao, mensagem.carga["pagina_id"])
        return _processar_retrato(sessao, mensagem.carga["retrato_id"], fontes, agora)
    except BaseException:
        sessao.rollback()  # a sessão sai limpa; a exceção segue para nova tentativa
        raise


def _processar_pagina(sessao: Session, pagina_id: int) -> Desfecho:
    pagina = ler_pagina_para_processar(sessao, pagina_id)
    if pagina is None:
        sessao.rollback()
        logger.info("pagina_inexistente", extra={"pagina_id": pagina_id})
        return "ok"
    if pagina.processada:
        sessao.rollback()
        logger.info("pagina_ja_processada", extra={"pagina_id": pagina_id})
        return "ok"
    resultado = registrar(sessao, pagina.registros)
    marcar_processada(sessao, pagina_id)  # commita o registro junto da marca
    logger.info(
        "pagina_processada",
        extra={
            "pagina_id": pagina_id,
            "inseridos": resultado.inseridos,
            "atualizados": resultado.atualizados,
            "ignorados": resultado.ignorados,
            "invalidos": resultado.invalidos,
        },
    )
    return "ok"


def _processar_retrato(
    sessao: Session, retrato_id: int, fontes: Mapping[str, FonteRetrato], agora: datetime
) -> Desfecho:
    antes = estado_retrato(sessao, retrato_id)
    sessao.rollback()  # a leitura antes da trava não deixa transação aberta
    if antes is None:
        logger.error("retrato_inexistente", extra={"retrato_id": retrato_id})
        return "ok"
    fonte = fontes[antes.fonte]  # fonte desconhecida é erro de deploy: nova tentativa
    with trava_fonte(sessao, fonte.nome) as obtida:
        if not obtida:
            logger.info("trava_ocupada", extra={"fonte": fonte.nome, "retrato_id": retrato_id})
            raise MensagemAdiada(f"trava da fonte {fonte.nome} ocupada")
        # Relido sob a trava: outro consumidor pode ter processado no intervalo.
        retrato = estado_retrato(sessao, retrato_id)
        if retrato is None:  # linhas de coleta_retrato não são apagadas
            raise RuntimeError(f"retrato {retrato_id} sumiu entre a leitura e a trava")
        desfecho = _sob_trava(sessao, retrato, fonte, agora)
    return desfecho


def _sob_trava(
    sessao: Session, retrato: EstadoRetrato, fonte: FonteRetrato, agora: datetime
) -> Desfecho:
    dados = {"fonte": fonte.nome, "retrato_id": retrato.id}
    if retrato.processado_em is not None:
        sessao.rollback()
        logger.info("retrato_ja_processado", extra=dados)
        return "ok"
    if retrato.status != "concluido":
        sessao.rollback()
        logger.warning("retrato_descartado", extra={**dados, "status": retrato.status})
        return "ok"
    if ha_retrato_mais_novo_processado(sessao, retrato):
        marcar_retrato_processado(sessao, retrato.id, agora)
        sessao.commit()
        logger.warning("retrato_obsoleto", extra=dados)
        _limpar_brutos(sessao, fonte.nome)
        return "ok"

    paginas = paginas_do_retrato(sessao, retrato.id)
    if not paginas:
        return _falhar(sessao, retrato.id, fonte.nome, "páginas ausentes")
    try:
        normalizados = fonte.extrair(paginas)
    except Exception as falha:  # parser quebrado: repetir não conserta (como na cli antiga)
        sessao.rollback()
        logger.exception("retrato_erro_extrair", extra=dados)
        return _falhar(sessao, retrato.id, fonte.nome, f"extrair: {falha!r}")
    vistos = {str(n["id_na_fonte"]) for n in normalizados if n.get("id_na_fonte") not in (None, "")}
    resultado = registrar_normalizados(sessao, normalizados)
    if resultado.invalidos > FRACAO_INVALIDOS * len(normalizados):
        sessao.rollback()  # desfaz o registro: nada gravado, nada reconciliado
        erro = f"muitos inválidos: {resultado.invalidos} de {len(normalizados)}"
        return _falhar(sessao, retrato.id, fonte.nome, erro)
    if vistos:
        encerrados = reconciliar_retrato(
            sessao, fonte.nome, vistos, agora, rotulo=fonte.rotulo_saida
        )
    else:
        # Defesa extra: com `vistos` vazio a reconciliação encerraria a fonte inteira.
        logger.warning("reconciliacao_ignorada", extra=dados)
        encerrados = 0
    marcar_retrato_processado(sessao, retrato.id, agora)
    sessao.commit()  # gravação + reconciliação + processado_em + páginas: tudo ou nada
    logger.info(
        "retrato_registrado",
        extra={
            **dados,
            "registros": len(normalizados),
            "inseridos": resultado.inseridos,
            "atualizados": resultado.atualizados,
            "invalidos": resultado.invalidos,
            "encerrados": encerrados,
        },
    )
    _limpar_brutos(sessao, fonte.nome)
    return "ok"


def _falhar(sessao: Session, retrato_id: int, fonte: str, erro: str) -> Desfecho:
    """Falha que repetir não conserta: rebaixa o retrato e confirma a mensagem, com alarme."""
    marcar_retrato_falha(sessao, retrato_id, erro)
    logger.error(
        "processamento_falhou", extra={"fonte": fonte, "retrato_id": retrato_id, "erro": erro}
    )
    return "falha_definitiva"


def _limpar_brutos(sessao: Session, fonte: str) -> None:
    """Retenção das páginas brutas da fonte. Falhar aqui não desfaz o retrato já gravado."""
    try:
        limpar_brutos(sessao, fonte)
    except Exception:
        sessao.rollback()
        logger.exception("brutos_erro_limpeza", extra={"fonte": fonte})
