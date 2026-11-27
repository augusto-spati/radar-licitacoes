from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from radar.analise.exigencias import (
    DOCUMENTOS,
    Achado,
    Atestado,
    CategoriaEvidencia,
    Exigencias,
    RespostaExtracao,
    ValorEvidencia,
    esquema_resposta,
)


def _percorrer(no: Any) -> list[dict[str, Any]]:
    """Todos os dicionários do esquema, em profundidade."""
    if isinstance(no, dict):
        filhos = [no]
        for valor in no.values():
            filhos.extend(_percorrer(valor))
        return filhos
    if isinstance(no, list):
        return [d for item in no for d in _percorrer(item)]
    return []


def test_esquema_resposta_regras_bedrock() -> None:
    esquema = esquema_resposta()
    nos = _percorrer(esquema)
    proibidas = {"$ref", "$defs", "title", "minimum", "maxLength"}
    nullables = 0
    for no in nos:
        assert not (proibidas & no.keys()), no
        if no.get("type") == "object":
            assert no["additionalProperties"] is False
            assert sorted(no["required"]) == sorted(no["properties"])
        tipo = no.get("type")
        if isinstance(tipo, list) and "null" in tipo:
            nullables += 1
        assert "anyOf" not in no
    assert nullables == 1


def test_esquema_lista_os_campos() -> None:
    campo = esquema_resposta()["properties"]["achados"]["items"]["properties"]["campo"]
    assert "capital_social_minimo" in campo["enum"]
    assert len(campo["enum"]) == 10


def test_achado_extra_proibido() -> None:
    with pytest.raises(ValidationError):
        Achado.model_validate(
            {
                "campo": "consorcio",
                "valor": "vedado",
                "numero": None,
                "unidade": "",
                "trecho": "é vedada a participação de consórcio",
                "pagina": 3,
            }
        )


def test_achado_sem_numero_vale_null() -> None:
    # Na sonda, modelos no modo ferramenta omitiram "numero" quando nulo.
    resposta = RespostaExtracao.model_validate_json(
        '{"achados": [{"campo": "visita_tecnica", "valor": "facultativa", "unidade": "",'
        ' "trecho": "A visita técnica é facultativa."}]}'
    )
    assert resposta.achados[0].numero is None


def test_documentos_sao_onze() -> None:
    assert len(DOCUMENTOS) == 11
    assert "certidao_fgts" in DOCUMENTOS


def test_exigencias_ida_e_volta_json() -> None:
    e = Exigencias(
        atestados=[
            Atestado(
                descricao="pesquisa de vazamentos",
                quantidade=Decimal("1000"),
                unidade="km",
                trecho="pesquisa de vazamentos em 1.000 km",
                pagina=12,
            )
        ],
        capital_social_minimo=ValorEvidencia(
            numero=Decimal("250000"), trecho="capital social mínimo", pagina=4
        ),
        visita_tecnica=CategoriaEvidencia(valor="facultativa", trecho="visita", pagina=5),
        documentos=[CategoriaEvidencia(valor="certidao_fgts", trecho="FGTS", pagina=6)],
    )
    assert Exigencias.model_validate_json(e.model_dump_json()) == e


@pytest.mark.parametrize("numero", ["NaN", "Infinity"])
def test_numero_nan_ou_infinito_recusado(numero: object) -> None:
    bruto = (
        '{"achados": [{"campo": "capital_social_minimo", "valor": "", "numero": '
        + (str(numero) if not isinstance(numero, str) else numero)
        + ', "unidade": "reais", "trecho": "capital social mínimo de R$ 1"}]}'
    )
    with pytest.raises(ValidationError):
        RespostaExtracao.model_validate_json(bruto)


def test_numero_nan_python_recusado() -> None:
    with pytest.raises(ValidationError):
        Achado(campo="capital_social_minimo", valor="", numero=float("nan"), unidade="", trecho="x")


def test_numero_negativo_vira_nulo_sem_invalidar_o_trecho() -> None:
    bruto = (
        '{"achados": [{"campo": "capital_social_minimo", "valor": "", "numero": -250000,'
        ' "unidade": "reais", "trecho": "capital social mínimo de R$ 250.000,00"},'
        ' {"campo": "consorcio", "valor": "vedado", "numero": null, "unidade": "",'
        ' "trecho": "é vedada a participação em consórcio"}]}'
    )
    resposta = RespostaExtracao.model_validate_json(bruto)
    assert [a.campo for a in resposta.achados] == ["capital_social_minimo", "consorcio"]
    assert resposta.achados[0].numero is None  # vira `verificar` na comparação
