"""Porta de entrada de terminal."""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import typer
from sqlalchemy.orm import Session

from radar import __version__
from radar.coleta import (
    ClientePNCP,
    coletar_pendentes,
    criar_http,
    ler_paginas_nao_processadas,
    marcar_processada,
    planejar_janelas,
)
from radar.coleta import (
    auditar as auditar_cobertura,
)
from radar.comum import configurar_logs, criar_engine, fabrica_sessao, obter_config
from radar.editais import EditalResumo, carregar_criterios, registrar
from radar.editais import filtrar as filtrar_editais

logger = logging.getLogger(__name__)

app = typer.Typer(help="Radar de licitações.", no_args_is_help=True)

FUSO = ZoneInfo("America/Sao_Paulo")
ARQUIVO_CONFIG = Path("config/filtro.toml")

OpcaoConfig = Annotated[
    Path, typer.Option("--config", help="Arquivo de critérios (modalidades, UFs, linhas).")
]


def _agora() -> datetime:
    return datetime.now(FUSO)


def _criar_sessao() -> Session:
    return fabrica_sessao(criar_engine(obter_config()))()


def _criar_cliente() -> ClientePNCP:
    return ClientePNCP(criar_http())


def _processar(sessao: Session, cliente: ClientePNCP) -> int:
    """Coleta e processa; devolve quantas janelas e páginas falharam nesta execução."""
    resumo = coletar_pendentes(sessao, cliente, _agora())
    typer.echo(
        f"coleta: {resumo.concluidas} concluídas, {resumo.falhas} falhas, "
        f"{resumo.divididas} divididas, {resumo.registros} registros"
    )
    paginas = inseridos = atualizados = ignorados = invalidos = 0
    falhas_pagina: set[int] = set()
    # Fronteira coleta -> editais: é aqui que a fila do Tema 2 entra.
    while lote := ler_paginas_nao_processadas(sessao, excluir=falhas_pagina):
        for pagina_id, registros in lote:
            try:
                resultado = registrar(sessao, registros)
                marcar_processada(sessao, pagina_id)  # commita o registro junto da marca
            except Exception:  # Ctrl+C (BaseException) continua parando o processo
                sessao.rollback()
                logger.exception("pagina_erro_processamento", extra={"pagina_id": pagina_id})
                falhas_pagina.add(pagina_id)
                continue
            paginas += 1
            inseridos += resultado.inseridos
            atualizados += resultado.atualizados
            ignorados += resultado.ignorados
            invalidos += resultado.invalidos
    logger.info(
        "processamento_concluido",
        extra={
            "paginas": paginas,
            "inseridos": inseridos,
            "atualizados": atualizados,
            "ignorados": ignorados,
            "invalidos": invalidos,
        },
    )
    typer.echo(
        f"processamento: {paginas} páginas, {inseridos} inseridos, {atualizados} atualizados, "
        f"{ignorados} ignorados, {invalidos} inválidos"
    )
    falhas = resumo.falhas + len(falhas_pagina)
    typer.echo(
        f"resultado: {resumo.falhas} janelas e {len(falhas_pagina)} páginas com falha"
        if falhas
        else "resultado: sem falhas"
    )
    return falhas


def _encerrar(falhas: int) -> None:
    if falhas:
        raise typer.Exit(code=1)


@app.command()
def versao() -> None:
    """Mostra a versão instalada."""
    typer.echo(__version__)


@app.command("carga-inicial")
def carga_inicial(
    dias: Annotated[int, typer.Option(min=1, help="Quantos dias anteriores a hoje coletar.")] = 30,
    config: OpcaoConfig = ARQUIVO_CONFIG,
) -> None:
    """Planeja janelas de publicação dos últimos dias, coleta e processa."""
    configurar_logs()
    criterios = carregar_criterios(config)
    hoje = _agora().date()
    with _criar_sessao() as sessao:
        novas = planejar_janelas(
            sessao, "publicacao", hoje - timedelta(days=dias), hoje - timedelta(days=1),
            criterios.modalidades,
        )  # fmt: skip
        typer.echo(f"janelas novas: {novas}")
        falhas = _processar(sessao, _criar_cliente())
    _encerrar(falhas)


@app.command()
def atualizar(
    lookback: Annotated[int, typer.Option(min=0, help="Dias de recuo até hoje.")] = 10,
    config: OpcaoConfig = ARQUIVO_CONFIG,
) -> None:
    """Planeja janelas de atualização de hoje menos o recuo até hoje, coleta e processa."""
    configurar_logs()
    criterios = carregar_criterios(config)
    hoje = _agora().date()
    with _criar_sessao() as sessao:
        novas = planejar_janelas(
            sessao, "atualizacao", hoje - timedelta(days=lookback), hoje, criterios.modalidades
        )
        typer.echo(f"janelas novas: {novas}")
        falhas = _processar(sessao, _criar_cliente())
    _encerrar(falhas)


@app.command()
def processar() -> None:
    """Coleta as janelas pendentes e registra os editais das páginas ainda não processadas."""
    configurar_logs()
    with _criar_sessao() as sessao:
        falhas = _processar(sessao, _criar_cliente())
    _encerrar(falhas)


@app.command()
def auditar(
    endpoint: Annotated[str, typer.Option(help="publicacao ou atualizacao.")] = "publicacao",
    dias: Annotated[
        int | None,
        typer.Option(min=1, help="Janela auditada, em dias (padrão: 30; atualizacao: 10)."),
    ] = None,
    config: OpcaoConfig = ARQUIVO_CONFIG,
) -> None:
    """Confere se todos os dias e modalidades esperados foram coletados."""
    if endpoint not in ("publicacao", "atualizacao"):
        typer.echo(f"endpoint inválido: {endpoint!r}; use publicacao ou atualizacao", err=True)
        raise typer.Exit(code=2)
    if dias is None:
        dias = 10 if endpoint == "atualizacao" else 30
    configurar_logs()
    criterios = carregar_criterios(config)
    hoje = _agora().date()
    fim = hoje if endpoint == "atualizacao" else hoje - timedelta(days=1)
    inicio = hoje - timedelta(days=dias)
    with _criar_sessao() as sessao:
        auditoria = auditar_cobertura(sessao, endpoint, inicio, fim, criterios.modalidades)
    typer.echo(f"esperadas: {auditoria.esperadas}")
    typer.echo(f"concluidas: {auditoria.concluidas}")
    typer.echo(f"faltando: {len(auditoria.faltando)}")
    for dia, modalidade in auditoria.faltando:
        typer.echo(f"  {dia:%Y-%m-%d} modalidade {modalidade}")
    typer.echo(f"falhas: {len(auditoria.falhas)}")
    for dia, modalidade, erro in auditoria.falhas:
        typer.echo(f"  {dia:%Y-%m-%d} modalidade {modalidade}: {erro}")
    if auditoria.faltando or auditoria.falhas:
        raise typer.Exit(code=1)


def _linha(edital: EditalResumo) -> str:
    encerramento = (
        f"{edital.data_encerramento_proposta:%d/%m %H:%M}"
        if edital.data_encerramento_proposta
        else "-"
    )
    objeto = edital.objeto.replace("\n", " ")[:80]
    return (
        f"{encerramento:<11}  {edital.uf or '-':<2}  {','.join(edital.linhas):<20}  "
        f"{edital.orgao_nome[:40]:<40}  {objeto}"
    )


@app.command()
def filtrar(
    todos: Annotated[bool, typer.Option("--todos", help="Inclui editais já encerrados.")] = False,
    config: OpcaoConfig = ARQUIVO_CONFIG,
) -> None:
    """Lista os editais que casam com os critérios."""
    configurar_logs()
    criterios = carregar_criterios(config)
    with _criar_sessao() as sessao:
        editais = filtrar_editais(sessao, criterios, _agora(), somente_abertos=not todos)
    typer.echo(f"{'ENCERRA':<11}  UF  {'LINHAS':<20}  {'ORGAO':<40}  OBJETO")
    for edital in editais:
        typer.echo(_linha(edital))
    typer.echo(f"{len(editais)} editais")
