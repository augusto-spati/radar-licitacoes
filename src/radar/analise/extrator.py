"""Extração das exigências em trechos: um pedido por trecho, ancoragem e junção no fim."""

import logging
from dataclasses import dataclass
from decimal import Decimal
from importlib import resources

from pydantic import ValidationError

from radar.analise.exigencias import Exigencias, RespostaExtracao, esquema_resposta
from radar.analise.normalizacao import AchadoAncorado, ancorar, juntar
from radar.analise.provedor import ConfigIA, Pedido, Provedor
from radar.analise.trechos import dividir

logger = logging.getLogger(__name__)

SISTEMA: str = (
    resources.files("radar.analise").joinpath("prompts/extracao.md").read_text(encoding="utf-8")
)


@dataclass(frozen=True)
class Extracao:
    exigencias: Exigencias
    trechos: int
    trechos_invalidos: int
    achados_descartados: int
    truncado: bool
    tokens_entrada: int
    tokens_saida: int

    @property
    def incompleta(self) -> bool:
        return self.trechos_invalidos > 0 or self.truncado


def extrair_exigencias(
    paginas: list[str],
    provedor: Provedor,
    modelo: str,
    config: ConfigIA,
    valor_estimado: Decimal | None,
) -> Extracao:
    """Divide, pede cada trecho, valida, ancora e junta. `TetoAtingido` e `RespostaAusente`
    propagam; resposta que não valida conta em `trechos_invalidos`, sem nova tentativa."""
    todos = dividir(paginas, config.caracteres_por_trecho)
    trechos = todos[: config.max_trechos]
    truncado = len(todos) > len(trechos)
    esquema = esquema_resposta()
    ancorados: list[AchadoAncorado] = []
    invalidos = descartados = entrada = saida = 0
    for indice, trecho in enumerate(trechos, start=1):
        resposta = provedor.gerar(
            Pedido(
                modelo=modelo,
                sistema=SISTEMA,
                texto=trecho.texto,
                esquema=esquema,
                max_tokens=config.max_tokens,
            )
        )
        entrada += resposta.tokens_entrada
        saida += resposta.tokens_saida
        try:
            achados = RespostaExtracao.model_validate_json(resposta.texto).achados
        except ValidationError as erro:
            invalidos += 1
            logger.warning(
                "trecho_invalido",
                extra={
                    "trecho": indice,
                    "erros": erro.error_count(),
                    "inicio": resposta.texto[:200],
                },
            )
            continue
        bons, perdidos = ancorar(achados, trecho.texto, trecho.pagina_inicial)
        ancorados.extend(bons)
        descartados += perdidos
    exigencias, fora_do_vocabulario = juntar(ancorados, valor_estimado)
    return Extracao(
        exigencias=exigencias,
        trechos=len(trechos),
        trechos_invalidos=invalidos,
        achados_descartados=descartados + fora_do_vocabulario,
        truncado=truncado,
        tokens_entrada=entrada,
        tokens_saida=saida,
    )
