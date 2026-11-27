"""Referências de preço por serviço-padrão: casos e medianas do estimado e do contratado.

Preço comparado é sempre com BDI e na unidade canônica (preço ÷ `fator_unidade`). Chave:
(serviço, material, DN, escopo, faixa); fora de rede e escavação ela é só o serviço. Os
números vêm da UF do edital quando ela tem pelo menos `CASOS_UF` casos do estimado nessa chave;
senão, do país inteiro (abrangência `nacional`). Item `suspeito` nunca entra.
"""

import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.precos.modelos import PrecoItem
from radar.precos.servicos import Servico, servicos_padrao

Abrangencia = Literal["uf", "nacional"]

# Casos do estimado na UF para usar a UF; abaixo disso, nacional.
CASOS_UF = 5
# Rede (material, DN, escopo) só aparece com pelo menos tantos casos na abrangência escolhida.
CASOS_REDE = 3
# Item com BDI desconhecido sai do estimado quando a chave tem tantos casos sem essa marca.
CASOS_SEM_BDI_DESCONHECIDO = 5
_CENTAVO = Decimal("0.01")

type _Chave = tuple[str, str | None, int | None, str | None, str | None]


@dataclass(frozen=True)
class Referencia:
    servico: str
    rotulo: str
    linha: str
    material: str | None
    dn: int | None
    # Só em rede: material (só o tubo), assentamento ou completo.
    escopo: str | None
    # Só em escavação: faixa de profundidade.
    faixa: str | None
    unidade: str
    # De onde vêm os números: a UF do edital (`uf`) ou o país (`nacional`).
    abrangencia: Abrangencia
    uf: str | None
    casos_estimado: int
    mediana_estimado: Decimal | None
    # Só TCE-PE (contratado casado com o estimado), sem suspeitos.
    casos_contratado: int
    mediana_contratado: Decimal | None
    # Período da data-base dos preços do estimado usados (None quando a fonte não diz).
    data_base_inicio: date | None
    data_base_fim: date | None


@dataclass(frozen=True)
class _Caso:
    origem: str
    uf: str
    estimado: Decimal | None
    contratado: Decimal | None
    bdi_desconhecido: bool
    data_base: date | None


def _mediana(valores: Sequence[Decimal]) -> Decimal | None:
    if not valores:
        return None
    return Decimal(statistics.median(valores)).quantize(_CENTAVO, rounding=ROUND_HALF_UP)


def _estimados(casos: Iterable[_Caso]) -> list[_Caso]:
    com_preco = [c for c in casos if c.estimado is not None]
    sem_marca = [c for c in com_preco if not c.bdi_desconhecido]
    return sem_marca if len(sem_marca) >= CASOS_SEM_BDI_DESCONHECIDO else com_preco


def _contratados(casos: Iterable[_Caso]) -> list[Decimal]:
    return [c.contratado for c in casos if c.origem == "tce_pe" and c.contratado is not None]


def _referencia(
    servico: Servico, chave: _Chave, casos: list[_Caso], uf: str | None
) -> Referencia | None:
    na_uf = [c for c in casos if c.uf == uf] if uf is not None else []
    abrangencia: Abrangencia = "uf" if len(_estimados(na_uf)) >= CASOS_UF else "nacional"
    escolhidos = na_uf if abrangencia == "uf" else casos
    estimados = _estimados(escolhidos)
    contratados = _contratados(escolhidos)
    if not estimados and not contratados:
        return None
    if servico.de_rede and len(estimados) < CASOS_REDE:
        return None
    datas = [c.data_base for c in estimados if c.data_base is not None]
    _, material, dn, escopo, faixa = chave
    return Referencia(
        servico=servico.nome,
        rotulo=servico.rotulo,
        linha=servico.linha,
        material=material,
        dn=dn,
        escopo=escopo,
        faixa=faixa,
        unidade=servico.unidade,
        abrangencia=abrangencia,
        uf=uf if abrangencia == "uf" else None,
        casos_estimado=len(estimados),
        mediana_estimado=_mediana([c.estimado for c in estimados if c.estimado is not None]),
        casos_contratado=len(contratados),
        mediana_contratado=_mediana(contratados),
        data_base_inicio=min(datas, default=None),
        data_base_fim=max(datas, default=None),
    )


def referencias(
    sessao: Session,
    linhas: Iterable[str],
    uf: str | None,
    servicos: tuple[Servico, ...] | None = None,
) -> list[Referencia]:
    """Referências dos serviços das `linhas` do edital; vazia se nenhuma linha tem serviço.

    Ordem: serviços na ordem de `config/servicos.toml`, depois mais casos primeiro.
    """
    pedidas = set(linhas)
    escolhidos = [s for s in (servicos or servicos_padrao()) if s.linha in pedidas]
    if not escolhidos:
        return []
    por_nome = {s.nome: s for s in escolhidos}
    linhas_db = sessao.execute(
        select(
            PrecoItem.servico,
            PrecoItem.material,
            PrecoItem.dn,
            PrecoItem.escopo,
            PrecoItem.faixa,
            PrecoItem.origem,
            PrecoItem.uf,
            PrecoItem.preco_com_bdi,
            PrecoItem.preco_contratado_com_bdi,
            PrecoItem.fator_unidade,
            PrecoItem.bdi_desconhecido,
            PrecoItem.data_base,
        ).where(PrecoItem.servico.in_(por_nome), PrecoItem.suspeito.is_(False))
    ).all()
    grupos: dict[_Chave, list[_Caso]] = defaultdict(list)
    for linha in linhas_db:
        fator = linha.fator_unidade
        grupos[(linha.servico, linha.material, linha.dn, linha.escopo, linha.faixa)].append(
            _Caso(
                origem=linha.origem,
                uf=linha.uf,
                estimado=None if linha.preco_com_bdi is None else linha.preco_com_bdi / fator,
                contratado=(
                    None
                    if linha.preco_contratado_com_bdi is None
                    else linha.preco_contratado_com_bdi / fator
                ),
                bdi_desconhecido=linha.bdi_desconhecido,
                data_base=linha.data_base,
            )
        )
    ordem = {s.nome: i for i, s in enumerate(escolhidos)}
    achadas = [
        r
        for chave, casos in grupos.items()
        if (r := _referencia(por_nome[chave[0]], chave, casos, uf)) is not None
    ]
    return sorted(
        achadas,
        key=lambda r: (
            ordem[r.servico],
            -r.casos_estimado,
            -r.casos_contratado,
            r.material or "",
            r.dn or 0,
            r.escopo or "",
            r.faixa or "",
        ),
    )
