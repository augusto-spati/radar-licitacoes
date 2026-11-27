"""Contratações de fora do PNCP no histórico do órgão (etapa 7a.3): processos do TCE-PE.

A Compesa não publica no PNCP; o TCE-PE tem os processos dela com estimado e homologado, e os
contratos (instrumentos jurídicos) com a empresa contratada. Cada processo vira um caso em
`resultado_externo`, no formato do resumo de `resultado_busca`, que `historico_do_edital` junta
aos do PNCP.

Recebe os dicts como o `radar.coleta.tcepe` devolve (todos os valores como texto) e não importa
a coleta nem os preços: quem compõe é a CLI (que também diz as linhas de serviço do processo,
com o casamento que trata o U+FFFD do TCE-PE).

Regras (decisões registradas em `docs/etapas/07-historico.md`, seção TCE-PE):
- Só órgãos de `ORGAOS_TCEPE` (hoje, a Compesa: unidade jurisdicionada 802). Processo
  homologado (estágio "...Homologado" e homologado > 0) vira `homologada`; "Concluído -
  Fracassado" e "Concluído - Deserto" viram `fracassada`/`deserta` sem homologado, desconto nem
  vencedor (como o PNCP na 7a: entram nas contagens, ficam fora da régua e dos vencedores).
  Revogado, anulado e em andamento ficam de fora.
- Desconto = `1 − valorHomologado ÷ valorGlobal` (o processo inteiro: o TCE-PE não dá o
  homologado por item); orçamento sigiloso, estimado ≤ 0 ou homologado ÷ estimado fora de
  [0,3; 1,5] → sem desconto.
- Vencedor: a contratada do contrato cujo valor bate com o homologado (±2%); com vários
  contratos (lotes), o de maior valor; sem contrato que bata, o participante do processo com o
  maior valor homologado; sem nenhum, o caso entra sem vencedor. Pessoa física vira "Pessoa
  física", sem CPF.
"""

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, get_args
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from radar.editais.modelos import FONTES_EXTERNAS, Fonte, ResultadoExterno
from radar.editais.resultados import PESSOA_FISICA

FUSO = ZoneInfo("America/Sao_Paulo")
FONTE_TCEPE = "tce_pe"
# Origem de um caso do histórico, como a API e a tela mostram.
OrigemHistorico = Literal["PNCP", "TCE-PE"]
ROTULOS_FONTE: dict[str, OrigemHistorico] = {Fonte.pncp.value: "PNCP", FONTE_TCEPE: "TCE-PE"}
# Toda fonte que entra no histórico (o PNCP e as externas) tem rótulo, e todo rótulo é usado:
# uma fonte externa nova sem rótulo quebra no import, não numa resposta da API.
if set(ROTULOS_FONTE) != {Fonte.pncp.value, *FONTES_EXTERNAS} or set(ROTULOS_FONTE.values()) != set(
    get_args(OrigemHistorico)
):
    raise RuntimeError(f"ROTULOS_FONTE desalinhado das fontes do histórico: {ROTULOS_FONTE}")
# Unidade jurisdicionada do TCE-PE → (CNPJ, nome do órgão). O CNPJ é o que os editais do órgão
# têm em `edital.orgao_cnpj` (o da Compesa vem de `radar.coleta.compesa.CNPJ`).
ORGAOS_TCEPE: dict[str, tuple[str, str]] = {"802": ("09769035000164", "COMPESA")}
UF_TCEPE = "PE"
TOLERANCIA_VALOR = Decimal("0.02")
RAZAO_PLAUSIVEL_MIN = Decimal("0.3")  # homologado ÷ estimado aceito para calcular o desconto
RAZAO_PLAUSIVEL_MAX = Decimal("1.5")
_QUATRO_CASAS = Decimal("0.0001")
_NUMERO_LICITACAO = re.compile(r"(\d+)\s*/\s*(\d{4})")

Registro = dict[str, Any]


@dataclass(frozen=True)
class CasoExterno:
    fonte: str
    id_na_fonte: str
    orgao_cnpj: str
    orgao_nome: str
    uf: str
    objeto: str
    modalidade_nome: str | None
    numero: str | None
    linhas: list[str]
    data: datetime
    data_resultado: datetime | None
    situacao: str
    valor_estimado: Decimal | None
    valor_homologado: Decimal | None
    desconto: Decimal | None
    vencedor_ni: str | None
    vencedor_nome: str | None
    vencedor_porte: str | None


@dataclass(frozen=True)
class VencedorExterno:
    ni: str | None
    nome: str


def _decimal(valor: object) -> Decimal | None:
    if valor is None or isinstance(valor, bool):
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    try:
        numero = Decimal(texto)
    except InvalidOperation:
        return None
    return numero if numero.is_finite() else None


def _data(valor: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(valor)[:10]) if valor else None
    except ValueError:
        return None


def _texto(valor: object) -> str:
    return re.sub(r"\s+", " ", str(valor or "")).strip()


def numero_licitacao(texto: object) -> str | None:
    """ "LC 213/2026 CEL-1" → "213/2026"; "053" e "2026" (TCE-PE) passam por `numero_tcepe`."""
    achado = _NUMERO_LICITACAO.search(str(texto or ""))
    return None if achado is None else f"{int(achado.group(1))}/{achado.group(2)}"


def numero_tcepe(processo: Registro) -> str | None:
    """`numeroModalidade`/`anoModalidade` ("053", "2026" → "53/2026"): o número da licitação
    no portal da Compesa ("LC 053/2026 ..."). Não numérico → None."""
    numero = _texto(processo.get("numeroModalidade"))
    ano = _texto(processo.get("anoModalidade"))
    if not numero.isdigit() or not (len(ano) == 4 and ano.isdigit()):
        return None
    return f"{int(numero)}/{ano}"


def orgao_tcepe(processo: Registro) -> tuple[str, str] | None:
    """(CNPJ, nome) do órgão, se a unidade jurisdicionada do processo está no histórico."""
    return ORGAOS_TCEPE.get(_texto(processo.get("unidadeJurisdicionadaId")))


def homologado_tcepe(processo: Registro) -> bool:
    """Estágio "Concluído - Adjudicado/Homologado" e homologado positivo (U+FFFD tolerado)."""
    valor = _decimal(processo.get("valorHomologado"))
    return (
        valor is not None
        and valor > 0
        and "homologado" in _texto(processo.get("estagioNome")).lower()
    )


def situacao_tcepe(processo: Registro) -> str | None:
    """`homologada`, `fracassada`, `deserta`, ou None (revogado, anulado, em andamento...)."""
    if homologado_tcepe(processo):
        return "homologada"
    estagio = _texto(processo.get("estagioNome")).lower()
    if "fracassad" in estagio:
        return "fracassada"
    if "desert" in estagio:
        return "deserta"
    return None


def alvo_historico_tcepe(processo: Registro) -> bool:
    """Processo de órgão do histórico, homologado, fracassado ou deserto (as linhas de serviço
    quem diz é a CLI)."""
    return orgao_tcepe(processo) is not None and situacao_tcepe(processo) is not None


def _bate(valor: Decimal, alvo: Decimal) -> bool:
    return abs(valor - alvo) <= alvo * TOLERANCIA_VALOR


def _participante(registros: Sequence[Registro]) -> VencedorExterno | None:
    """O primeiro participante com nome; PF sem documento (vem mascarado mesmo)."""
    for registro in registros:
        nome = _texto(registro.get("participanteNome"))
        if not nome:
            continue
        documento = "".join(
            c for c in str(registro.get("participanteDocumento") or "") if c.isdigit()
        )
        tipo = _texto(registro.get("tipoParticipanteId")).upper()
        if tipo == "PF" or (tipo != "PJ" and len(documento) != 14):
            return VencedorExterno(None, PESSOA_FISICA)
        return VencedorExterno(documento if len(documento) == 14 else None, nome)
    return None


def contratos_do_processo(processo: Registro, instrumentos: Sequence[Registro]) -> list[Registro]:
    """Contratos que são deste processo: o que bate com o homologado (±2%), o mais próximo;
    senão, todos juntos se a soma bater (lotes com vencedores diferentes); senão, nenhum.

    O filtro do TCE-PE por número pode trazer contrato de outro processo: o valor confere.
    """
    alvo = _decimal(processo.get("valorHomologado"))
    if alvo is None or alvo <= 0:
        return []
    com_valor = [
        (valor, i)
        for i in instrumentos
        if (valor := _decimal(i.get("ValorGlobalAtualizado"))) is not None and valor > 0
    ]
    batem = [(abs(valor - alvo), i) for valor, i in com_valor if _bate(valor, alvo)]
    if batem:
        return [min(batem, key=lambda par: par[0])[1]]
    if len(com_valor) > 1 and _bate(sum((v for v, _ in com_valor), Decimal(0)), alvo):
        return [i for _, i in com_valor]
    return []


def vencedor_tcepe(
    contratos: Sequence[tuple[Registro, Sequence[Registro]]],
    participantes_processo: Sequence[Registro] = (),
) -> VencedorExterno | None:
    """Contratada do contrato de maior valor (dos já escolhidos por `contratos_do_processo`);
    sem contratada, o participante do processo com maior valor homologado."""
    ordenados = sorted(
        contratos,
        key=lambda par: _decimal(par[0].get("ValorGlobalAtualizado")) or Decimal(0),
        reverse=True,
    )
    for _, participantes in ordenados:
        if (vencedor := _participante(participantes)) is not None:
            return vencedor
    homologados = [
        (valor, p)
        for p in participantes_processo
        if (valor := _decimal(p.get("valorHomologado"))) is not None and valor > 0
    ]
    if not homologados:
        return None
    return _participante([max(homologados, key=lambda par: par[0])[1]])


def caso_tcepe(
    processo: Registro, linhas: Sequence[str], vencedor: VencedorExterno | None
) -> CasoExterno | None:
    """O caso do histórico, ou None se o processo não é alvo (órgão, situação, linha, data).

    Fracassado ou deserto: sem homologado, desconto nem vencedor (o `vencedor` é ignorado); o
    estimado fica, para a tela mostrar como no PNCP.
    """
    orgao = orgao_tcepe(processo)
    situacao = situacao_tcepe(processo)
    processo_id = _texto(processo.get("processoContratacaoId"))
    data = (
        _data(processo.get("dataSessaoAbertura"))
        or _data(processo.get("dataPublicacao"))
        or _data(processo.get("dataResultado"))
    )
    if orgao is None or situacao is None or not linhas or not processo_id:
        return None
    if data is None:
        return None
    homologado = _decimal(processo.get("valorHomologado")) if situacao == "homologada" else None
    if situacao != "homologada":
        vencedor = None
    estimado = _decimal(processo.get("valorGlobal"))
    if str(processo.get("orcamentoSigiloso") or "0").strip() == "1" or (
        estimado is not None and estimado <= 0
    ):
        estimado = None
    desconto = None
    if estimado is not None and homologado is not None:
        # Razão fora de [0,3; 1,5] é erro no dado aberto (a ata 93/2025 da Compesa veio com o
        # estimado por unidade: -9.570%). O caso conta para o órgão, mas fica fora da régua. A
        # mesma faixa marca `suspeito` no preço contratado da 7b.1.
        if RAZAO_PLAUSIVEL_MIN <= homologado / estimado <= RAZAO_PLAUSIVEL_MAX:
            desconto = (1 - homologado / estimado).quantize(_QUATRO_CASAS)
    resultado = _data(processo.get("dataResultado"))
    return CasoExterno(
        fonte=FONTE_TCEPE,
        id_na_fonte=processo_id,
        orgao_cnpj=orgao[0],
        orgao_nome=orgao[1],
        uf=UF_TCEPE,
        objeto=_texto(processo.get("objeto")),
        modalidade_nome=_texto(processo.get("modalidadeNome")) or None,
        numero=numero_tcepe(processo),
        linhas=list(linhas),
        data=data,
        data_resultado=None if resultado is None else resultado.replace(tzinfo=FUSO),
        situacao=situacao,
        valor_estimado=estimado,
        valor_homologado=homologado,
        desconto=desconto,
        vencedor_ni=None if vencedor is None else vencedor.ni,
        vencedor_nome=None if vencedor is None else vencedor.nome,
        vencedor_porte=None,  # o TCE-PE não informa porte
    )


def vencedor_gravado(
    sessao: Session, fonte: str, id_na_fonte: str, valor_homologado: object
) -> VencedorExterno | None:
    """Vencedor já gravado do caso, se o homologado não mudou: a rodada não busca de novo."""
    alvo = _decimal(valor_homologado)
    linha = sessao.execute(
        select(
            ResultadoExterno.vencedor_ni,
            ResultadoExterno.vencedor_nome,
            ResultadoExterno.valor_homologado,
        ).where(ResultadoExterno.fonte == fonte, ResultadoExterno.id_na_fonte == id_na_fonte)
    ).first()
    if linha is None or linha.vencedor_nome is None or alvo is None:
        return None
    if linha.valor_homologado != alvo:
        return None
    return VencedorExterno(linha.vencedor_ni, linha.vencedor_nome)


def gravar_casos_externos(sessao: Session, casos: Iterable[CasoExterno]) -> int:
    """Upsert por (fonte, id_na_fonte); o mesmo processo duas vezes no lote: vale o último.

    O vencedor é um par (CNPJ, nome, porte juntos): caso `homologada` que chega sem vencedor
    (resposta instável do TCE-PE) mantém os três antigos; com vencedor, os três novos. Caso que
    deixou de ser `homologada` (fracassada, deserta) fica sem vencedor. Sem commit.
    """
    unicos = {(c.fonte, c.id_na_fonte): asdict(c) for c in casos}
    if not unicos:
        return 0
    linhas = list(unicos.values())
    insercao = insert(ResultadoExterno).values(linhas)
    vencedor = ("vencedor_ni", "vencedor_nome", "vencedor_porte")
    tabela = ResultadoExterno.__table__.c
    novo = insercao.excluded
    manter_antigo = and_(novo.situacao == "homologada", novo.vencedor_nome.is_(None))
    atualizar: dict[str, Any] = {
        c: case((manter_antigo, tabela[c]), else_=novo[c]) if c in vencedor else novo[c]
        for c in linhas[0]
        if c not in ("fonte", "id_na_fonte")
    }
    atualizar["gravado_em"] = func.now()
    sessao.execute(
        insercao.on_conflict_do_update(index_elements=["fonte", "id_na_fonte"], set_=atualizar)
    )
    return len(linhas)


def casos_externos_gravados(sessao: Session, fonte: str) -> set[str]:
    return set(
        sessao.scalars(
            select(ResultadoExterno.id_na_fonte).where(ResultadoExterno.fonte == fonte)
        ).all()
    )


def apagar_casos_externos(sessao: Session, fonte: str, ids: Collection[str]) -> int:
    """Apaga os casos (processo que saiu do alvo: revogado, anulado, sem linha). Sem commit."""
    if not ids:
        return 0
    resultado = sessao.execute(
        delete(ResultadoExterno).where(
            ResultadoExterno.fonte == fonte, ResultadoExterno.id_na_fonte.in_(list(ids))
        )
    )
    return int(getattr(resultado, "rowcount", 0) or 0)
