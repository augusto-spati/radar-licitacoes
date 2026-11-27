"""Aplicação FastAPI: rotas de leitura sob /api."""

import logging
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session

from radar.analise import carregar_config_ia, obter_analise
from radar.api.modelos import (
    AnaliseOut,
    EditalDetalheOut,
    ListaOut,
    StatusOut,
    analise_out,
    detalhe_out,
    iso_com_fuso,
    resumo_out,
)
from radar.coleta import ultima_atualizacao
from radar.comum import criar_engine, fabrica_sessao, obter_config
from radar.editais import carregar_criterios, filtrar, obter_edital, separar_id_publico

FUSO = ZoneInfo("America/Sao_Paulo")
logger = logging.getLogger(__name__)


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
    # Sem config/ia.toml (ex.: Lambda sem RADAR_CONFIG_IA), só a rota da análise fica fora.
    try:
        modelo_padrao: str | None = carregar_config_ia(config.config_ia).modelo_padrao
    except FileNotFoundError:
        logger.warning("config_ia_ausente", extra={"caminho": str(config.config_ia)})
        modelo_padrao = None
    except (tomllib.TOMLDecodeError, KeyError, TypeError, ValueError, ArithmeticError) as erro:
        # ia.toml quebrado não derruba a API inteira: só a rota da análise fica fora.
        logger.error(
            "config_ia_invalida", extra={"caminho": str(config.config_ia), "erro": repr(erro)}
        )
        modelo_padrao = None
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

    @app.get("/api/editais/{id_publico:path}")
    def detalhe(id_publico: str, sessao: SessaoDep) -> EditalDetalheOut:
        """`id_publico` é `<fonte>~<id_na_fonte>`; o id da fonte pode ter `/` (ou `%2F`)."""
        chave = separar_id_publico(id_publico)
        edital = None if chave is None else obter_edital(sessao, *chave, criterios)
        if edital is None:
            raise HTTPException(status_code=404, detail="edital não encontrado")
        return detalhe_out(edital)

    @app.get("/api/analises/{id_publico:path}")
    def analise(id_publico: str, sessao: SessaoDep) -> AnaliseOut:
        """Exigências extraídas por IA e comparadas com o perfil (modelo padrão)."""
        if modelo_padrao is None:
            raise HTTPException(status_code=503, detail="análise indisponível")
        chave = separar_id_publico(id_publico)
        lida = None if chave is None else obter_analise(sessao, *chave, modelo_padrao)
        if lida is None:
            raise HTTPException(status_code=404, detail="análise não encontrada")
        return analise_out(lida)

    return app
