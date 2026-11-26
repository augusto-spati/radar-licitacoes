"""Aplicação FastAPI: rotas de leitura sob /api."""

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
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
    config_filtro: Path | None = None,
    relogio: Callable[[], datetime] | None = None,
) -> FastAPI:
    """`fabrica` devolve uma Session, usada como context manager (uma por requisição).

    Sem `config_filtro`, usa `RADAR_CONFIG_FILTRO` (padrão: config/filtro.toml da raiz).
    """
    config = obter_config()
    fabrica = fabrica or fabrica_sessao(criar_engine(config))
    relogio = relogio or _agora
    criterios = carregar_criterios(config_filtro or config.config_filtro)
    # `linha` e `uf` são enums dos critérios carregados: o OpenAPI (e o api.d.ts do
    # front) carrega as uniões `Linha` e `UF`, e valor fora delas é 422 da validação.
    # Para o mypy são strings (StrEnum é str); o tipo real só existe em tempo de execução.
    if TYPE_CHECKING:
        type Linha = str
        type UF = str
    else:
        Linha = StrEnum("Linha", {nome: nome for nome in criterios.linhas})
        UF = StrEnum("UF", {uf: uf for uf in criterios.ufs})
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
        linha: Linha | None = None,
        uf: Annotated[list[UF] | None, Query()] = None,
        todos: bool = False,
        limite: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> ListaOut:
        escolhidos = criterios
        if linha is not None:
            escolhidos = replace(escolhidos, linhas={str(linha): criterios.linhas[linha]})
        if uf:
            pedidas = {str(u) for u in uf}
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
