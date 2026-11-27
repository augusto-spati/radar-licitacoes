"""Porta de entrada de terminal."""

import functools
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import typer
import uvicorn
from sqlalchemy.orm import Session, sessionmaker

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
    paginas_publicadas_sem_processar,
    planejar_janelas,
    retratos_sem_processar,
    ultimo_retrato,
)
from radar.coleta import (
    auditar as auditar_cobertura,
)
from radar.comum import (
    Fila,
    FilaSQS,
    configurar_logs,
    criar_engine,
    fabrica_sessao,
    limpar_outbox,
    obter_config,
    pendencias_outbox,
    publicar_outbox,
)
from radar.editais import Criterios, EditalResumo, carregar_criterios
from radar.editais import filtrar as filtrar_editais
from radar.processador import FilaLocal, criar_fila, fontes_retrato

logger = logging.getLogger(__name__)

app = typer.Typer(help="Radar de licitações.", no_args_is_help=True)

FUSO = ZoneInfo("America/Sao_Paulo")
IDADE_MAXIMA_RETRATO = timedelta(hours=36)
# Mensagem publicada e página ainda parada depois disto: o consumidor não está dando conta.
ATRASO_MAXIMO_FILA = timedelta(hours=1)
# Padrão de RADAR_CONFIG_FILTRO (config/filtro.toml da raiz, sem depender do cwd).
ARQUIVO_CONFIG = obter_config().config_filtro

OpcaoConfig = Annotated[
    Path, typer.Option("--config", help="Arquivo de critérios (modalidades, UFs, linhas).")
]


def _agora() -> datetime:
    return datetime.now(FUSO)


@functools.cache
def _fabrica() -> sessionmaker[Session]:
    """Um engine por processo: a fila local abre uma sessão por mensagem."""
    return fabrica_sessao(criar_engine(obter_config()))


def _criar_sessao() -> Session:
    return _fabrica()()


def _criar_cliente() -> ClientePNCP:
    return ClientePNCP(criar_http())


def _criar_cliente_retrato(fonte: FonteRetrato) -> ClienteEducado:
    return ClienteEducado(criar_http_educado(), marcador_bloqueio=fonte.marcador_bloqueio)


def _criar_cliente_sqs() -> Any:
    import boto3  # type: ignore[import-untyped]

    return boto3.client("sqs")


def _criar_fila(fontes: dict[str, FonteRetrato]) -> Fila:
    """A fila de `RADAR_FILA`: local (consome aqui mesmo) ou SQS."""
    config = obter_config()
    if config.fila == "sqs" and not config.fila_url:
        typer.echo("RADAR_FILA=sqs exige RADAR_FILA_URL", err=True)
        raise typer.Exit(code=2)
    cliente = _criar_cliente_sqs() if config.fila == "sqs" else None
    return criar_fila(config, _criar_sessao, fontes, _agora, cliente)


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


def _publicar(sessao: Session, fila: Fila) -> int:
    """Relay da outbox para a fila; devolve as recusas (erro inesperado conta 1)."""
    try:
        resumo = publicar_outbox(sessao, fila, agora=_agora)
    except Exception:  # a outbox continua pendente; a próxima execução publica
        sessao.rollback()
        logger.exception("relay_erro_inesperado")
        typer.echo("relay: erro inesperado (veja o log)")
        return 1
    typer.echo(f"relay: {resumo.publicadas} publicadas, {resumo.recusadas} recusadas")
    return resumo.recusadas


def _falhas_da_fila(fila: Fila) -> int:
    """Com a fila local, o que o consumidor não conseguiu também faz a execução falhar."""
    if not isinstance(fila, FilaLocal) or not fila.falhas:
        return 0
    typer.echo(f"fila local: {fila.falhas} mensagens com falha (veja o log)")
    return fila.falhas


def _com_fila(sessao: Session, fila: Fila, coletar: Callable[[], int]) -> int:
    """Relay, coleta, relay; devolve as falhas somadas.

    O relay do começo recupera o que ficou na outbox se a execução anterior morreu entre
    coletar e publicar. O do fim roda depois de todas as coletas soltarem a trava da fonte
    (o consumidor local precisa dela).
    """
    falhas = _publicar(sessao, fila)
    falhas += coletar()
    falhas += _publicar(sessao, fila)
    return falhas + _falhas_da_fila(fila)


def _rodar_retrato(sessao: Session, fonte: FonteRetrato) -> int:
    """Coleta o retrato de uma fonte (gravar é do consumidor); devolve 1 se falhou.

    Falha de uma fonte não para as outras. A trava da fonte vale durante a coleta.
    """
    try:
        with _criar_cliente_retrato(fonte) as cliente:
            resumo = executar_retrato(sessao, fonte, cliente, _agora())
    except Exception:  # Ctrl+C (BaseException) continua parando o processo
        sessao.rollback()
        logger.exception("retrato_erro_inesperado", extra={"fonte": fonte.nome})
        typer.echo(f"retrato {fonte.nome}: erro inesperado (veja o log)")
        return 1
    if resumo.status == "ocupado":
        typer.echo(f"retrato {fonte.nome}: já em andamento noutro processo")
        return 0
    if resumo.status != "concluido":
        typer.echo(f"retrato {fonte.nome}: falha: {resumo.erro}")
        return 1
    typer.echo(f"retrato {fonte.nome}: {resumo.registros} registros coletados")
    return 0


def _rodar_pncp(sessao: Session, criterios: Criterios, hoje: date, lookback: int) -> int:
    """Janelas do PNCP; devolve as falhas. Um erro inesperado não impede os retratos."""
    try:
        novas = planejar_janelas(
            sessao, "atualizacao", hoje - timedelta(days=lookback), hoje, criterios.modalidades
        )
        typer.echo(f"janelas novas: {novas}")
        return _coletar(sessao, _criar_cliente())
    except Exception:  # Ctrl+C (BaseException) continua parando o processo
        sessao.rollback()
        logger.exception("pncp_erro_inesperado")
        typer.echo("pncp: erro inesperado (veja o log)")
        return 1


def _coletar(sessao: Session, cliente: ClientePNCP) -> int:
    """Coleta as janelas pendentes; devolve quantas falharam. Cada página vai para a outbox."""
    resumo = coletar_pendentes(sessao, cliente, _agora())
    typer.echo(
        f"coleta: {resumo.concluidas} concluídas, {resumo.falhas} falhas, "
        f"{resumo.divididas} divididas, {resumo.registros} registros"
    )
    return resumo.falhas


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
    """Planeja janelas de publicação dos últimos dias, coleta e publica na fila."""
    configurar_logs()
    criterios = carregar_criterios(config)
    hoje = _agora().date()
    fila = _criar_fila(fontes_retrato())
    with _criar_sessao() as sessao:

        def coletar() -> int:
            novas = planejar_janelas(
                sessao, "publicacao", hoje - timedelta(days=dias), hoje - timedelta(days=1),
                criterios.modalidades,
            )  # fmt: skip
            typer.echo(f"janelas novas: {novas}")
            return _coletar(sessao, _criar_cliente())

        falhas = _com_fila(sessao, fila, coletar)
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
    fila = _criar_fila(retratos)
    with _criar_sessao() as sessao:

        def coletar() -> int:
            falhas = 0
            if "pncp" in escolhidas:
                falhas += _rodar_pncp(sessao, criterios, hoje, lookback)
            for nome in escolhidas:
                if nome != "pncp":
                    falhas += _rodar_retrato(sessao, retratos[nome])
            return falhas

        falhas = _com_fila(sessao, fila, coletar)
    _encerrar(falhas)


@app.command()
def processar() -> None:
    """Coleta as janelas pendentes e publica a outbox na fila (o consumidor registra)."""
    configurar_logs()
    fila = _criar_fila(fontes_retrato())
    with _criar_sessao() as sessao:
        falhas = _com_fila(sessao, fila, lambda: _coletar(sessao, _criar_cliente()))
    _encerrar(falhas)


@app.command()
def publicar() -> None:
    """Publica a outbox pendente na fila e apaga as linhas publicadas há mais de 7 dias."""
    configurar_logs()
    fila = _criar_fila(fontes_retrato())
    with _criar_sessao() as sessao:
        falhas = _publicar(sessao, fila)
        apagadas = limpar_outbox(sessao, _agora())
    typer.echo(f"outbox: {apagadas} publicadas antigas apagadas")
    _encerrar(falhas + _falhas_da_fila(fila))


def _arn_da_fila(cliente: Any, url: str) -> str:
    resposta = cliente.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])
    return str(resposta["Attributes"]["QueueArn"])


@app.command("reprocessar-falhas")
def reprocessar_falhas() -> None:
    """Devolve as mensagens da fila de falhas (DLQ) para a fila principal. Só com SQS."""
    configurar_logs()
    config = obter_config()
    if config.fila != "sqs":
        typer.echo(
            "reprocessar-falhas só funciona com RADAR_FILA=sqs (a fila local não tem DLQ)",
            err=True,
        )
        raise typer.Exit(code=2)
    if not config.fila_url or not config.fila_falhas_url:
        typer.echo("defina RADAR_FILA_URL e RADAR_FILA_FALHAS_URL", err=True)
        raise typer.Exit(code=2)
    cliente = _criar_cliente_sqs()
    origem = _arn_da_fila(cliente, config.fila_falhas_url)
    destino = _arn_da_fila(cliente, config.fila_url)
    handle = FilaSQS(config.fila_url, cliente).mover_falhas(origem, destino)
    typer.echo(f"reprocessar-falhas: movendo da fila de falhas para a principal, tarefa {handle}")


@app.command()
def auditar(
    endpoint: Annotated[str, typer.Option(help="publicacao ou atualizacao.")] = "publicacao",
    dias: Annotated[
        int | None,
        typer.Option(min=1, help="Janela auditada, em dias (padrão: 30; atualizacao: 10)."),
    ] = None,
    config: OpcaoConfig = ARQUIVO_CONFIG,
) -> None:
    """Confere a cobertura do PNCP, o último retrato de cada fonte e o que está parado na fila."""
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
        pendentes, mais_velha = pendencias_outbox(sessao)
        paradas = paginas_publicadas_sem_processar(sessao, agora - ATRASO_MAXIMO_FILA)
        sem_processar = retratos_sem_processar(sessao)
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
    linha = f"outbox pendente: {pendentes}"
    if mais_velha is not None:
        linha += f" (a mais velha de {mais_velha.astimezone(FUSO):%Y-%m-%d %H:%M})"
    typer.echo(linha)
    typer.echo(f"páginas publicadas sem processar há mais de 1 h: {paradas}")
    if retratos or sem_processar:  # sem fontes de retrato, a saída do PNCP não muda
        typer.echo(f"retratos concluídos sem processar: {sem_processar}")
    fila_parada = pendentes or paradas or sem_processar
    if auditoria.faltando or auditoria.falhas or retratos_ruins or fila_parada:
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
