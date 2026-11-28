"""Resumo não sensível do perfil, gravado pela análise para o painel ler."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from radar.analise.modelos import PerfilResumo
from radar.analise.perfil import Perfil


@dataclass(frozen=True)
class ResumoDoPerfil:
    exemplo: bool
    uf_sede: str | None
    valor_maximo_obra: Decimal | None
    atualizado_em: datetime


def gravar_perfil_resumo(sessao: Session, perfil: Perfil, agora: datetime) -> None:
    """Upsert da linha `id = 1`; quem chama faz o commit."""
    linha = sessao.get(PerfilResumo, 1)
    if linha is None:
        linha = PerfilResumo(id=1)
        sessao.add(linha)
    linha.exemplo = perfil.exemplo
    linha.uf_sede = perfil.uf_sede
    linha.valor_maximo_obra = perfil.valor_maximo_obra
    linha.atualizado_em = agora


def ler_perfil_resumo(sessao: Session) -> ResumoDoPerfil | None:
    linha = sessao.get(PerfilResumo, 1)
    if linha is None:
        return None
    return ResumoDoPerfil(
        exemplo=linha.exemplo,
        uf_sede=linha.uf_sede,
        valor_maximo_obra=linha.valor_maximo_obra,
        atualizado_em=linha.atualizado_em,
    )
