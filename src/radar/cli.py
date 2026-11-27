"""Porta de entrada de terminal."""

import functools
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4
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
    frescor_por_fonte,
    janelas_com_lease_vivo,
    janelas_paradas,
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
    Mensagem,
    configurar_logs,
    criar_engine,
    fabrica_sessao,
    limpar_outbox,
    metricas,
    obter_config,
    pendencias_outbox,
    publicar_outbox,
    regiao_da_url,
)
from radar.comum.telemetria import telemetria_do_processo
from radar.editais import Criterios, EditalResumo, carregar_criterios
from radar.editais import filtrar as filtrar_editais
from radar.processador import (
    FilaLocal,
    criar_fila,
    fontes_retrato,
    planejar_e_enfileirar,
    processar_janela,
)

logger = logging.getLogger(__name__)

app = typer.Typer(help="Radar de licitações.", no_args_is_help=True)

FUSO = ZoneInfo("America/Sao_Paulo")
IDADE_MAXIMA_RETRATO = timedelta(hours=36)
# Mensagem publicada e página ainda parada depois disto: o consumidor não está dando conta.
ATRASO_MAXIMO_FILA = timedelta(hours=1)
# Janela em_andamento começada antes disto: a cadeia de fatias parou (a coleta leva ~2 h).
IDADE_MAXIMA_JANELA = timedelta(hours=6)
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
    return ClienteEducado(
        criar_http_educado(), marcador_bloqueio=fonte.marcador_bloqueio, fonte=fonte.nome
    )


def _criar_cliente_sqs(url: str) -> Any:
    import boto3  # type: ignore[import-untyped]

    return boto3.client("sqs", region_name=regiao_da_url(url))


def _coletora_local(sessao: Session, mensagem: Mensagem) -> str:
    """`coletar_janela` na fila local: a fatia vai até o fim da janela (sem orçamento)."""
    return processar_janela(sessao, mensagem, _criar_cliente(), uuid4().hex, None).desfecho


def _criar_fila(fontes: dict[str, FonteRetrato], coletar_janelas: bool = False) -> Fila:
    """A fila de `RADAR_FILA`: local (consome aqui mesmo) ou SQS (roteada por tipo).

    `coletar_janelas`: a fila local também coleta as `coletar_janela` (o `planejar` local).
    """
    config = obter_config()
    if config.fila == "sqs" and not (config.fila_url and config.fila_janelas_url):
        typer.echo("RADAR_FILA=sqs exige RADAR_FILA_URL e RADAR_FILA_JANELAS_URL", err=True)
        raise typer.Exit(code=2)
    cliente = None
    if config.fila == "sqs" and config.fila_url:
        cliente = _criar_cliente_sqs(config.fila_url)
    coletora = _coletora_local if coletar_janelas else None
    return criar_fila(config, _criar_sessao, fontes, _agora, cliente, coletora)


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
    """Relay da outbox para a fila; devolve as recusas (erro inesperado conta 1).

    Com a fila local, publica só os tipos que ela consome neste comando: uma
    `coletar_janela` (da Lambda coletora) fica pendente, intocada, fora do `planejar`.
    """
    tipos = fila.tipos if isinstance(fila, FilaLocal) else None
    try:
        resumo = publicar_outbox(sessao, fila, agora=_agora, tipos=tipos)
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


def _com_fila(sessao: Session, fila: Fila, coletar: Callable[[], int]) -> tuple[int, int, int]:
    """Relay, coleta, relay; devolve as falhas (coleta, relay, fila) para `_encerrar`.

    O relay do começo recupera o que ficou na outbox se a execução anterior morreu entre
    coletar e publicar. O do fim roda depois de todas as coletas soltarem a trava da fonte
    (o consumidor local precisa dela).
    """
    relay = _publicar(sessao, fila)
    coleta = coletar()
    relay += _publicar(sessao, fila)
    return coleta, relay, _falhas_da_fila(fila)


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


def _recusar_pncp_com_lease_vivo() -> None:
    """A coleta local do PNCP não roda enquanto alguém tem o lease de uma janela.

    Lease vivo é a Lambda coletora (ou outro processo) coletando agora: o PNCP local não
    deve rodar contra o banco de produção, onde a cadeia de fatias é da Lambda.
    """
    with _criar_sessao() as sessao:
        vivas = janelas_com_lease_vivo(sessao)
    if vivas:
        typer.echo(
            f"pncp: {vivas} janelas com lease vivo (a Lambda coletora ou outro processo está"
            " coletando); a coleta local do PNCP não roda contra o banco de produção."
            " Use --fontes sem pncp ou espere o lease vencer.",
            err=True,
        )
        raise typer.Exit(code=2)


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


def _medir_frescor(sessao: Session, fontes: list[str]) -> None:
    """`FrescorHoras` de cada fonte (a fonte é propriedade, não dimensão) e o log por fonte.

    Medir nunca derruba a execução: erro vira log e a sessão volta limpa.
    """
    try:
        horas = frescor_por_fonte(sessao, _agora(), fontes)
        sessao.rollback()  # só leitura: não deixa transação aberta durante a coleta
    except Exception:
        sessao.rollback()
        logger.exception("frescor_erro")
        return
    for fonte, valor in horas.items():
        logger.info("frescor_medido", extra={"fonte": fonte, "horas": valor})
        metricas.registrar("FrescorHoras", valor, propriedades={"fonte": fonte})


def _encerrar(coleta: int = 0, relay: int = 0, fila: int = 0) -> None:
    """Imprime a única linha `resultado:` da execução, por último, e sai com 1 se houve falha.

    A linha é contada pelo filtro de log `radar-coletor-execucoes` em infra/prod/coletor.tf
    (alarme `radar-coletor-nao-rodou`); não a remova.
    """
    total = coleta + relay + fila
    typer.echo(
        f"resultado: {total} falhas (coleta {coleta}, relay {relay}, fila {fila})"
        if total
        else "resultado: sem falhas"
    )
    if total:
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
    """Planeja janelas de publicação dos últimos dias, coleta e publica na fila.

    Coleta o PNCP aqui mesmo: não rode contra o banco de produção (sai com 2 se alguma
    janela tem lease vivo).
    """
    configurar_logs()
    criterios = carregar_criterios(config)
    _recusar_pncp_com_lease_vivo()
    hoje = _agora().date()
    fila = _criar_fila(fontes_retrato())
    with telemetria_do_processo("radar-coletor"), _criar_sessao() as sessao:

        def coletar() -> int:
            novas = planejar_janelas(
                sessao, "publicacao", hoje - timedelta(days=dias), hoje - timedelta(days=1),
                criterios.modalidades,
            )  # fmt: skip
            typer.echo(f"janelas novas: {novas}")
            return _coletar(sessao, _criar_cliente())

        falhas = _com_fila(sessao, fila, coletar)
    _encerrar(*falhas)


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
    """Atualiza o PNCP (janelas do recuo até hoje) e lê o retrato das outras fontes.

    O PNCP é coletado aqui mesmo (laço local): não o inclua contra o banco de produção, onde
    quem coleta é a Lambda (`radar planejar`). Com o PNCP incluído e alguma janela com lease
    vivo, sai com 2 sem coletar nada.
    """
    retratos = fontes_retrato()
    escolhidas = _escolher_fontes(fontes, ["pncp", *retratos])
    configurar_logs()
    criterios = carregar_criterios(config)
    if "pncp" in escolhidas:
        _recusar_pncp_com_lease_vivo()
    hoje = _agora().date()
    fila = _criar_fila(retratos)
    # Frescor do PNCP sempre (quem coleta é a Lambda; medir daqui não depende dela) e das
    # fontes de retrato escolhidas: uma fonte fora da nuvem (a Compesa) mediria 9999 h todo
    # dia e o alarme de frescor dispararia sem nada errado.
    medidas = ["pncp", *(nome for nome in escolhidas if nome != "pncp")]
    with telemetria_do_processo("radar-coletor"):
        ok = False
        try:
            with _criar_sessao() as sessao:

                def coletar() -> int:
                    falhas = 0
                    if "pncp" in escolhidas:
                        falhas += _rodar_pncp(sessao, criterios, hoje, lookback)
                    for nome in escolhidas:
                        if nome != "pncp":
                            falhas += _rodar_retrato(sessao, retratos[nome])
                    return falhas

                _medir_frescor(sessao, medidas)
                falhas = _com_fila(sessao, fila, coletar)
                _medir_frescor(sessao, medidas)
            ok = not sum(falhas)
        finally:
            metricas.registrar("ExecucaoOk", 1 if ok else 0)
    _encerrar(*falhas)


@app.command()
def processar() -> None:
    """Coleta as janelas pendentes e publica a outbox na fila (o consumidor registra).

    Coleta o PNCP aqui mesmo: sai com 2 se alguma janela tem lease vivo.
    """
    configurar_logs()
    _recusar_pncp_com_lease_vivo()
    fila = _criar_fila(fontes_retrato())
    with telemetria_do_processo("radar-coletor"), _criar_sessao() as sessao:
        falhas = _com_fila(sessao, fila, lambda: _coletar(sessao, _criar_cliente()))
    _encerrar(*falhas)


@app.command()
def publicar() -> None:
    """Publica a outbox pendente na fila e apaga as linhas publicadas há mais de 7 dias."""
    configurar_logs()
    fila = _criar_fila(fontes_retrato())
    with _criar_sessao() as sessao:
        falhas = _publicar(sessao, fila)
        apagadas = limpar_outbox(sessao, _agora())
    typer.echo(f"outbox: {apagadas} publicadas antigas apagadas")
    _encerrar(relay=falhas, fila=_falhas_da_fila(fila))


@app.command()
def planejar(
    recuo: Annotated[int, typer.Option(min=0, help="Dias de recuo até hoje.")] = 10,
    config: OpcaoConfig = ARQUIVO_CONFIG,
) -> None:
    """Planeja as janelas de atualização do PNCP e enfileira a coleta (como o planejador).

    Com a fila local, a cadeia inteira de fatias roda aqui mesmo, janela por janela.
    """
    configurar_logs()
    criterios = carregar_criterios(config)
    fila = _criar_fila(fontes_retrato(), coletar_janelas=True)
    with _criar_sessao() as sessao:
        novas, enfileiradas = planejar_e_enfileirar(sessao, criterios, _agora().date(), recuo)
        typer.echo(f"janelas novas: {novas}, enfileiradas: {enfileiradas}")
        falhas = _publicar(sessao, fila)
    _encerrar(relay=falhas, fila=_falhas_da_fila(fila))


def _atributos_da_fila(cliente: Any, url: str, *nomes: str) -> dict[str, str]:
    resposta = cliente.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn", *nomes])
    return dict(resposta["Attributes"])


@app.command("reprocessar-falhas")
def reprocessar_falhas(
    fila: Annotated[
        str, typer.Option("--fila", help="editais (radar-editais) ou janelas (radar-janelas).")
    ] = "editais",
) -> None:
    """Devolve as mensagens da fila de falhas (DLQ) para a fila principal. Só com SQS."""
    if fila not in ("editais", "janelas"):
        typer.echo(f"fila inválida: {fila!r}; use editais ou janelas", err=True)
        raise typer.Exit(code=2)
    configurar_logs()
    config = obter_config()
    if config.fila != "sqs":
        typer.echo(
            "reprocessar-falhas só funciona com RADAR_FILA=sqs (a fila local não tem DLQ)",
            err=True,
        )
        raise typer.Exit(code=2)
    if fila == "janelas":
        principal, dlq_url = config.fila_janelas_url, config.fila_janelas_falhas_url
        variaveis = "RADAR_FILA_JANELAS_URL e RADAR_FILA_JANELAS_FALHAS_URL"
    else:
        principal, dlq_url = config.fila_url, config.fila_falhas_url
        variaveis = "RADAR_FILA_URL e RADAR_FILA_FALHAS_URL"
    if not principal or not dlq_url:
        typer.echo(f"defina {variaveis}", err=True)
        raise typer.Exit(code=2)
    cliente = _criar_cliente_sqs(principal)
    dlq = _atributos_da_fila(cliente, dlq_url, "ApproximateNumberOfMessages")
    quantas = int(dlq.get("ApproximateNumberOfMessages", "0"))
    if quantas == 0:
        typer.echo("nenhuma mensagem na fila de falhas")
        return
    destino = _atributos_da_fila(cliente, principal)["QueueArn"]
    # Uma coletar_janela devolvida traz o lote dela: se a janela já andou, sai obsoleta.
    handle = FilaSQS(principal, cliente).mover_falhas(dlq["QueueArn"], destino)
    typer.echo(f"{quantas} mensagens sendo movidas para a fila principal (tarefa {handle})")


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
    with telemetria_do_processo("radar-coletor"), _criar_sessao() as sessao:
        _medir_frescor(sessao, ["pncp", *fontes_retrato()])
        auditoria = auditar_cobertura(sessao, endpoint, inicio, fim, criterios.modalidades)
        retratos = [(f.nome, ultimo_retrato(sessao, f.nome)) for f in fontes_retrato().values()]
        pendentes, mais_velha = pendencias_outbox(sessao)
        paradas = paginas_publicadas_sem_processar(sessao, agora - ATRASO_MAXIMO_FILA)
        sem_processar = retratos_sem_processar(sessao)
        paradas_janela = janelas_paradas(sessao, agora - IDADE_MAXIMA_JANELA)
    typer.echo(f"esperadas: {auditoria.esperadas}")
    typer.echo(f"concluidas: {auditoria.concluidas}")
    typer.echo(f"faltando: {len(auditoria.faltando)}")
    for dia, modalidade in auditoria.faltando:
        typer.echo(f"  {dia:%Y-%m-%d} modalidade {modalidade}")
    typer.echo(f"falhas: {len(auditoria.falhas)}")
    for dia, modalidade, erro, proxima, total in auditoria.falhas:
        typer.echo(f"  {dia:%Y-%m-%d} modalidade {modalidade} ({_onde(proxima, total)}): {erro}")
    typer.echo(f"janelas em andamento há mais de 6 h: {len(paradas_janela)}")
    for parada in paradas_janela:
        typer.echo(
            f"  {parada.dia:%Y-%m-%d} modalidade {parada.modalidade}"
            f" ({_onde(parada.proxima_pagina, parada.total_paginas)})"
        )
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
    fila_parada = pendentes or paradas or sem_processar or paradas_janela
    if auditoria.faltando or auditoria.falhas or retratos_ruins or fila_parada:
        raise typer.Exit(code=1)


def _onde(proxima: int, total: int | None) -> str:
    """Onde a janela parou: a próxima página e o total (desconhecido antes da página 1)."""
    return f"página {proxima}/{total if total is not None else '?'}"


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
