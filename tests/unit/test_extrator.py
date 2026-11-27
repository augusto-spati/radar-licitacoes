import json
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

import pytest

from radar.analise.custos import TetoAtingido
from radar.analise.exigencias import CAMPOS, esquema_resposta
from radar.analise.extrator import SISTEMA, extrair_exigencias
from radar.analise.provedor import ConfigIA, Pedido, PrecoModelo, Resposta

MODELO = "amazon.nova-lite-v1:0"
CONFIG = ConfigIA(
    regiao="us-east-2",
    modelo_padrao=MODELO,
    teto_mensal_usd=Decimal("2"),
    max_editais_por_rodada=10,
    caracteres_por_trecho=60000,
    max_trechos=15,
    max_tokens=4096,
    modelos={
        MODELO: PrecoModelo(
            modo_saida="ferramenta",
            entrada_usd_mtok=Decimal("0.06"),
            saida_usd_mtok=Decimal("0.24"),
        )
    },
)


def _achados(*achados: tuple[str, str, float | None, str, str]) -> str:
    return json.dumps(
        {
            "achados": [
                {"campo": c, "valor": v, "numero": n, "unidade": u, "trecho": t}
                for c, v, n, u, t in achados
            ]
        }
    )


class ProvedorPorTrecho:
    """Responde conforme o texto do trecho; guarda os pedidos."""

    def __init__(self, responder: Callable[[int, Pedido], str]) -> None:
        self.responder = responder
        self.pedidos: list[Pedido] = []

    def gerar(self, pedido: Pedido) -> Resposta:
        self.pedidos.append(pedido)
        texto = self.responder(len(self.pedidos), pedido)
        return Resposta(texto=texto, tokens_entrada=100, tokens_saida=10, latencia_ms=1)


# Duas páginas de ~40 mil caracteres: cada uma vira um trecho (com sobreposição, 2 chamadas).
PAGINA_1 = "Capital social mínimo de R$ 100.000,00. " + "a" * 40_000
PAGINA_2 = "A visita técnica é obrigatória. " + "b" * 40_000


def _responder_dois(numero: int, pedido: Pedido) -> str:
    if numero == 1:
        return _achados(
            (
                "capital_social_minimo",
                "",
                100000,
                "reais",
                "Capital social mínimo de R$ 100.000,00.",
            )
        )
    return _achados(("visita_tecnica", "obrigatoria", None, "", "A visita técnica é obrigatória."))


def test_dois_trechos_juntam() -> None:
    provedor = ProvedorPorTrecho(_responder_dois)
    extracao = extrair_exigencias([PAGINA_1, PAGINA_2], provedor, MODELO, CONFIG, None)
    assert extracao.trechos == 2
    assert extracao.exigencias.capital_social_minimo is not None
    assert extracao.exigencias.capital_social_minimo.pagina == 1
    assert extracao.exigencias.visita_tecnica is not None
    assert extracao.exigencias.visita_tecnica.pagina == 2
    assert (extracao.tokens_entrada, extracao.tokens_saida) == (200, 20)
    assert not extracao.incompleta


def test_resposta_invalida_marca_incompleta() -> None:
    def responder(numero: int, pedido: Pedido) -> str:
        return _responder_dois(1, pedido) if numero == 1 else "não sei"

    extracao = extrair_exigencias(
        [PAGINA_1, PAGINA_2], ProvedorPorTrecho(responder), MODELO, CONFIG, None
    )
    assert extracao.trechos_invalidos == 1
    assert extracao.incompleta
    assert extracao.exigencias.capital_social_minimo is not None


def test_achado_sem_lastro_contado() -> None:
    def responder(numero: int, pedido: Pedido) -> str:
        return _achados(("consorcio", "vedado", None, "", "é vedado o consórcio"))

    extracao = extrair_exigencias([PAGINA_1], ProvedorPorTrecho(responder), MODELO, CONFIG, None)
    assert extracao.achados_descartados == 1
    assert extracao.exigencias.consorcio is None


def test_trunca_em_max_trechos() -> None:
    provedor = ProvedorPorTrecho(lambda n, p: '{"achados": []}')
    paginas = ["x" * 50_000] * 4
    extracao = extrair_exigencias(paginas, provedor, MODELO, replace(CONFIG, max_trechos=2), None)
    assert len(provedor.pedidos) == 2
    assert extracao.truncado
    assert extracao.incompleta


def test_sistema_e_esquema_vao_no_pedido() -> None:
    provedor = ProvedorPorTrecho(lambda n, p: '{"achados": []}')
    extrair_exigencias(["texto"], provedor, MODELO, CONFIG, None)
    [pedido] = provedor.pedidos
    assert pedido.esquema == esquema_resposta()
    assert pedido.sistema == SISTEMA
    assert pedido.modelo == MODELO
    assert pedido.max_tokens == 4096
    assert pedido.texto == "[[pagina 1]]\ntexto"


def test_teto_propaga() -> None:
    class ProvedorComTeto:
        def gerar(self, pedido: Pedido) -> Resposta:
            raise TetoAtingido("teto")

    with pytest.raises(TetoAtingido):
        extrair_exigencias(["texto"], ProvedorComTeto(), MODELO, CONFIG, None)


def test_valor_estimado_chega_na_juncao() -> None:
    def responder(numero: int, pedido: Pedido) -> str:
        return _achados(("capital_social_minimo", "percentual", 10, "%", "Capital social mínimo"))

    extracao = extrair_exigencias(
        [PAGINA_1], ProvedorPorTrecho(responder), MODELO, CONFIG, Decimal("1000000")
    )
    assert extracao.exigencias.capital_social_minimo is not None
    assert extracao.exigencias.capital_social_minimo.numero == Decimal("100000")


def test_prompt_empacotado() -> None:
    assert SISTEMA.strip()
    for campo in CAMPOS:
        assert campo in SISTEMA
