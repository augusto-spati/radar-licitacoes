"""Referências de preço por serviço-padrão: casos e medianas do estimado e do contratado.

Preço comparado é sempre com BDI e na unidade canônica (preço ÷ `fator_unidade`). Chave:
(serviço, material, DN, escopo, faixa); fora de rede e escavação ela é só o serviço. Os
números vêm da UF do edital quando ela tem pelo menos `CASOS_UF` processos do estimado nessa
chave; senão, de todas as UFs com dados. Item `suspeito` nunca entra.

Caso é processo, não item: a mediana de cada chave é a mediana das medianas de cada processo
(um orçamento grande com muitos itens iguais não domina), e todo corte conta processos. `ufs`
diz de onde vieram os números e `origens` de que fonte (TCE-PE, planilhas do PNCP); `abrangencia`
é `uf` quando todos são da UF do edital e `outras_ufs` quando não.
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
from radar.precos.tcepe import ORIGEM as ORIGEM_TCEPE
from radar.precos.tcepe import processo_da_chave

Abrangencia = Literal["uf", "outras_ufs"]

# Processos do estimado na UF para usar só a UF; abaixo disso, todas as UFs com dados.
CASOS_UF = 5
# Rede (material, DN, escopo) só aparece com pelo menos tantos processos na abrangência.
CASOS_REDE = 3
# Abaixo de tantos processos do estimado, a referência vem marcada com `poucos_casos`.
CASOS_MINIMOS = 3
# Item com BDI desconhecido sai do estimado quando a chave tem tantos processos sem essa marca.
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
    # `uf`: todos os números são da UF do edital; `outras_ufs`: não (ver `ufs`).
    abrangencia: Abrangencia
    # UFs de onde vieram os números, em ordem.
    ufs: tuple[str, ...]
    # Origens do estimado ("tce_pe", "pncp_planilha"), em ordem; o contratado é só do TCE-PE.
    origens: tuple[str, ...]
    processos_estimado: int
    itens_estimado: int
    # Mediana das medianas por processo.
    mediana_estimado: Decimal | None
    # Só TCE-PE (contratado casado com o estimado), sem suspeitos.
    processos_contratado: int
    itens_contratado: int
    mediana_contratado: Decimal | None
    # Estimado dos mesmos itens que têm contratado (mesma população, para comparar).
    mediana_estimado_contratados: Decimal | None
    # Menos de `CASOS_MINIMOS` processos do estimado.
    poucos_casos: bool
    # Período da data-base dos preços do estimado usados (None quando a fonte não diz).
    data_base_inicio: date | None
    data_base_fim: date | None


@dataclass(frozen=True)
class _Caso:
    # (origem, id do processo): no TCE-PE o prefixo de `chave_origem`, porque o rótulo
    # `processo` se repete; nas outras origens, o `processo`.
    processo: tuple[str, str]
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


def _processos(casos: Iterable[_Caso]) -> int:
    return len({c.processo for c in casos})


def _mediana_por_processo(pares: Iterable[tuple[tuple[str, str], Decimal]]) -> Decimal | None:
    """Mediana das medianas de cada processo."""
    por_processo: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    for processo, valor in pares:
        por_processo[processo].append(valor)
    return _mediana([statistics.median(valores) for valores in por_processo.values()])


def _estimados(casos: Iterable[_Caso]) -> list[_Caso]:
    com_preco = [c for c in casos if c.estimado is not None]
    sem_marca = [c for c in com_preco if not c.bdi_desconhecido]
    return sem_marca if _processos(sem_marca) >= CASOS_SEM_BDI_DESCONHECIDO else com_preco


def _contratados(casos: Iterable[_Caso]) -> list[_Caso]:
    return [c for c in casos if c.origem == ORIGEM_TCEPE and c.contratado is not None]


def _referencia(
    servico: Servico, chave: _Chave, casos: list[_Caso], uf: str | None
) -> Referencia | None:
    na_uf = [c for c in casos if c.uf == uf] if uf is not None else []
    escolhidos = na_uf if _processos(_estimados(na_uf)) >= CASOS_UF else casos
    estimados = _estimados(escolhidos)
    contratados = _contratados(escolhidos)
    processos_estimado = _processos(estimados)
    if processos_estimado == 0 and not contratados:
        return None
    if servico.de_rede and processos_estimado < CASOS_REDE:
        return None
    ufs = tuple(sorted({c.uf for c in [*estimados, *contratados]}))
    abrangencia: Abrangencia = "uf" if uf is not None and ufs == (uf,) else "outras_ufs"
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
        ufs=ufs,
        origens=tuple(sorted({c.origem for c in estimados})),
        processos_estimado=processos_estimado,
        itens_estimado=len(estimados),
        mediana_estimado=_mediana_por_processo(
            (c.processo, c.estimado) for c in estimados if c.estimado is not None
        ),
        processos_contratado=_processos(contratados),
        itens_contratado=len(contratados),
        mediana_contratado=_mediana_por_processo(
            (c.processo, c.contratado) for c in contratados if c.contratado is not None
        ),
        mediana_estimado_contratados=_mediana_por_processo(
            (c.processo, c.estimado) for c in contratados if c.estimado is not None
        ),
        poucos_casos=processos_estimado < CASOS_MINIMOS,
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

    Ordem: serviços na ordem de `config/servicos.toml`, depois mais processos primeiro. Chave
    sem nenhum processo não aparece; com menos de `CASOS_MINIMOS`, vem com `poucos_casos`.
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
            PrecoItem.chave_origem,
            PrecoItem.processo,
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
                processo=(
                    linha.origem,
                    processo_da_chave(linha.chave_origem)
                    if linha.origem == ORIGEM_TCEPE
                    else linha.processo,
                ),
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
            -r.processos_estimado,
            -r.processos_contratado,
            r.material or "",
            r.dn or 0,
            r.escopo or "",
            r.faixa or "",
        ),
    )
