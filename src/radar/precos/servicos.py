"""Serviços-padrão: casa a descrição e a unidade de um item com um serviço comparável.

As regras ficam em `config/servicos.toml`. O casamento é por termos sem acento, com U+FFFD
(o acento perdido no `Remessa_*` do TCE-PE) valendo por qualquer letra. Na dúvida, o item fica
sem serviço: casar com dois serviços, unidade fora da lista, rede sem material ou sem DN.
"""

import re
import tomllib
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from pathlib import Path

from radar.comum import RAIZ_REPOSITORIO

CAMINHO_PADRAO = RAIZ_REPOSITORIO / "config" / "servicos.toml"
CURINGA = "�"

_ESPACOS = re.compile(r"\s+")


@dataclass(frozen=True)
class Material:
    nome: str
    termos: tuple[str, ...]


@dataclass(frozen=True)
class Servico:
    nome: str
    rotulo: str
    linha: str
    unidade: str
    unidades_aceitas: dict[str, Decimal]
    # Cada grupo é uma alternativa ("asfalt" ou "cbuq"); todos os grupos precisam casar.
    termos_obrigatorios: tuple[tuple[str, ...], ...]
    termos_qualquer: tuple[str, ...]
    termos_excluidos: tuple[str, ...]
    materiais: tuple[Material, ...] = ()
    dn_min: int | None = None
    dn_max: int | None = None

    @property
    def de_rede(self) -> bool:
        return bool(self.materiais)


@dataclass(frozen=True)
class Classificacao:
    servico: str
    material: str | None
    dn: int | None
    com_fornecimento: bool | None
    unidade_canonica: str
    # Quantidade na unidade canônica = quantidade × fator; preço canônico = preço ÷ fator.
    fator: Decimal


def sem_acento(texto: str) -> str:
    """Minúsculas, sem acento e com espaços colapsados. U+FFFD e pontuação ficam."""
    decomposto = unicodedata.normalize("NFKD", texto)
    limpo = "".join(c for c in decomposto if not unicodedata.combining(c)).lower()
    return _ESPACOS.sub(" ", limpo).strip()


def normalizar_unidade(unidade: str) -> str:
    """Unidade sem acento, ponto nem espaço ("m³" → "m3", "Unid." → "unid", "M" → "m")."""
    return re.sub(r"[\s.]", "", sem_acento(unidade))


@cache
def _padrao_termo(termo: str) -> re.Pattern[str]:
    """Termo no começo de uma palavra; cada letra também casa com U+FFFD."""
    partes: list[str] = []
    for c in sem_acento(termo):
        if c == " ":
            partes.append(rf"[^a-z0-9{CURINGA}]+")
        elif c.isalpha():
            partes.append(f"[{re.escape(c)}{CURINGA}]")
        else:
            partes.append(re.escape(c))
    return re.compile(rf"(?<![a-z0-9{CURINGA}])" + "".join(partes))


def tem_termo(texto: str, termo: str) -> bool:
    """`texto` já normalizado por `sem_acento`."""
    return _padrao_termo(termo).search(texto) is not None


def _algum(texto: str, termos: tuple[str, ...]) -> bool:
    return any(tem_termo(texto, t) for t in termos)


# DN explícito: "DN 150", "DN150", "DN=150", "Ø 150", "diâmetro nominal de 150 mm".
_DN_EXPLICITO = (
    re.compile(r"(?<![a-z])dn\s*[=:]?\s*(\d{2,4})(?![\d,.]?\d)"),
    re.compile(r"[øφ]\s*[=:]?\s*(\d{2,4})(?![\d,.]?\d)"),
    re.compile(r"(?<![a-z])diametro(?:\s+nominal)?(?:\s+de)?\s*[=:]?\s*(\d{2,4})(?![\d,.]?\d)"),
)
# "150 MM" solto: só vale quando a descrição fala de tubo ou rede e nada explícito apareceu.
_DN_MM = re.compile(r"(?<![\d,.])(\d{2,4})\s*mm(?![a-z])")
_TUBO_OU_REDE = ("tubo", "tubos", "tubula", "rede", "redes", "coletor", "manilha")


def extrair_dn(texto: str) -> int | None:
    """DN em mm de uma descrição normalizada. Dois valores diferentes → None (ambíguo)."""
    achados: set[int] = set()
    for padrao in _DN_EXPLICITO:
        achados.update(int(m.group(1)) for m in padrao.finditer(texto))
    if not achados and _algum(texto, _TUBO_OU_REDE):
        achados.update(int(m.group(1)) for m in _DN_MM.finditer(texto))
    return achados.pop() if len(achados) == 1 else None


_SEM_FORNECIMENTO = ("sem fornecimento", "exclusive fornecimento", "exceto fornecimento")
_FORNECIMENTO = ("fornecimento", "aquisicao", "inclusive tubo", "inclusive o tubo")
_SO_SERVICO = ("assentamento", "assentar", "montagem", "instalacao")


def com_fornecimento(texto: str) -> bool:
    """Item de rede inclui o tubo? Só "assentamento" (ou "sem fornecimento") é serviço sem tubo.

    "Fornecimento e assentamento" e o tubo sozinho ("TUBO PVC ... DN 150") contam como com
    fornecimento.
    """
    if _algum(texto, _SEM_FORNECIMENTO):
        return False
    if _algum(texto, _FORNECIMENTO):
        return True
    return not _algum(texto, _SO_SERVICO)


def _casa_termos(servico: Servico, texto: str) -> bool:
    if _algum(texto, servico.termos_excluidos):
        return False
    if not all(_algum(texto, grupo) for grupo in servico.termos_obrigatorios):
        return False
    return not servico.termos_qualquer or _algum(texto, servico.termos_qualquer)


def _classificar_em(servico: Servico, texto: str, unidade: str) -> Classificacao | None:
    fator = servico.unidades_aceitas.get(unidade)
    if fator is None or not _casa_termos(servico, texto):
        return None
    if not servico.de_rede:
        return Classificacao(servico.nome, None, None, None, servico.unidade, fator)
    material = next((m.nome for m in servico.materiais if _algum(texto, m.termos)), None)
    dn = extrair_dn(texto)
    if material is None or dn is None:
        return None
    if (servico.dn_min is not None and dn < servico.dn_min) or (
        servico.dn_max is not None and dn > servico.dn_max
    ):
        return None
    return Classificacao(
        servico.nome, material, dn, com_fornecimento(texto), servico.unidade, fator
    )


def _termos(valor: object) -> tuple[str, ...]:
    if not isinstance(valor, list) or not all(isinstance(t, str) and t.strip() for t in valor):
        raise ValueError(f"lista de termos inválida: {valor!r}")
    return tuple(sem_acento(t) for t in valor)


def _servico(dados: dict[str, object]) -> Servico:
    nome = str(dados["nome"])
    obrigatorios_brutos = dados.get("termos_obrigatorios", [])
    if not isinstance(obrigatorios_brutos, list):
        raise ValueError(f"{nome}: termos_obrigatorios precisa ser lista")
    obrigatorios = tuple(
        _termos(item if isinstance(item, list) else [item]) for item in obrigatorios_brutos
    )
    unidades_brutas = dados["unidades_aceitas"]
    if not isinstance(unidades_brutas, dict) or not unidades_brutas:
        raise ValueError(f"{nome}: unidades_aceitas vazia")
    unidades = {normalizar_unidade(u): Decimal(str(f)) for u, f in unidades_brutas.items()}
    unidade = normalizar_unidade(str(dados["unidade"]))
    if unidades.get(unidade) != Decimal(1):
        raise ValueError(f"{nome}: a unidade canônica {unidade!r} precisa ter fator 1")
    materiais_brutos = dados.get("materiais", [])
    if not isinstance(materiais_brutos, list):
        raise ValueError(f"{nome}: materiais precisa ser lista")
    materiais = tuple(Material(str(m["nome"]), _termos(m["termos"])) for m in materiais_brutos)
    dn_min, dn_max = dados.get("dn_min"), dados.get("dn_max")
    if materiais and not (isinstance(dn_min, int) and isinstance(dn_max, int)):
        raise ValueError(f"{nome}: serviço de rede precisa de dn_min e dn_max")
    return Servico(
        nome=nome,
        rotulo=str(dados["rotulo"]),
        linha=str(dados["linha"]),
        unidade=unidade,
        unidades_aceitas=unidades,
        termos_obrigatorios=obrigatorios,
        termos_qualquer=_termos(dados.get("termos_qualquer", [])),
        termos_excluidos=_termos(dados.get("termos_excluidos", [])),
        materiais=materiais,
        dn_min=dn_min if isinstance(dn_min, int) else None,
        dn_max=dn_max if isinstance(dn_max, int) else None,
    )


def carregar_servicos(caminho: Path = CAMINHO_PADRAO) -> tuple[Servico, ...]:
    with caminho.open("rb") as arquivo:
        dados = tomllib.load(arquivo)
    servicos = tuple(_servico(s) for s in dados["servico"])
    nomes = [s.nome for s in servicos]
    if len(set(nomes)) != len(nomes):
        raise ValueError(f"nome de serviço repetido em {caminho}")
    return servicos


@cache
def _servicos_padrao() -> tuple[Servico, ...]:
    return carregar_servicos()


def classificar(
    descricao: str, unidade: str, servicos: tuple[Servico, ...] | None = None
) -> Classificacao | None:
    """Serviço-padrão do item, ou None se não casar com exatamente um serviço."""
    texto = sem_acento(descricao)
    unidade_norm = normalizar_unidade(unidade)
    candidatos = servicos if servicos is not None else _servicos_padrao()
    achados = [c for s in candidatos if (c := _classificar_em(s, texto, unidade_norm))]
    return achados[0] if len(achados) == 1 else None
