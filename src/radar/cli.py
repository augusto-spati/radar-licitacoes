"""Porta de entrada de terminal."""

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import typer
import uvicorn
from sqlalchemy.orm import Session

from radar import __version__
from radar.api.app import criar_app
from radar.coleta import (
    ClienteEducado,
    ClientePNCP,
    FonteRetrato,
    coletar_pendentes,
    criar_http,
    criar_http_educado,
    executar_retrato,
    ler_paginas_nao_processadas,
    limpar_brutos,
    marcar_processada,
    marcar_retrato_falha,
    paginas_do_retrato,
    planejar_janelas,
    trava_fonte,
    ultimo_retrato,
)
from radar.coleta import (
    auditar as auditar_cobertura,
)
from radar.comum import configurar_logs, criar_engine, fabrica_sessao, obter_config
from radar.editais import (
    Criterios,
    EditalResumo,
    carregar_criterios,
    reconciliar_retrato,
    registrar,
    registrar_normalizados,
)
from radar.editais import filtrar as filtrar_editais
from radar.processador import FRACAO_INVALIDOS, fontes_retrato

logger = logging.getLogger(__name__)

app = typer.Typer(help="Radar de licitações.", no_args_is_help=True)

FUSO = ZoneInfo("America/Sao_Paulo")
IDADE_MAXIMA_RETRATO = timedelta(hours=36)
# Padrão de RADAR_CONFIG_FILTRO (config/filtro.toml da raiz, sem depender do cwd).
ARQUIVO_CONFIG = obter_config().config_filtro

OpcaoConfig = Annotated[
    Path, typer.Option("--config", help="Arquivo de critérios (modalidades, UFs, linhas).")
]


def _agora() -> datetime:
    return datetime.now(FUSO)


def _criar_sessao() -> Session:
    return fabrica_sessao(criar_engine(obter_config()))()


def _criar_cliente() -> ClientePNCP:
    return ClientePNCP(criar_http())


def _criar_cliente_retrato(fonte: FonteRetrato) -> ClienteEducado:
    return ClienteEducado(criar_http_educado(), marcador_bloqueio=fonte.marcador_bloqueio)


def _escolher_fontes(opcao: str | None, disponiveis: list[str]) -> list[str]:
    """Fontes pedidas em `--fontes` (lista separada por vírgula), na ordem de `disponiveis`."""
    if opcao is None:
        return disponiveis
    pedidas = {nome.strip() for nome in opcao.split(",") if nome.strip()}
    desconhecidas = sorted(pedidas - set(disponiveis))
    if desconhecidas or not pedidas:
        typer.echo(
            f"fontes desconhecidas: {', '.join(desconhecidas) or '(nenhuma)'}; "
            f"use {', '.join(disponiveis)}",
            err=True,
        )
        raise typer.Exit(code=2)
    return [nome for nome in disponiveis if nome in pedidas]


def _extrair_do_retrato(
    sessao: Session, fonte: FonteRetrato, retrato_id: int
) -> tuple[list[dict[str, Any]], set[str]]:
    """Normalizados e ids listados, relidos das páginas brutas do retrato.

    TODO Task 5: ponte temporária; o registro síncrono sai da cli e vai para o consumidor.
    """
    normalizados = fonte.extrair(paginas_do_retrato(sessao, retrato_id))
    ids = {str(n["id_na_fonte"]) for n in normalizados if n.get("id_na_fonte") not in (None, "")}
    return normalizados, ids


def _rodar_retrato(sessao: Session, fonte: FonteRetrato) -> int:
    """Retrato de uma fonte; devolve 1 se falhou. Falha de uma fonte não para as outras."""
    retrato_id: int | None = None
    try:
        # O lock da fonte cobre o retrato e o registro: o ciclo inteiro não se sobrepõe.
        with trava_fonte(sessao, fonte.nome) as obtida:
            if not obtida:
                logger.warning("retrato_em_andamento_noutro_processo", extra={"fonte": fonte.nome})
                typer.echo(f"retrato {fonte.nome}: já em andamento noutro processo")
                return 0
            with _criar_cliente_retrato(fonte) as cliente:
                resumo = executar_retrato(sessao, fonte, cliente, _agora(), trava_mantida=True)
            retrato_id = resumo.retrato_id
            if resumo.status != "concluido":
                typer.echo(f"retrato {fonte.nome}: falha: {resumo.erro}")
                return 1
            # Fronteira coleta -> editais: gravar e reconciliar numa transação só.
            assert retrato_id is not None  # concluido sempre tem id
            normalizados, vistos = _extrair_do_retrato(sessao, fonte, retrato_id)  # TODO Task 5
            resultado = registrar_normalizados(sessao, normalizados)
            if resultado.invalidos > FRACAO_INVALIDOS * resumo.registros:
                sessao.rollback()  # desfaz o registro: nada gravado, nada reconciliado
                erro = f"muitos inválidos: {resultado.invalidos} de {resumo.registros}"
                logger.warning(
                    "retrato_muitos_invalidos",
                    extra={"fonte": fonte.nome, "retrato_id": retrato_id, "erro": erro},
                )
                marcar_retrato_falha(sessao, retrato_id, erro)
                typer.echo(f"retrato {fonte.nome}: falha: {erro}")
                return 1
            if vistos:
                encerrados = reconciliar_retrato(
                    sessao, fonte.nome, vistos, _agora(), rotulo=fonte.rotulo_saida
                )
            else:
                # Defesa extra: com `vistos` vazio a reconciliação encerraria a fonte inteira.
                logger.warning(
                    "reconciliacao_ignorada", extra={"fonte": fonte.nome, "retrato_id": retrato_id}
                )
                encerrados = 0
            sessao.commit()
            _limpar_brutos(sessao, fonte.nome)
    except Exception as erro:  # Ctrl+C (BaseException) continua parando o processo
        sessao.rollback()
        logger.exception("retrato_erro_registro", extra={"fonte": fonte.nome})
        if retrato_id is not None:
            # O retrato concluiu, mas os editais não foram gravados: para a auditoria, falhou.
            try:
                marcar_retrato_falha(sessao, retrato_id, f"registro: {erro!r}")
            except Exception:
                sessao.rollback()
                logger.exception("retrato_erro_marcar_falha", extra={"retrato_id": retrato_id})
        typer.echo(f"retrato {fonte.nome}: erro inesperado (veja o log)")
        return 1
    logger.info(
        "retrato_registrado",
        extra={
            "fonte": fonte.nome,
            "retrato_id": resumo.retrato_id,
            "inseridos": resultado.inseridos,
            "atualizados": resultado.atualizados,
            "invalidos": resultado.invalidos,
            "encerrados": encerrados,
        },
    )
    typer.echo(
        f"retrato {fonte.nome}: {resumo.registros} registros, {resultado.inseridos} inseridos, "
        f"{resultado.atualizados} atualizados, {resultado.invalidos} inválidos, "
        f"{encerrados} encerrados"
    )
    return 0


def _limpar_brutos(sessao: Session, fonte: str) -> None:
    """Retenção das páginas brutas da fonte. Falhar aqui não desfaz o retrato já gravado."""
    try:
        limpar_brutos(sessao, fonte)
    except Exception:
        sessao.rollback()
        logger.exception("brutos_erro_limpeza", extra={"fonte": fonte})


def _rodar_pncp(sessao: Session, criterios: Criterios, hoje: date, lookback: int) -> int:
    """Janelas do PNCP; devolve as falhas. Um erro inesperado não impede os retratos."""
    try:
        novas = planejar_janelas(
            sessao, "atualizacao", hoje - timedelta(days=lookback), hoje, criterios.modalidades
        )
        typer.echo(f"janelas novas: {novas}")
        return _processar(sessao, _criar_cliente())
    except Exception:  # Ctrl+C (BaseException) continua parando o processo
        sessao.rollback()
        logger.exception("pncp_erro_inesperado")
        typer.echo("pncp: erro inesperado (veja o log)")
        return 1


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
    fontes: Annotated[
        str | None,
        typer.Option(
            "--fontes", help="Fontes separadas por vírgula, ex.: pncp,sanepar (padrão: todas)."
        ),
    ] = None,
) -> None:
    """Atualiza o PNCP (janelas do recuo até hoje) e lê o retrato das outras fontes."""
    retratos = fontes_retrato()
    escolhidas = _escolher_fontes(fontes, ["pncp", *retratos])
    configurar_logs()
    criterios = carregar_criterios(config)
    hoje = _agora().date()
    falhas = 0
    with _criar_sessao() as sessao:
        if "pncp" in escolhidas:
            falhas += _rodar_pncp(sessao, criterios, hoje, lookback)
        for nome in escolhidas:
            if nome != "pncp":
                falhas += _rodar_retrato(sessao, retratos[nome])
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
    """Confere a cobertura do PNCP e o último retrato de cada fonte (falha ou mais de 36 h)."""
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
    agora = _agora()
    with _criar_sessao() as sessao:
        auditoria = auditar_cobertura(sessao, endpoint, inicio, fim, criterios.modalidades)
        retratos = [(f.nome, ultimo_retrato(sessao, f.nome)) for f in fontes_retrato().values()]
    typer.echo(f"esperadas: {auditoria.esperadas}")
    typer.echo(f"concluidas: {auditoria.concluidas}")
    typer.echo(f"faltando: {len(auditoria.faltando)}")
    for dia, modalidade in auditoria.faltando:
        typer.echo(f"  {dia:%Y-%m-%d} modalidade {modalidade}")
    typer.echo(f"falhas: {len(auditoria.falhas)}")
    for dia, modalidade, erro in auditoria.falhas:
        typer.echo(f"  {dia:%Y-%m-%d} modalidade {modalidade}: {erro}")
    retratos_ruins = 0
    for nome, info in retratos:
        if info is None:
            typer.echo(f"retrato {nome}: nenhum")
            retratos_ruins += 1
            continue
        linha = (
            f"retrato {nome}: {info.status} em {info.iniciado_em.astimezone(FUSO):%Y-%m-%d %H:%M}"
            f", {info.registros} registros"
        )
        if info.status != "concluido":
            linha += f": {info.erro}"
            retratos_ruins += 1
        elif agora - info.iniciado_em > IDADE_MAXIMA_RETRATO:
            linha += " (mais de 36 h)"
            retratos_ruins += 1
        typer.echo(linha)
    if auditoria.faltando or auditoria.falhas or retratos_ruins:
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


@app.command()
def api(
    porta: Annotated[int, typer.Option(min=1, max=65535, help="Porta local da API.")] = 8000,
    config: OpcaoConfig = ARQUIVO_CONFIG,
) -> None:
    """Sobe a API JSON local (só leitura) em 127.0.0.1."""
    configurar_logs()
    uvicorn.run(criar_app(config_filtro=config), host="127.0.0.1", port=porta)
