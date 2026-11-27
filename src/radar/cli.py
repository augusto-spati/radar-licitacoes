"""Porta de entrada de terminal."""

import functools
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import typer
import uvicorn
from opentelemetry.context import Context
from opentelemetry.trace import StatusCode
from sqlalchemy.orm import Session, sessionmaker

from radar import __version__
from radar.coleta import (
    ClienteEducado,
    ClientePNCP,
    ClienteResultadosPNCP,
    ErroResultados,
    FonteRetrato,
    carregar_historico,
    coletar_pendentes,
    criar_http,
    criar_http_educado,
    criar_http_resultados,
    executar_retrato,
    frescor_por_fonte,
    janelas_com_lease_vivo,
    janelas_paradas,
    liberar_trava,
    meses_ate,
    obter_trava,
    paginas_publicadas_sem_processar,
    planejar_janelas,
    retratos_sem_processar,
    ultimo_retrato,
)
from radar.coleta import (
    auditar as auditar_cobertura,
)
from radar.comum import (
    RAIZ_REPOSITORIO,
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
from radar.comum.telemetria import telemetria_do_processo, tracer
from radar.editais import (
    Criterios,
    EditalResumo,
    alvos_resultado,
    bruto_do_edital,
    carregar_criterios,
    gravar_resultados,
    interpretar_itens,
    obter_edital,
    registrar,
    separar_id_publico,
)
from radar.editais import filtrar as filtrar_editais
from radar.processador import (
    FilaLocal,
    criar_fila,
    fontes_retrato,
    planejar_e_enfileirar,
    processar_janela,
)

# A análise (e a API, que a importa) só carrega nos comandos que a usam: o coletor e o relay
# não pagam o import nem quebram se algo da análise (prompt, config) estiver ruim.
if TYPE_CHECKING:
    from radar.analise import (
        AlvoAnalise,
        ClienteArquivosPNCP,
        ConfigIA,
        DocumentosCompesa,
        Provedor,
    )

logger = logging.getLogger(__name__)

app = typer.Typer(help="Radar de licitações.", no_args_is_help=True)
app_avaliar = typer.Typer(
    help="Evals da extração com IA (gabarito em evals/).", no_args_is_help=True
)
app.add_typer(app_avaliar, name="avaliar")
app_historico = typer.Typer(help="Carga histórica do PNCP.", no_args_is_help=True)
app.add_typer(app_historico, name="historico")
app_resultados = typer.Typer(help="Resultados das contratações (PNCP).", no_args_is_help=True)
app.add_typer(app_resultados, name="resultados")

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


def _criar_cliente_resultados() -> ClienteResultadosPNCP:
    return ClienteResultadosPNCP(criar_http_resultados())


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


def _medir_frescor(sessao: Session, fontes: list[str]) -> dict[str, float]:
    """`FrescorHoras` de cada fonte (a fonte é propriedade, não dimensão) e o log por fonte.

    Medir nunca derruba a execução: erro vira log, a sessão volta limpa e nada é medido.
    """
    horas = _frescor(sessao, fontes)
    for fonte, valor in horas.items():
        logger.info("frescor_medido", extra={"fonte": fonte, "horas": valor})
        metricas.registrar("FrescorHoras", valor, propriedades={"fonte": fonte})
    return horas


def _frescor(sessao: Session, fontes: list[str]) -> dict[str, float]:
    try:
        horas = frescor_por_fonte(sessao, _agora(), fontes)
        sessao.rollback()  # só leitura: não deixa transação aberta durante a coleta
    except Exception:
        sessao.rollback()
        logger.exception("frescor_erro")
        return {}
    return dict(horas)


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


def _sair_sem_resultado(falhas: int) -> None:
    """Sai com 1 se houve falha, sem a linha `resultado:` (só a coleta e o relay a imprimem).

    `analisar` roda na família do coletor e escreve no mesmo log group: um `resultado:` dele
    contaria como execução do coletor e calaria o alarme `radar-coletor-nao-rodou`.
    """
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
        # Manual: só imprime. A métrica (e o alarme) é do `atualizar` agendado; um auditar
        # avulso não deve disparar nem calar o alarme de frescor.
        frescor = _frescor(sessao, ["pncp", *fontes_retrato()])
        auditoria = auditar_cobertura(sessao, endpoint, inicio, fim, criterios.modalidades)
        retratos = [(f.nome, ultimo_retrato(sessao, f.nome)) for f in fontes_retrato().values()]
        pendentes, mais_velha = pendencias_outbox(sessao)
        paradas = paginas_publicadas_sem_processar(sessao, agora - ATRASO_MAXIMO_FILA)
        sem_processar = retratos_sem_processar(sessao)
        paradas_janela = janelas_paradas(sessao, agora - IDADE_MAXIMA_JANELA)
    for fonte, valor in frescor.items():
        typer.echo(f"frescor {fonte}: {valor:.1f} h")
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
    problemas = {
        "faltando": len(auditoria.faltando),
        "falhas": len(auditoria.falhas),
        "retratos_ruins": retratos_ruins,
        "outbox_pendente": pendentes,
        "paginas_paradas": paradas,
        "retratos_sem_processar": sem_processar,
        "janelas_paradas": len(paradas_janela),
    }
    if auditoria.faltando or auditoria.falhas or retratos_ruins or fila_parada:
        # O alarme "radar-auditoria-falhou" conta este evento (infra/prod/coletor.tf).
        logger.warning("auditoria_falhou", extra={"endpoint": endpoint, **problemas})
        raise typer.Exit(code=1)
    logger.info("auditoria_ok", extra={"endpoint": endpoint})


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


def _criar_provedor_ia(config: "ConfigIA") -> "Provedor":
    from radar.analise import ProvedorBedrock

    return ProvedorBedrock(config)


def _criar_documentos_compesa() -> "DocumentosCompesa":
    from radar.analise import criar_documentos_compesa

    return criar_documentos_compesa()


def _criar_cliente_sanepar() -> "ClienteArquivosPNCP":
    from radar.analise import criar_cliente_sanepar

    return criar_cliente_sanepar()


def _criar_cliente_arquivos() -> "ClienteArquivosPNCP":
    from radar.analise import ClienteArquivosPNCP, criar_http_arquivos

    return ClienteArquivosPNCP(criar_http_arquivos())


# Quantos editais abertos o `analisar` considera antes de cortar no limite da rodada.
CANDIDATOS_ANALISE = 1000
# Não começa edital novo depois disto (a agenda é diária; o resto fica para amanhã).
ORCAMENTO_RODADA_S = 30 * 60
# Lease em trava_fonte: uma rodada de análise por vez (agendada e manual não se sobrepõem).
# Renovado a cada edital; precisa cobrir um edital inteiro (até 3 downloads de 180 s, a
# extração e os pedidos ao Bedrock).
TRAVA_ANALISE = "analise"
DURACAO_TRAVA_ANALISE = timedelta(minutes=60)
_relogio_rodada = time.monotonic


# Vagas da rodada reservadas para a Sanepar: os editais de obra dela ficam abertos por semanas e,
# na ordem por encerramento, perderiam sempre para os pregões do PNCP que fecham antes.
RESERVA_SANEPAR = 3


def _candidatos_por_fonte(
    abertos: list[EditalResumo], fontes: tuple[str, ...]
) -> list[EditalResumo]:
    """Até RESERVA_SANEPAR da Sanepar primeiro, depois o resto das `fontes` por encerramento."""
    analisaveis = [e for e in abertos if e.fonte in fontes]
    reservados = [e for e in analisaveis if e.fonte == "sanepar"][:RESERVA_SANEPAR]
    ja = {(e.fonte, e.id_na_fonte) for e in reservados}
    return reservados + [e for e in analisaveis if (e.fonte, e.id_na_fonte) not in ja]


def _alvos_analise(
    sessao: Session,
    criterios: Criterios,
    edital: str | None,
    modelo: str,
    refazer: bool,
    limite: int,
    fontes: tuple[str, ...],
) -> tuple[list["AlvoAnalise"], int]:
    """Os editais a analisar e quantos foram pulados por já terem análise."""
    from radar.analise import AlvoAnalise, ja_analisado, versao_prompt_atual

    versao = versao_prompt_atual()
    if edital is not None:
        chave = separar_id_publico(edital)
        detalhe = None if chave is None else obter_edital(sessao, *chave, criterios)
        if detalhe is None:
            typer.echo(f"edital não encontrado: {edital}", err=True)
            raise typer.Exit(code=2)
        candidatos: list[EditalResumo] = [detalhe]
    else:
        abertos = filtrar_editais(
            sessao, criterios, _agora(), somente_abertos=True, limite=CANDIDATOS_ANALISE
        )
        candidatos = _candidatos_por_fonte(abertos, fontes)
    alvos: list[AlvoAnalise] = []
    pulados = 0
    for candidato in candidatos:
        if len(alvos) >= limite:
            break
        alvo = AlvoAnalise(candidato.fonte, candidato.id_na_fonte, candidato.valor_estimado)
        if not refazer and ja_analisado(sessao, alvo, modelo, versao):
            pulados += 1
            continue
        if alvo.fonte in ("sanepar", "compesa"):
            # Os documentos saem do bruto que a coleta guardou: anexos (Sanepar), número e
            # ZIP_ANEXOS (Compesa).
            bruto = bruto_do_edital(sessao, alvo.fonte, alvo.id_na_fonte)
            alvo = replace(alvo, bruto=bruto)
        alvos.append(alvo)
    return alvos, pulados


@app.command()
def analisar(
    limite: Annotated[
        int | None, typer.Option(min=1, help="Máximo de editais (padrão: config/ia.toml).")
    ] = None,
    modelo: Annotated[
        str | None, typer.Option(help="Id do modelo (padrão: modelo_padrao de config/ia.toml).")
    ] = None,
    edital: Annotated[
        str | None, typer.Option(help="Analisa só este id_publico, mesmo encerrado.")
    ] = None,
    refazer: Annotated[bool, typer.Option("--refazer", help="Ignora a análise existente.")] = False,
    fontes: Annotated[
        str,
        typer.Option(
            help="Fontes separadas por vírgula (pncp, sanepar, compesa). A agenda da AWS "
            "pede as três."
        ),
    ] = "pncp,sanepar",
    config: OpcaoConfig = ARQUIVO_CONFIG,
) -> None:
    """Extrai as exigências dos editais abertos com IA e compara com o perfil."""
    from radar.analise import (
        FONTES_ANALISADAS,
        CacheBanco,
        Dependencias,
        DocumentosSanepar,
        ProvedorComCache,
        SemDocumentos,
        TetoAtingido,
        analisar_edital,
        carregar_config_ia,
        carregar_perfil_de,
        criar_armazem,
        guarda_teto,
    )

    configurar_logs()
    fontes_pedidas = tuple(f.strip() for f in fontes.split(",") if f.strip())
    desconhecidas = sorted(set(fontes_pedidas) - set(FONTES_ANALISADAS))
    if not fontes_pedidas or desconhecidas:
        typer.echo(f"fontes sem análise: {', '.join(desconhecidas) or '(nenhuma)'}", err=True)
        raise typer.Exit(code=2)
    ambiente = obter_config()
    if ambiente.ia_provedor == "desligado":
        typer.echo("IA desligada: defina RADAR_IA_PROVEDOR=bedrock", err=True)
        raise typer.Exit(code=2)
    config_ia = carregar_config_ia(ambiente.config_ia)
    modelo = modelo or config_ia.modelo_padrao
    if modelo not in config_ia.modelos:
        typer.echo(f"modelo fora de config/ia.toml: {modelo}", err=True)
        raise typer.Exit(code=2)
    perfil = carregar_perfil_de(ambiente.config_perfil)
    criterios = carregar_criterios(config)
    analisados = falhas = adiados = sem_documentos = 0
    teto = False
    dono = uuid4()
    # Span raiz da rodada e um filho por edital. Atributos só com contagens, ids e desfechos:
    # nunca texto do edital nem da resposta do modelo (exceções também não entram no span).
    with (
        telemetria_do_processo("radar-analisador"),
        tracer().start_as_current_span(
            "analise.rodada",
            context=Context(),
            attributes={"radar.analise.modelo": modelo},
            record_exception=False,
            set_status_on_exception=False,
        ) as rodada,
    ):
        ok = False
        try:
            with _criar_sessao() as sessao:
                if not obter_trava(sessao, TRAVA_ANALISE, dono, DURACAO_TRAVA_ANALISE):
                    # Não é falha: a outra rodada está fazendo o trabalho. Sem "resultado:".
                    typer.echo("analise: outra rodada de análise em andamento; nada feito")
                    ok = True
                    return
                try:
                    alvos, pulados = _alvos_analise(
                        sessao,
                        criterios,
                        edital,
                        modelo,
                        refazer,
                        limite or config_ia.max_editais_por_rodada,
                        fontes_pedidas,
                    )
                    sessao.rollback()  # só leitura: nada aberto durante rede e Bedrock
                    # O cache no banco commita cada resposta paga na hora, em conexão própria:
                    # o gasto da rodada já entra no do mês, e um edital que falha não o apaga.
                    provedor = ProvedorComCache(
                        _criar_provedor_ia(config_ia),
                        CacheBanco(sessao),
                        "gravar",
                        config_ia,
                        guarda_teto(sessao, config_ia, _agora, lambda: Decimal(0)),
                    )
                    cliente = _criar_cliente_arquivos()
                    documentos_sanepar = DocumentosSanepar(_criar_cliente_sanepar())
                    documentos_compesa = (
                        _criar_documentos_compesa() if "compesa" in fontes_pedidas else None
                    )
                    dep = Dependencias(
                        cliente_arquivos=cliente,
                        documentos_sanepar=documentos_sanepar,
                        documentos_compesa=documentos_compesa,
                        armazem=criar_armazem(ambiente.documentos),
                        provedor=provedor,
                        config=config_ia,
                        perfil=perfil,
                        modelo=modelo,
                    )
                    inicio = _relogio_rodada()
                    try:
                        for indice, alvo in enumerate(alvos):
                            if _relogio_rodada() - inicio > ORCAMENTO_RODADA_S:
                                adiados = len(alvos) - indice
                                typer.echo(
                                    f"orçamento de tempo da rodada esgotado: {adiados} editais "
                                    "ficam para a próxima"
                                )
                                break
                            if not obter_trava(sessao, TRAVA_ANALISE, dono, DURACAO_TRAVA_ANALISE):
                                typer.echo(
                                    "trava da análise perdida: rodada interrompida", err=True
                                )
                                falhas += 1
                                break
                            with tracer().start_as_current_span(
                                "analise.edital",
                                attributes={
                                    "radar.fonte": alvo.fonte,
                                    "radar.edital.id_na_fonte": alvo.id_na_fonte,
                                },
                                record_exception=False,
                                set_status_on_exception=False,
                            ) as span:
                                try:
                                    analisar_edital(sessao, alvo, dep, _agora())
                                except SemDocumentos:
                                    # Nada gravado: o edital volta na próxima rodada.
                                    span.set_attribute("radar.desfecho", "sem_documentos")
                                    logger.info(
                                        "analise_sem_documentos",
                                        extra={
                                            "fonte": alvo.fonte,
                                            "id_na_fonte": alvo.id_na_fonte,
                                        },
                                    )
                                    sem_documentos += 1
                                    continue
                                except TetoAtingido:
                                    span.set_attribute("radar.desfecho", "teto")
                                    teto = True
                                    typer.echo("teto mensal da IA atingido: rodada interrompida")
                                    break
                                except Exception as erro:  # um edital ruim não para a rodada
                                    # Só o tipo: a mensagem pode trazer texto do edital/modelo.
                                    span.set_attribute("radar.desfecho", "falha")
                                    span.set_status(StatusCode.ERROR, type(erro).__name__)
                                    sessao.rollback()
                                    logger.exception(
                                        "analise_falhou",
                                        extra={
                                            "fonte": alvo.fonte,
                                            "id_na_fonte": alvo.id_na_fonte,
                                        },
                                    )
                                    falhas += 1
                                    continue
                                span.set_attribute("radar.desfecho", "analisado")
                            analisados += 1
                    finally:
                        cliente.fechar()
                        documentos_sanepar.fechar()
                        if documentos_compesa is not None:
                            documentos_compesa.fechar()
                finally:
                    liberar_trava(sessao, TRAVA_ANALISE, dono)
            rodada.set_attributes(
                {
                    "radar.analise.alvos": len(alvos),
                    "radar.analise.analisados": analisados,
                    "radar.analise.pulados": pulados,
                    "radar.analise.falhas": falhas,
                    "radar.analise.adiados": adiados,
                    "radar.analise.sem_documentos": sem_documentos,
                    "radar.analise.teto": teto,
                    "radar.analise.custo_usd": float(provedor.custo_usd),
                }
            )
            if falhas or teto:
                rodada.set_status(StatusCode.ERROR, "teto" if teto else f"{falhas} falhas")
            ok = not (falhas or teto)
        except BaseException as erro:
            rodada.set_status(StatusCode.ERROR, type(erro).__name__)
            raise
        finally:
            metricas.registrar("ExecucaoOk", 1 if ok else 0)
    # A última linha da saída. Nunca "resultado:": o log group é o do coletor e essa linha
    # cala o alarme "não rodou".
    typer.echo(
        f"analise: {analisados} analisados, {pulados} pulados, {falhas} falhas, "
        f"US$ {provedor.custo_usd:.4f}, {sem_documentos} sem documentos"
    )
    _sair_sem_resultado(falhas + int(teto))


def _gravar_registros(sessao: Session, registros: list[dict[str, Any]]) -> int:
    """`gravar` da carga histórica: registra (sem commit) e conta inseridos + atualizados."""
    resultado = registrar(sessao, registros)
    return resultado.inseridos + resultado.atualizados


def _meses_a_refazer(valores: list[str]) -> set[date]:
    meses: set[date] = set()
    for valor in valores:
        try:
            ano, mes = valor.split("-")
            meses.add(date(int(ano), int(mes), 1))
        except ValueError:
            typer.echo(f"mês inválido (use AAAA-MM): {valor}", err=True)
            raise typer.Exit(code=2) from None
    return meses


@app_historico.command("carregar")
def historico_carregar(
    meses: Annotated[int, typer.Option(min=1, help="Quantos meses para trás.")] = 12,
    refazer_mes: Annotated[
        list[str] | None, typer.Option("--refazer-mes", help="Reabre este mês (AAAA-MM).")
    ] = None,
    config: OpcaoConfig = ARQUIVO_CONFIG,
) -> None:
    """Carrega o histórico de editais do PNCP, só o que casa com o filtro. Retomável."""
    configurar_logs()
    criterios = carregar_criterios(config)
    termos = sorted({t for lista in criterios.linhas.values() for t in lista})
    refazer = _meses_a_refazer(refazer_mes or [])
    with telemetria_do_processo("radar-historico"), _criar_sessao() as sessao:
        try:
            resumo = carregar_historico(
                sessao,
                _criar_cliente(),
                criterios.ufs,
                criterios.modalidades,
                meses_ate(_agora().date(), meses),
                termos,
                _gravar_registros,
                refazer=refazer,
            )
        except RuntimeError as erro:  # trava: outra carga em andamento
            typer.echo(f"historico: {erro}", err=True)
            raise typer.Exit(code=1) from None
    # Nunca "resultado:": o log group é o do coletor.
    typer.echo(
        f"historico: {resumo.combinacoes} combinações ({resumo.concluidas} concluídas, "
        f"{resumo.falhas} falhas), {resumo.paginas} páginas, {resumo.lidos} lidos, "
        f"{resumo.gravados} gravados"
    )
    _sair_sem_resultado(resumo.falhas)


FALHAS_SEGUIDAS_PNCP = 5


@app_resultados.command("atualizar")
def resultados_atualizar(
    limite: Annotated[int, typer.Option(min=1, help="Máximo de editais da rodada.")] = 300,
    config: OpcaoConfig = ARQUIVO_CONFIG,
) -> None:
    """Busca no PNCP os itens e vencedores dos editais encerrados que casam com o filtro."""
    configurar_logs()
    criterios = carregar_criterios(config)
    editais = itens_total = homologadas = falhas = seguidas = 0
    interrompida = False
    with telemetria_do_processo("radar-resultados"), _criar_sessao() as sessao:
        alvos = alvos_resultado(sessao, criterios, _agora(), limite)
        sessao.rollback()  # só leitura: nada aberto durante a rede
        cliente = _criar_cliente_resultados()
        try:
            for alvo in alvos:
                try:
                    brutos = cliente.itens_com_resultados(alvo.cnpj, alvo.ano, alvo.sequencial)
                    resumo = gravar_resultados(
                        sessao,
                        alvo.fonte,
                        alvo.id_na_fonte,
                        interpretar_itens(brutos),
                        _agora(),
                    )
                    sessao.commit()
                except Exception as erro:  # um edital ruim não para a rodada
                    sessao.rollback()
                    logger.exception(
                        "resultados_falhou",
                        extra={"fonte": alvo.fonte, "id_na_fonte": alvo.id_na_fonte},
                    )
                    falhas += 1
                    # Marca como buscado (sem itens): um edital que falha sempre não volta ao topo
                    # dos "nunca buscados" todo dia; a regra dos 7 dias o tenta de novo. Se já
                    # havia itens gravados, a lista vazia não apaga nada (gravar_resultados).
                    try:
                        gravar_resultados(sessao, alvo.fonte, alvo.id_na_fonte, [], _agora())
                        sessao.commit()
                    except Exception:
                        sessao.rollback()
                    # Disjuntor: PNCP fora do ar não vira 300 requisições com retentativas.
                    seguidas = seguidas + 1 if isinstance(erro, ErroResultados) else 0
                    if seguidas >= FALHAS_SEGUIDAS_PNCP:
                        interrompida = True
                        break
                    continue
                seguidas = 0
                editais += 1
                itens_total += len(brutos)
                homologadas += int(resumo.situacao == "homologada")
        finally:
            cliente.fechar()
    typer.echo(
        f"resultados: {editais} editais, {itens_total} itens, {homologadas} homologadas, "
        f"{falhas} falhas"
        + (
            f" (interrompida após {FALHAS_SEGUIDAS_PNCP} falhas seguidas do PNCP)"
            if interrompida
            else ""
        )
    )
    _sair_sem_resultado(falhas)


OpcaoRaizEvals = Annotated[Path, typer.Option("--raiz", help="Pasta do corpus de evals.")]
RAIZ_EVALS = RAIZ_REPOSITORIO / "evals"


@app_avaliar.command("preparar")
def avaliar_preparar(
    id_publico: Annotated[str, typer.Argument(help="Ex.: pncp~78200482000110-1-000298/2026")],
    raiz: OpcaoRaizEvals = RAIZ_EVALS,
) -> None:
    """Baixa os documentos do edital e grava as páginas em textos/<slug>.json (sem gabarito).

    CPF e RG de pessoas citadas no edital são mascarados: o texto vai para o repositório.
    """
    from radar.analise import (
        TextoEval,
        chave_pncp,
        eh_pdf,
        escolher_documentos,
        extrair_paginas,
        mascarar_dados_pessoais,
        slug,
        status_do_texto,
    )

    configurar_logs()
    fonte, _, id_na_fonte = id_publico.partition("~")
    chave = chave_pncp(id_na_fonte) if fonte == "pncp" else None
    if chave is None:
        typer.echo(f"só editais do PNCP: {id_publico}", err=True)
        raise typer.Exit(code=2)
    cliente = _criar_cliente_arquivos()
    try:
        valor = cliente.compra(*chave).get("valorTotalEstimado")
        documentos: list[dict[str, object]] = []
        paginas: list[str] = []
        for arquivo in escolher_documentos(cliente.listar(*chave)):
            dados = cliente.baixar(arquivo.url)
            item: dict[str, object] = {
                "sequencial": arquivo.sequencial,
                "titulo": arquivo.titulo,
                "url": arquivo.url,
                "sha256": hashlib.sha256(dados).hexdigest(),
            }
            if not eh_pdf(dados):
                item["status"] = "nao_pdf"
            else:
                texto = extrair_paginas(dados)
                item.update(status=status_do_texto(texto), paginas=len(texto.paginas))
                item["pagina_inicial"] = len(paginas) + 1
                if item["status"] == "extraido":
                    paginas.extend(mascarar_dados_pessoais(p) for p in texto.paginas)
            documentos.append(item)
    finally:
        cliente.fechar()
    corpo = TextoEval(
        edital=id_publico,
        valor_estimado=None if valor is None else Decimal(str(valor)),
        documentos=documentos,
        paginas=paginas,
    )
    destino = raiz / "textos" / f"{slug(id_publico)}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    texto_json = json.dumps(corpo.model_dump(mode="json"), ensure_ascii=False, indent=2)
    destino.write_text(texto_json + "\n", encoding="utf-8", newline="\n")
    typer.echo(f"{destino}: {len(documentos)} documentos, {len(paginas)} páginas")


@app_avaliar.command("validar")
def avaliar_validar(raiz: OpcaoRaizEvals = RAIZ_EVALS) -> None:
    """Confere esquema dos gabaritos e que todo trecho existe na página indicada."""
    from radar.analise import validar_gabaritos

    erros = validar_gabaritos(raiz)
    for erro in erros:
        typer.echo(erro)
    typer.echo(f"{len(erros)} erros")
    _sair_sem_resultado(len(erros))


@app_avaliar.command("rodar")
def avaliar_rodar(
    modelo: Annotated[str | None, typer.Option(help="Padrão: modelo_padrao.")] = None,
    modo: Annotated[str, typer.Option(help="replay (sem custo) ou gravar.")] = "replay",
    conjunto: Annotated[str, typer.Option(help="ajuste, teste ou todos.")] = "todos",
    teto_usd: Annotated[float, typer.Option("--teto-usd", help="Teto desta rodada.")] = 1.0,
    raiz: OpcaoRaizEvals = RAIZ_EVALS,
) -> None:
    """Roda a extração sobre o corpus e imprime a tabela de precisão e recall por campo."""
    from radar.analise import (
        INSTRUCAO_GRAVAR,
        CacheArquivos,
        Pedido,
        ProvedorComCache,
        TetoAtingido,
        avaliar,
        carregar_config_ia,
        estimar,
        para_markdown,
    )

    if modo not in ("replay", "gravar") or conjunto not in ("ajuste", "teste", "todos"):
        typer.echo("use --modo replay|gravar e --conjunto ajuste|teste|todos", err=True)
        raise typer.Exit(code=2)
    configurar_logs("WARNING")
    ambiente = obter_config()
    config_ia = carregar_config_ia(ambiente.config_ia)
    modelo = modelo or config_ia.modelo_padrao
    if modelo not in config_ia.modelos:
        typer.echo(f"modelo fora de config/ia.toml: {modelo}", err=True)
        raise typer.Exit(code=2)
    cache = CacheArquivos(raiz / "respostas")
    if modo == "replay":
        provedor = ProvedorComCache(None, cache, "replay", config_ia)
    else:
        if ambiente.ia_provedor != "bedrock":
            typer.echo("modo gravar chama o modelo: defina RADAR_IA_PROVEDOR=bedrock", err=True)
            raise typer.Exit(code=2)
        teto = Decimal(str(teto_usd))

        def teto_da_rodada(pedido: Pedido) -> None:
            previsto = provedor.custo_usd + estimar(pedido, config_ia.preco(pedido.modelo))
            if previsto > teto:
                raise TetoAtingido(f"teto da rodada: US$ {previsto:.4f} > US$ {teto}")

        provedor = ProvedorComCache(
            _criar_provedor_ia(config_ia), cache, "gravar", config_ia, teto_da_rodada
        )
    try:
        relatorio = avaliar(
            raiz,
            provedor,
            modelo,
            config_ia,
            conjunto,  # type: ignore[arg-type]  # validado acima
            replay=modo == "replay",
        )
    except TetoAtingido as erro:
        typer.echo(f"{erro}; as respostas já gravadas ficam em {raiz / 'respostas'}")
        raise typer.Exit(code=1) from erro
    typer.echo(para_markdown(relatorio))
    if relatorio.ausentes:
        faltam = len(relatorio.ausentes)
        typer.echo(f"{faltam} respostas ausentes: {INSTRUCAO_GRAVAR} --modelo {modelo}")
        raise typer.Exit(code=1)


@app.command()
def recomparar() -> None:
    """Refaz o veredito de todas as análises com o perfil atual (sem IA, sem custo)."""
    from radar.analise import carregar_perfil_de
    from radar.analise import recomparar as recomparar_analises

    configurar_logs()
    perfil = carregar_perfil_de(obter_config().config_perfil)
    sessao = _criar_sessao()
    try:
        total, mudaram = recomparar_analises(sessao, perfil)
    finally:
        sessao.close()
    typer.echo(f"recomparar: {total} análises, {mudaram} mudaram de veredito")


@app.command()
def api(
    porta: Annotated[int, typer.Option(min=1, max=65535, help="Porta local da API.")] = 8000,
    config: OpcaoConfig = ARQUIVO_CONFIG,
) -> None:
    """Sobe a API JSON local (só leitura) em 127.0.0.1."""
    from radar.api.app import criar_app

    configurar_logs()
    uvicorn.run(criar_app(config_filtro=config), host="127.0.0.1", port=porta)
