"""Gravação das planilhas do PNCP em `preco_item`, estado por edital e republicação."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from radar.precos import (
    EditalPlanilha,
    LinhaPlanilha,
    PlanilhaEdital,
    PrecoItem,
    gasto_planilhas_do_mes,
    gravar_planilha,
    itens_da_planilha,
    planilhas_a_ignorar,
    referencias,
    registrar_planilha,
)

EDITAL = EditalPlanilha(
    id_pncp="44239770000167-1-000172/2026",
    uf="SP",
    municipio="Araraquara",
    orgao_nome="DAAE Araraquara",
    orgao_cnpj="44239770000167",
    objeto="Execução de coletor de esgotos",
    data=date(2026, 11, 10),
)
AGORA = datetime(2026, 11, 27, 13, 0, tzinfo=UTC)


def _linha(item: str, dn: int, com: str, codigo: str = "90701") -> LinhaPlanilha:
    return LinhaPlanilha(
        item=item,
        codigo=codigo,
        fonte="sinapi",
        descricao=f"Tubo, PVC Ocre, DN {dn} - fornec./assent.",
        unidade="m",
        quantidade=Decimal("10.00"),
        preco_sem_bdi=None,
        preco_com_bdi=Decimal(com),
        total=Decimal(com) * 10,
        bdi=Decimal("0.25"),
        data_base=date(2026, 8, 1),
        secao="Coletor",
        pagina=1,
    )


LINHAS = [_linha("2.1", 150, "111.81"), _linha("2.2", 200, "184.95"), _linha("2.3", 250, "288.59")]


def _gravar(sessao: Session, edital: EditalPlanilha, sha: str, linhas: list[LinhaPlanilha]) -> int:
    itens = itens_da_planilha(edital, sha, linhas)
    gravacao = gravar_planilha(
        sessao,
        edital,
        sha,
        itens,
        metodo="deterministico",
        documento="Anexo III.pdf",
        linhas_lidas=len(linhas),
    )
    return gravacao.gravadas


def _processos(sessao: Session) -> list[str]:
    return sorted(set(sessao.scalars(select(PrecoItem.processo)).all()))


def test_grava_e_regrava_trocando_as_linhas(sessao: Session) -> None:
    assert _gravar(sessao, EDITAL, "a" * 64, LINHAS) == 3
    assert _gravar(sessao, EDITAL, "a" * 64, LINHAS[:2]) == 2

    assert sessao.query(PrecoItem).count() == 2
    estado = sessao.get(PlanilhaEdital, EDITAL.id_pncp)
    assert estado is not None
    assert (estado.status, estado.metodo, estado.linhas_lidas, estado.linhas_gravadas) == (
        "extraida",
        "deterministico",
        2,
        2,
    )


def test_mesma_planilha_em_edital_mais_novo_fica_uma_vez(sessao: Session) -> None:
    novo = replace(EDITAL, id_pncp="44239770000167-1-000199/2026", data=date(2026, 11, 25))
    _gravar(sessao, EDITAL, "a" * 64, LINHAS)
    _gravar(sessao, novo, "a" * 64, LINHAS)

    assert _processos(sessao) == [novo.id_pncp]
    antigo = sessao.get(PlanilhaEdital, EDITAL.id_pncp)
    assert antigo is not None
    assert (antigo.status, antigo.republicada_em) == ("republicada", novo.id_pncp)
    assert EDITAL.id_pncp in planilhas_a_ignorar(sessao, AGORA)


def test_mesmo_orgao_com_80_por_cento_das_linhas_iguais_e_republicacao(sessao: Session) -> None:
    novo = replace(EDITAL, id_pncp="44239770000167-1-000200/2026")
    outro_orgao = replace(EDITAL, id_pncp="11111111000111-1-000001/2026", orgao_cnpj="111")
    _gravar(sessao, EDITAL, "a" * 64, LINHAS)
    _gravar(sessao, outro_orgao, "c" * 64, LINHAS)  # outro órgão: não é republicação
    # Planilha refeita (outro arquivo) com as mesmas linhas e uma a mais: 3 de 4 = 75%.
    quatro = [*LINHAS, _linha("2.4", 300, "400.00")]
    _gravar(sessao, novo, "b" * 64, quatro)
    assert _processos(sessao) == sorted([EDITAL.id_pncp, novo.id_pncp, outro_orgao.id_pncp])

    # Mais uma: as 4 linhas iguais às do `novo` (100%) → o `novo` vira republicação.
    terceiro = replace(EDITAL, id_pncp="44239770000167-1-000201/2026")
    _gravar(sessao, terceiro, "d" * 64, quatro)
    assert novo.id_pncp not in _processos(sessao)
    estado = sessao.get(PlanilhaEdital, novo.id_pncp)
    assert estado is not None and estado.status == "republicada"


def test_ignorar_estados_finais_falha_recente_e_teto_do_mes(sessao: Session) -> None:
    registrar_planilha(sessao, "sem", "sem_planilha")
    registrar_planilha(sessao, "falha-nova", "falhou", motivo="ErroArquivos")
    registrar_planilha(sessao, "falha-velha", "falhou")
    registrar_planilha(sessao, "teto", "pulado_teto")
    registrar_planilha(sessao, "teto-velho", "pulado_teto")
    sessao.flush()
    sessao.execute(
        update(PlanilhaEdital)
        .where(PlanilhaEdital.id_pncp.in_(["falha-velha", "teto-velho"]))
        .values(processada_em=AGORA - timedelta(days=40))
    )
    sessao.execute(
        update(PlanilhaEdital)
        .where(PlanilhaEdital.id_pncp.in_(["sem", "falha-nova", "teto"]))
        .values(processada_em=AGORA - timedelta(hours=1))
    )

    assert planilhas_a_ignorar(sessao, AGORA) == {"sem", "falha-nova", "teto"}


def test_gasto_do_mes_soma_so_o_mes(sessao: Session) -> None:
    registrar_planilha(sessao, "ia-1", "extraida", metodo="ia", custo_usd=Decimal("0.0012"))
    registrar_planilha(sessao, "ia-2", "extraida", metodo="ia", custo_usd=Decimal("0.0030"))
    sessao.flush()
    sessao.execute(
        update(PlanilhaEdital)
        .where(PlanilhaEdital.id_pncp == "ia-2")
        .values(processada_em=AGORA - timedelta(days=40))
    )
    sessao.execute(
        update(PlanilhaEdital)
        .where(PlanilhaEdital.id_pncp == "ia-1")
        .values(processada_em=AGORA - timedelta(hours=1))
    )

    assert gasto_planilhas_do_mes(sessao, AGORA) == Decimal("0.0012")


def test_alimenta_as_referencias_com_a_uf_de_origem(sessao: Session) -> None:
    for n in range(3):
        edital = replace(EDITAL, id_pncp=f"4423977000016{n}-1-000172/2026", orgao_cnpj=str(n))
        _gravar(sessao, edital, f"{n}" * 64, LINHAS)

    refs = referencias(sessao, ["redes"], "MG")
    dn150 = next(r for r in refs if r.dn == 150)
    assert (dn150.servico, dn150.escopo, dn150.processos_estimado) == (
        "rede_esgoto",
        "completo",
        3,
    )
    assert dn150.ufs == ("SP",)
    assert dn150.abrangencia == "outras_ufs"
    assert dn150.origens == ("pncp_planilha",)
    assert dn150.mediana_estimado == Decimal("111.81")
    assert dn150.processos_contratado == 0
