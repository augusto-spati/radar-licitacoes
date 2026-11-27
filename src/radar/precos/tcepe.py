"""Interpreta os registros crus do TCE-PE e grava os itens com serviço-padrão em `preco_item`.

Recebe os dicts como o `radar.coleta.tcepe` devolve (todos os valores como texto) e não
importa a coleta: quem compõe é a CLI.

- Estimado: `Remessa_ProcessoContratacaoOrcamento`. O `ValorUnitario` já inclui o BDI (na
  amostra de Pedra, Σ `ValorTotal` = `valorGlobal`), então `preco_com_bdi = ValorUnitario` e
  o campo `BDI` é só informação, guardado como fração (< 1 já é fração; ≥ 1 é percentual).
- Contratado: `Remessa_InstrumentoJuridico` não traz o id do processo, e o filtro por número
  pode trazer contrato de outro processo. Vale o contrato cujo total bate com o
  `valorHomologado` (±2%); se nenhum bater sozinho, todos juntos (processo em lotes com mais
  de um vencedor); senão, o item fica só com o estimado.
- Os itens do contrato não têm número: casam por descrição normalizada + quantidade e, quando
  as duas listas têm o mesmo tamanho, pela mesma posição com a mesma descrição.
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from radar.precos.modelos import Origem, PrecoItem, PrecoItemNovo
from radar.precos.servicos import (
    CURINGA,
    Servico,
    classificar,
    sem_acento,
    termos_processo_padrao,
)

ORIGEM: Origem = "tce_pe"
UF = "PE"
TOLERANCIA_VALOR = Decimal("0.02")
RAZAO_MIN = Decimal("0.3")
RAZAO_MAX = Decimal("1.5")

Registro = dict[str, Any]

# --- Pré-filtro do processo (mesma ideia do `prefiltrar` da 7a, sem importar a coleta) ---

_VAZIAS = frozenset(
    "a o as os e de da do das dos em na no nas nos para por com ao aos um uma".split()
)
_PLURAIS = (("oes", "ao"), ("aes", "ao"), ("ais", "al"), ("eis", "el"), ("ois", "ol"), ("ns", "m"))
_PALAVRA = re.compile(rf"[a-z0-9{CURINGA}]+")


def _singular(palavra: str) -> str:
    if len(palavra) <= 3:
        return palavra
    for plural, singular in _PLURAIS:
        if palavra.endswith(plural):
            return palavra[: -len(plural)] + singular
    if palavra.endswith("s"):
        palavra = palavra[:-1]
    return palavra[:-1] if len(palavra) > 3 and palavra.endswith("e") else palavra


def _normalizar_objeto(texto: str) -> str:
    """Sem acento, só letras/números/U+FFFD, sem palavras vazias, cada palavra no singular."""
    palavras = _PALAVRA.findall(sem_acento(texto))
    return " ".join(_singular(p) for p in palavras if p not in _VAZIAS)


def _padrao_objeto(termo: str) -> re.Pattern[str] | None:
    normalizado = _normalizar_objeto(termo.replace(CURINGA, ""))
    if not normalizado:
        return None
    partes = [f"[{re.escape(c)}{CURINGA}]" if c.isalnum() else re.escape(c) for c in normalizado]
    return re.compile("".join(partes))


def processo_de_saneamento(processo: Registro, termos: Sequence[str] | None = None) -> bool:
    """O `objeto` contém algum termo (sem acento, no singular; U+FFFD vale por uma letra).

    Sem `termos`, usa `[processos] termos` de `config/servicos.toml`.
    """
    objeto = processo.get("objeto")
    if not isinstance(objeto, str):
        return False
    if termos is None:
        termos = termos_processo_padrao()
    texto = _normalizar_objeto(objeto)
    padroes = [p for p in (_padrao_objeto(t) for t in termos) if p is not None]
    return any(p.search(texto) for p in padroes)


def processo_homologado(processo: Registro) -> bool:
    """Estágio "...Homologado" e valor homologado positivo."""
    valor = _decimal(processo.get("valorHomologado"))
    estagio = sem_acento(str(processo.get("estagioNome") or ""))
    return valor is not None and valor > 0 and "homologado" in estagio


# --- Conversões ---


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


def bdi_fracao(valor: object) -> Decimal | None:
    """BDI como fração: "0.2467" → 0.2467; "20.9700" → 0.2097; vazio ou negativo → None."""
    numero = _decimal(valor)
    if numero is None or numero < 0:
        return None
    return numero if numero < 1 else numero / 100


def _data(valor: object) -> date | None:
    try:
        return date.fromisoformat(str(valor)[:10]) if valor else None
    except ValueError:
        return None


def _texto(valor: object) -> str | None:
    texto = str(valor).strip() if valor is not None else ""
    return texto or None


_MUNICIPIO = re.compile(r"^prefeitura municipal (?:de |da |do |das |dos )?(.+)$")


def _municipio(orgao: str) -> str | None:
    """ "Prefeitura Municipal da Pedra" → "Pedra"; outros órgãos (Compesa, SAAE) → None."""
    limpo = re.sub(r"\s+", " ", orgao).strip()
    achado = _MUNICIPIO.match(sem_acento(limpo))
    if achado is None:
        return None
    return limpo[len(limpo) - len(achado.group(1)) :]


# --- Contrato do processo ---


def _total_contrato(instrumento: Registro, itens: list[Registro]) -> list[Decimal]:
    totais = []
    if (global_ := _decimal(instrumento.get("ValorGlobalAtualizado"))) is not None:
        totais.append(global_)
    soma = [v for v in (_decimal(i.get("ValorTotal")) for i in itens) if v is not None]
    if soma:
        totais.append(sum(soma, Decimal(0)))
    return totais


def _bate(total: Decimal, alvo: Decimal) -> bool:
    return abs(total - alvo) <= alvo * TOLERANCIA_VALOR


def escolher_contrato(
    processo: Registro, contratos: Sequence[tuple[Registro, list[Registro]]]
) -> list[Registro] | None:
    """Itens do contrato do processo, ou None se nenhum total bater com o homologado (±2%).

    Um contrato bate se o `ValorGlobalAtualizado` ou a soma dos itens bater. Entre vários, o
    mais próximo. Se nenhum bater sozinho, vale a soma de todos (lotes com vencedores
    diferentes), com os itens de todos.
    """
    alvo = _decimal(processo.get("valorHomologado"))
    if alvo is None or alvo <= 0 or not contratos:
        return None
    candidatos: list[tuple[Decimal, list[Registro]]] = []
    for instrumento, itens in contratos:
        diferencas = [abs(t - alvo) for t in _total_contrato(instrumento, itens) if _bate(t, alvo)]
        if diferencas and itens:
            candidatos.append((min(diferencas), itens))
    if candidatos:
        return min(candidatos, key=lambda c: c[0])[1]
    if len(contratos) > 1:
        todos = [item for _, itens in contratos for item in itens]
        soma = sum(
            (v for v in (_decimal(i.get("ValorTotal")) for i in todos) if v is not None),
            Decimal(0),
        )
        if todos and _bate(soma, alvo):
            return todos
    return None


def _descricao_normalizada(texto: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", sem_acento(str(texto or ""))))


def casar_itens(orcamento: list[Registro], contrato: list[Registro]) -> dict[int, Registro]:
    """Índice do item do orçamento → item do contrato.

    Primeiro por descrição normalizada + quantidade (repetidos são consumidos em ordem); o que
    sobrar, pela mesma posição quando as listas têm o mesmo tamanho e a descrição é a mesma.
    """
    livres: dict[tuple[str, Decimal | None], list[int]] = defaultdict(list)
    for j, item in enumerate(contrato):
        chave = (
            _descricao_normalizada(item.get("itemDescricao")),
            _decimal(item.get("Quantidade")),
        )
        livres[chave].append(j)
    usados: set[int] = set()
    casados: dict[int, Registro] = {}
    for i, item in enumerate(orcamento):
        quantidade = _decimal(item.get("Quantidade"))
        fila = livres.get((_descricao_normalizada(item.get("itemDescricao")), quantidade))
        if quantidade is not None and fila:
            j = fila.pop(0)
            usados.add(j)
            casados[i] = contrato[j]
    if len(orcamento) == len(contrato):
        for i, item in enumerate(orcamento):
            if i in casados or i in usados:
                continue
            mesma = _descricao_normalizada(item.get("itemDescricao")) == _descricao_normalizada(
                contrato[i].get("itemDescricao")
            )
            if mesma:
                usados.add(i)
                casados[i] = contrato[i]
    return casados


# --- Itens ---


def _chave(processo_id: str, item: Registro, posicao: int) -> str:
    if item_id := _texto(item.get("itemId")):
        return f"{processo_id}:{item_id}"
    lote = _texto(item.get("loteId")) or "-"
    numero = _texto(item.get("itemNumero")) or f"pos{posicao}"
    return f"{processo_id}:{lote}:{numero}"


def itens_de_processo(
    processo: Registro,
    orcamento: list[Registro],
    contratos: Sequence[tuple[Registro, list[Registro]]],
    termos: Sequence[str] | None = None,
    servicos: tuple[Servico, ...] | None = None,
) -> list[PrecoItemNovo]:
    """Itens do orçamento que casam com um serviço-padrão, com o contratado quando casa.

    Processo fora do pré-filtro de saneamento devolve lista vazia. Item sem preço positivo fica
    de fora. Razão contratado ÷ estimado fora de [0,3; 1,5] marca `suspeito`.
    """
    if not processo_de_saneamento(processo, termos):
        return []
    processo_id = str(processo.get("processoContratacaoId") or "").strip()
    if not processo_id:
        raise ValueError("processo sem processoContratacaoId")
    orgao = re.sub(r"\s+", " ", str(processo.get("unidadeJurisdicionadaNome") or "")).strip()
    numero = str(processo.get("numeroContratacao") or "").strip()
    ano = str(processo.get("anoContratacao") or "").strip()
    uj = str(processo.get("unidadeJurisdicionadaId") or "").strip()
    rotulo = f"{uj}/{numero}/{ano}"
    data = _data(processo.get("dataResultado")) or _data(processo.get("dataOrcamento"))
    municipio = _municipio(orgao)

    itens_contrato = escolher_contrato(processo, contratos)
    casados = casar_itens(orcamento, itens_contrato) if itens_contrato else {}

    novos: list[PrecoItemNovo] = []
    for i, item in enumerate(orcamento):
        descricao = str(item.get("itemDescricao") or "")
        unidade = str(item.get("UnidadeMedida") or "")
        estimado = _decimal(item.get("ValorUnitario"))
        if not descricao.strip() or estimado is None or estimado <= 0:
            continue
        classificacao = classificar(descricao, unidade, servicos)
        if classificacao is None:
            continue
        contratado = None
        if (par := casados.get(i)) is not None:
            contratado = _decimal(par.get("ValorUnitario"))
            if contratado is not None and contratado <= 0:
                contratado = None
        suspeito = contratado is not None and not (RAZAO_MIN <= contratado / estimado <= RAZAO_MAX)
        novos.append(
            PrecoItemNovo(
                origem=ORIGEM,
                chave_origem=_chave(processo_id, item, i),
                uf=UF,
                municipio=municipio,
                orgao_nome=orgao,
                orgao_cnpj=None,
                processo=rotulo,
                data=data,
                data_base=_data(item.get("DataReferencia")),
                descricao=descricao,
                unidade=unidade,
                quantidade=_decimal(item.get("Quantidade")),
                preco_sem_bdi=None,
                bdi=bdi_fracao(item.get("BDI")),
                preco_com_bdi=estimado,
                bdi_desconhecido=False,
                preco_contratado_com_bdi=contratado,
                fonte_referencia=_texto(item.get("FonteReferencia")),
                codigo_referencia=_texto(item.get("CodigoReferencia")),
                servico=classificacao.servico,
                material=classificacao.material,
                dn=classificacao.dn,
                escopo=classificacao.escopo,
                faixa=classificacao.faixa,
                unidade_canonica=classificacao.unidade_canonica,
                fator_unidade=classificacao.fator,
                suspeito=suspeito,
            )
        )
    return novos


def gravar_precos(sessao: Session, itens: Iterable[PrecoItemNovo]) -> int:
    """Upsert por `(origem, chave_origem)`; devolve quantas linhas foram gravadas. Sem commit."""
    linhas = [asdict(item) for item in itens]
    if not linhas:
        return 0
    # Chave repetida no mesmo lote faria o ON CONFLICT falhar; vale a última.
    unicas = list({(linha["origem"], linha["chave_origem"]): linha for linha in linhas}.values())
    insercao = insert(PrecoItem).values(unicas)
    atualizar: dict[str, Any] = {
        c: insercao.excluded[c] for c in unicas[0] if c not in ("origem", "chave_origem")
    }
    atualizar["gravado_em"] = func.now()
    sessao.execute(
        insercao.on_conflict_do_update(constraint="uq_preco_item_origem", set_=atualizar)
    )
    return len(unicas)
