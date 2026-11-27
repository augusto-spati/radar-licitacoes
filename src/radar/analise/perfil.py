"""Perfil da empresa (o do repositório só tem valores de exemplo)."""

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from radar.analise.exigencias import DOCUMENTOS


class AtestadoPerfil(BaseModel):
    descricao: str
    # Palavras (sem acento, minúsculas) que casam com a descrição exigida no edital.
    termos: list[str]
    quantidade: Decimal
    unidade: str


class Perfil(BaseModel):
    porte: Literal["me", "epp", "demais"]
    capital_social: Decimal | None
    patrimonio_liquido: Decimal | None
    garantia_maxima_percentual: Decimal
    faz_visita_tecnica: bool
    depende_de_consorcio: bool
    documentos: list[str]
    atestados: list[AtestadoPerfil] = []


def carregar_perfil(caminho: Path) -> Perfil:
    """Lê `[empresa]` e `[[atestados]]`; documento fora de DOCUMENTOS é erro."""
    with caminho.open("rb") as arquivo:
        dados = tomllib.load(arquivo)
    empresa = dict(dados.get("empresa", {}))
    invalidos = sorted(set(empresa.get("documentos", [])) - set(DOCUMENTOS))
    if invalidos:
        raise ValueError(f"documentos desconhecidos no perfil: {', '.join(invalidos)}")
    empresa["atestados"] = dados.get("atestados", [])
    # TOML lê 1000000 como int e 5.5 como float; Decimal(str()) evita lixo binário.
    for campo in ("capital_social", "patrimonio_liquido", "garantia_maxima_percentual"):
        if isinstance(empresa.get(campo), float):
            empresa[campo] = Decimal(str(empresa[campo]))
    return Perfil.model_validate(empresa)
