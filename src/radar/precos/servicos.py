"""Serviços-padrão: casa a descrição e a unidade de um item com um serviço comparável.

As regras ficam em `config/servicos.toml`. O casamento é por termos sem acento, com U+FFFD
(o acento perdido no `Remessa_*` do TCE-PE) valendo por qualquer letra. Na dúvida, o item fica
sem serviço: casar com dois serviços, unidade fora da lista, rede sem material, sem DN ou
sem escopo claro (só o tubo, só o assentamento ou os dois).
"""

import re
import tomllib
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Literal, get_args

from radar.comum import RAIZ_REPOSITORIO

CAMINHO_PADRAO = RAIZ_REPOSITORIO / "config" / "servicos.toml"
CURINGA = "�"

Escopo = Literal["material", "assentamento", "completo"]
ESCOPOS: tuple[str, ...] = get_args(Escopo)
# Faixa de profundidade da escavação: o m³ fica mais caro com a profundidade.
Faixa = Literal["ate_1_5m", "1_5_a_3m", "3_a_6m"]
FAIXAS: tuple[str, ...] = get_args(Faixa)

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
    # Escavação: sem faixa de profundidade reconhecível o item fica sem serviço.
    exige_faixa: bool = False

    @property
    def de_rede(self) -> bool:
        return bool(self.materiais)


@dataclass(frozen=True)
class Classificacao:
    servico: str
    material: str | None
    # Em PEAD pode ser o diâmetro externo (DE), não o nominal; só se compara dentro do material.
    dn: int | None
    # Só em rede: "material" (só o tubo), "assentamento" (só o serviço) ou "completo" (os dois).
    escopo: Escopo | None
    unidade_canonica: str
    # Quantidade na unidade canônica = quantidade × fator; preço canônico = preço ÷ fator.
    fator: Decimal
    # Só em escavação: faixa de profundidade (ver `faixa_profundidade`).
    faixa: Faixa | None = None


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
# Em PEAD o número costuma ser o diâmetro externo ("DE 110 MM"), não o DN: fica em `dn` como
# veio. Não se mistura com PVC/FoFo porque o material faz parte da chave de comparação.
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


_SEM_FORNECIMENTO = (
    "sem fornecimento",
    "exclusive fornecimento",
    "exceto fornecimento",
    "nao inclui fornecimento",
)
_FORNECIMENTO = ("fornecimento", "aquisicao")
_INCLUSIVE = ("inclusive fornecimento", "inclusive o fornecimento", "inclusive tubo")
_SERVICO = (
    "assentamento",
    "assentar",
    "montagem",
    "instalacao",
    "execucao",
    "implantacao",
    "lancamento",
    "mao de obra",
)
# Linha que é o próprio tubo: começa por "TUBO ..." ou "FORNECIMENTO/AQUISIÇÃO DE TUBO ...".
_SO_MATERIAL = (
    "tubo",
    "fornecimento de tubo",
    "fornecimento tubo",
    "aquisicao de tubo",
    "aquisicao tubo",
)


def escopo_de_rede(texto: str) -> Escopo | None:
    """O que o item de rede cobre: só o tubo, só o assentamento, os dois ou não dá para saber.

    "ASSENTAMENTO DE TUBO ...", "... MAO DE OBRA" (ou com "sem fornecimento") → assentamento;
    "FORNECIMENTO E ASSENTAMENTO ..." e "EXECUCAO DE REDE ... INCLUSIVE FORNECIMENTO" →
    completo; "TUBO PVC ... DN 150" e "FORNECIMENTO DE TUBO ..." sem palavra de serviço →
    material. Fora disso ("REDE COLETORA DE ESGOTO PVC DN 150", sem verbo) → None, e o item
    fica sem serviço.
    """
    if _algum(texto, _SEM_FORNECIMENTO):
        return "assentamento"
    if _algum(texto, _INCLUSIVE):
        return "completo"
    if _algum(texto, _SERVICO):
        return "completo" if _algum(texto, _FORNECIMENTO) else "assentamento"
    inicio = texto.lstrip(" -.,;:0123456789")
    if any(_padrao_termo(t).match(inicio) for t in _SO_MATERIAL):
        return "material"
    return None


# Profundidade. Números em metros ("1,5", "1,50", "3.0"); o "m" pode faltar no primeiro número
# de um intervalo ("1,5 A 3,0 M"). A largura da vala ("LARG. DE 0,8 M A 1,5 M") sai antes.
_NUM = r"(\d+(?:[.,]\d+)?)"
_M = r"\s*m(?![a-z0-9])"
_LIMITE = r"(?:ate|menor que|menor ou igual a|inferior a|maior que|acima de|superior a|de)"
_LARGURA = re.compile(
    rf"(?<![a-z])larg\w*\.?\s*(?:{_LIMITE}\s+)?{_NUM}(?:{_M})?(?:\s+(?:e\s+)?(?:a|ate)\s+{_NUM}{_M})?"
)
_INTERVALO = re.compile(
    rf"(?:(?<![a-z])(?:maior que|acima de|superior a|entre|de)\s+)?(?<![\d.,]){_NUM}(?:{_M})?"
    rf"\s+(?:e\s+)?(?:a|ate)\s+{_NUM}{_M}"
)
_ATE = re.compile(rf"(?<![a-z])(?:ate|menor que|menor ou igual a|inferior a)\s+{_NUM}{_M}")
_ACIMA = re.compile(rf"(?<![a-z])(?:maior que|acima de|superior a)\s+{_NUM}{_M}")
_ATE_SEM_ACENTO = re.compile(rf"(?<![a-z])at{CURINGA}(?![a-z])")  # "ATÉ" com o acento perdido
_TETO_RASA = Decimal("1.5")
_TETO_MEDIA = Decimal("3")
_TETO_FUNDA = Decimal("6")


def _metros(texto: str) -> Decimal:
    return Decimal(texto.replace(",", "."))


def _faixa_de(minimo: Decimal, maximo: Decimal | None) -> Faixa | None:
    if maximo is not None and maximo <= _TETO_RASA:
        return "ate_1_5m"
    if minimo >= _TETO_RASA and maximo is not None and maximo <= _TETO_MEDIA:
        return "1_5_a_3m"
    if minimo >= _TETO_MEDIA and (maximo is None or maximo <= _TETO_FUNDA):
        return "3_a_6m"
    return None


def faixa_profundidade(texto: str) -> Faixa | None:
    """Faixa de profundidade de uma escavação (texto já normalizado por `sem_acento`).

    "PROF. ATE 1,5 M" → ate_1_5m; "MAIOR QUE 1,5 M ATE 3,0 M", "1,5 A 3,0 M" → 1_5_a_3m;
    "DE 3,0 M ATE 4,5 M", "ACIMA DE 3,00 M" → 3_a_6m. Sem profundidade, faixa que atravessa
    um limite ("ATE 3,0 M", "ACIMA DE 1,5 M") ou duas faixas diferentes → None.
    """
    resto = _LARGURA.sub(" ", _ATE_SEM_ACENTO.sub("ate", texto))
    faixas: set[Faixa | None] = set()
    for achado in _INTERVALO.finditer(resto):
        faixas.add(_faixa_de(_metros(achado.group(1)), _metros(achado.group(2))))
    resto = _INTERVALO.sub(" ", resto)
    for achado in _ATE.finditer(resto):
        faixas.add(_faixa_de(Decimal(0), _metros(achado.group(1))))
    resto = _ATE.sub(" ", resto)
    for achado in _ACIMA.finditer(resto):
        faixas.add(_faixa_de(_metros(achado.group(1)), None))
    if len(faixas) != 1:
        return None
    return faixas.pop()


# Linha de rede que cita água e esgoto ("TUBO PEAD ... PARA REDE DE AGUA OU ESGOTO") é dos dois
# serviços ao mesmo tempo: fica sem. "Águas servidas" é esgoto e não conta como água.
_AGUAS_SERVIDAS = re.compile(r"aguas? servidas?")


def _cita_agua_e_esgoto(texto: str) -> bool:
    sem_servidas = _AGUAS_SERVIDAS.sub(" ", texto)
    return tem_termo(sem_servidas, "agua") and tem_termo(sem_servidas, "esgot")


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
    if servico.exige_faixa:
        faixa = faixa_profundidade(texto)
        if faixa is None:
            return None
        return Classificacao(servico.nome, None, None, None, servico.unidade, fator, faixa)
    if not servico.de_rede:
        return Classificacao(servico.nome, None, None, None, servico.unidade, fator)
    if _cita_agua_e_esgoto(texto):
        return None
    material = next((m.nome for m in servico.materiais if _algum(texto, m.termos)), None)
    dn = extrair_dn(texto)
    if material is None or dn is None:
        return None
    if (servico.dn_min is not None and dn < servico.dn_min) or (
        servico.dn_max is not None and dn > servico.dn_max
    ):
        return None
    escopo = escopo_de_rede(texto)
    if escopo is None:
        return None
    return Classificacao(servico.nome, material, dn, escopo, servico.unidade, fator)


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
    exige_faixa = dados.get("exige_faixa", False)
    if not isinstance(exige_faixa, bool):
        raise ValueError(f"{nome}: exige_faixa precisa ser booleano")
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
        exige_faixa=exige_faixa,
    )


def carregar_termos_processo(caminho: Path = CAMINHO_PADRAO) -> tuple[str, ...]:
    """Termos do pré-filtro de processo do TCE-PE (`[processos] termos`)."""
    with caminho.open("rb") as arquivo:
        dados = tomllib.load(arquivo)
    processos = dados.get("processos")
    if not isinstance(processos, dict):
        raise ValueError(f"[processos] ausente em {caminho}")
    return _termos(processos.get("termos"))


@cache
def termos_processo_padrao() -> tuple[str, ...]:
    return carregar_termos_processo()


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
