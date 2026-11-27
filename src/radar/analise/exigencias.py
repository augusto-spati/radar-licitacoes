"""O que o modelo devolve (achados) e o que o pipeline guarda (exigências)."""

from decimal import Decimal
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict

Campo = Literal[
    "atestado_tecnico",
    "capital_social_minimo",
    "patrimonio_liquido_minimo",
    "garantia_proposta",
    "garantia_contratual",
    "visita_tecnica",
    "consorcio",
    "me_epp",
    "prazo_execucao",
    "documento_habilitacao",
]
CAMPOS: tuple[str, ...] = get_args(Campo)

DOCUMENTOS: tuple[str, ...] = (
    "certidao_federal",
    "certidao_fgts",
    "certidao_trabalhista",
    "certidao_estadual",
    "certidao_municipal",
    "certidao_falencia",
    "balanco_patrimonial",
    "registro_conselho",
    "contrato_social",
    "declaracao_menor",
    "outro",
)


class Achado(BaseModel):
    """Um achado de um trecho, como o modelo devolve. A página não vem do modelo."""

    model_config = ConfigDict(extra="forbid")

    campo: Campo
    valor: str
    # O esquema enviado pede o campo sempre; no modo ferramenta alguns modelos o omitem quando
    # é nulo (sonda de 2026-11-26), e a ausência vale null.
    numero: float | None = None
    unidade: str
    trecho: str


class RespostaExtracao(BaseModel):
    model_config = ConfigDict(extra="forbid")

    achados: list[Achado]


_CHAVES_DESCARTADAS = {"title", "default", "description", "minimum", "maximum", "maxLength"}


def _limpar(no: Any, defs: dict[str, Any]) -> Any:
    if isinstance(no, list):
        return [_limpar(item, defs) for item in no]
    if not isinstance(no, dict):
        return no
    if "$ref" in no:
        return _limpar(defs[no["$ref"].rsplit("/", 1)[-1]], defs)
    if "anyOf" in no:
        tipos = [_limpar(opcao, defs) for opcao in no["anyOf"]]
        simples = [t["type"] for t in tipos if set(t) == {"type"}]
        if len(simples) != len(tipos):
            raise ValueError(f"união não suportada no esquema: {no['anyOf']}")
        return {"type": simples}
    limpo = {
        chave: _limpar(valor, defs)
        for chave, valor in no.items()
        if chave not in _CHAVES_DESCARTADAS and chave != "$defs"
    }
    if limpo.get("type") == "object":
        limpo["additionalProperties"] = False
        limpo["required"] = list(limpo["properties"])
    return limpo


def esquema_resposta() -> dict[str, Any]:
    """JSON Schema de `RespostaExtracao` nas regras do Bedrock.

    Sem `$ref`/`$defs`/`title`, todo objeto fechado e com todos os campos obrigatórios, um só
    nullable (`numero`).
    """
    bruto = RespostaExtracao.model_json_schema()
    esquema: dict[str, Any] = _limpar(bruto, bruto.get("$defs", {}))
    return esquema


class ValorEvidencia(BaseModel):
    numero: Decimal | None
    trecho: str
    pagina: int
    conflito: bool = False
    observacao: str = ""


class CategoriaEvidencia(BaseModel):
    valor: str
    trecho: str
    pagina: int
    conflito: bool = False


class Atestado(BaseModel):
    descricao: str
    quantidade: Decimal | None
    unidade: str | None
    trecho: str
    pagina: int


class Exigencias(BaseModel):
    """Exigências de habilitação de um edital. `None` = o edital não trata disso (ou a extração
    não achou; o eval mede a diferença)."""

    atestados: list[Atestado] = []
    capital_social_minimo: ValorEvidencia | None = None
    patrimonio_liquido_minimo: ValorEvidencia | None = None
    garantia_proposta_percentual: ValorEvidencia | None = None
    garantia_contratual_percentual: ValorEvidencia | None = None
    prazo_execucao_dias: ValorEvidencia | None = None
    visita_tecnica: CategoriaEvidencia | None = None
    consorcio: CategoriaEvidencia | None = None
    me_epp: CategoriaEvidencia | None = None
    # O `valor` de cada um é um item de DOCUMENTOS.
    documentos: list[CategoriaEvidencia] = []
