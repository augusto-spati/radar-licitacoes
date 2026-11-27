"""Perfil da empresa (o do repositório só tem valores de exemplo).

O perfil real é dado privado da empresa e não vai para o repositório (público) nem para a
imagem: na nuvem ele fica no bucket privado de documentos (`s3://<bucket>/perfil/perfil.toml`)
e `RADAR_CONFIG_PERFIL` aponta para lá. Trocou o perfil, rode `radar recomparar`: os vereditos
gravados foram calculados com o perfil antigo.
"""

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
    return ler_perfil(caminho.read_bytes())


def carregar_perfil_de(origem: str) -> Perfil:
    """`s3://<bucket>/<chave>` (bucket privado) ou um caminho de arquivo."""
    if not origem.startswith("s3://"):
        return carregar_perfil(Path(origem))
    from radar.analise.armazem import ArmazemS3

    bucket, _, chave = origem.removeprefix("s3://").partition("/")
    dados = ArmazemS3(bucket).ler(chave)
    if dados is None:
        raise FileNotFoundError(f"perfil ausente: {origem}")
    return ler_perfil(dados)


def ler_perfil(conteudo: bytes) -> Perfil:
    """O perfil a partir do texto TOML."""
    dados = tomllib.loads(conteudo.decode("utf-8"))
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
