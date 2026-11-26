"""Aplicação FastAPI: rotas de leitura sob /api."""

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session

from radar.api.modelos import (
    EditalDetalheOut,
    ListaOut,
    StatusOut,
    detalhe_out,
    iso_com_fuso,
    resumo_out,
)
from radar.coleta import ultima_atualizacao
from radar.comum import criar_engine, fabrica_sessao, obter_config
from radar.editais import carregar_criterios, filtrar, obter_edital

FUSO = ZoneInfo("America/Sao_Paulo")


def _agora() -> datetime:
    return datetime.now(FUSO)


def criar_app(
    fabrica: Callable[[], Session] | None = None,
    config_filtro: Path = Path("config/filtro.toml"),
    relogio: Callable[[], datetime] | None = None,
) -> FastAPI:
    """`fabrica` devolve uma Session, usada como context manager (uma por requisição)."""
    fabrica = fabrica or fabrica_sessao(criar_engine(obter_config()))
    relogio = relogio or _agora
    criterios = carregar_criterios(config_filtro)
    app = FastAPI(title="Radar de licitações", docs_url=None, redoc_url=None)

    def sessao_da_requisicao() -> Iterator[Session]:
        with fabrica() as sessao:
            yield sessao

    SessaoDep = Annotated[Session, Depends(sessao_da_requisicao)]

    @app.get("/api/status")
    def status(sessao: SessaoDep) -> StatusOut:
        return StatusOut(atualizado_em=iso_com_fuso(ultima_atualizacao(sessao)))

    @app.get("/api/editais")
    def listar(
        sessao: SessaoDep,
        linha: str | None = None,
        uf: Annotated[list[str] | None, Query()] = None,
        todos: bool = False,
        limite: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> ListaOut:
        escolhidos = criterios
        if linha is not None:
            if linha not in criterios.linhas:
                raise HTTPException(status_code=422, detail=f"linha desconhecida: {linha}")
            escolhidos = replace(escolhidos, linhas={linha: criterios.linhas[linha]})
        if uf:
            pedidas = {u.upper() for u in uf}
            escolhidos = replace(escolhidos, ufs=[u for u in criterios.ufs if u in pedidas])
        editais = filtrar(sessao, escolhidos, relogio(), somente_abertos=not todos, limite=limite)
        return ListaOut(
            atualizado_em=iso_com_fuso(ultima_atualizacao(sessao)),
            editais=[resumo_out(e) for e in editais],
        )

    @app.get("/api/editais/{numero:path}")
    def detalhe(numero: str, sessao: SessaoDep) -> EditalDetalheOut:
        edital = obter_edital(sessao, numero, criterios)
        if edital is None:
            raise HTTPException(status_code=404, detail="edital não encontrado")
        return detalhe_out(edital)

    return app
