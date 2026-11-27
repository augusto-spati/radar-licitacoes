"""Referências de preço: medianas, suspeitos fora, UF→nacional, corte da rede e faixa."""

from datetime import date
from decimal import Decimal
from itertools import count

from sqlalchemy.orm import Session

from radar.precos import PrecoItem, Referencia, referencias

_seq = count()


def _item(
    sessao: Session,
    servico: str,
    preco: str | None,
    *,
    uf: str = "PE",
    origem: str = "tce_pe",
    contratado: str | None = None,
    fator: str = "1",
    unidade: str = "un",
    unidade_canonica: str = "un",
    material: str | None = None,
    dn: int | None = None,
    escopo: str | None = None,
    faixa: str | None = None,
    suspeito: bool = False,
    bdi_desconhecido: bool = False,
    data_base: date | None = None,
) -> None:
    n = next(_seq)
    sessao.add(
        PrecoItem(
            origem=origem,
            chave_origem=f"teste-{n}",
            uf=uf,
            orgao_nome="Órgão",
            processo=f"proc-{n}",
            descricao="item de teste",
            unidade=unidade,
            preco_com_bdi=None if preco is None else Decimal(preco),
            preco_contratado_com_bdi=None if contratado is None else Decimal(contratado),
            bdi_desconhecido=bdi_desconhecido,
            servico=servico,
            material="pvc",
            dn=dn,
            escopo=escopo,
            faixa=faixa,
            unidade_canonica=unidade_canonica,
            fator_unidade=Decimal(fator),
            suspeito=suspeito,
            data_base=data_base,
        )
    )
    sessao.flush()


def _so(refs: list[Referencia], servico: str) -> Referencia:
    [ref] = [r for r in refs if r.servico == servico]
    return ref


def test_mediana_com_numeros_conhecidos_e_suspeito_fora(sessao: Session) -> None:
    for preco, contratado in [
        ("100", "90"),
        ("200", "150"),
        ("300", None),
        ("400", "250"),
        ("500", None),
    ]:
        _item(sessao, "ligacao_agua", preco, contratado=contratado, data_base=date(2025, 3, 1))
    _item(sessao, "ligacao_agua", "10000", contratado="5", suspeito=True)
    # Planilha entra no estimado, nunca no contratado.
    _item(sessao, "ligacao_agua", "600", origem="planilha_edital", contratado="999")

    ref = _so(referencias(sessao, ["redes"], "PE"), "ligacao_agua")

    assert (ref.abrangencia, ref.uf) == ("uf", "PE")
    assert (ref.casos_estimado, ref.mediana_estimado) == (6, Decimal("350.00"))
    assert (ref.casos_contratado, ref.mediana_contratado) == (3, Decimal("150.00"))
    assert ref.unidade == "un"
    assert (ref.data_base_inicio, ref.data_base_fim) == (date(2025, 3, 1), date(2025, 3, 1))


def test_preco_vai_para_a_unidade_canonica(sessao: Session) -> None:
    # Pesquisa de vazamento é por km; item em m com fator 0,001: R$ 2/m = R$ 2.000/km.
    _item(
        sessao,
        "pesquisa_vazamento",
        "2",
        contratado="1.5",
        fator="0.001",
        unidade="m",
        unidade_canonica="km",
    )

    ref = _so(referencias(sessao, ["perdas"], "PE"), "pesquisa_vazamento")

    assert ref.unidade == "km"
    assert ref.mediana_estimado == Decimal("2000.00")
    assert ref.mediana_contratado == Decimal("1500.00")
    assert str(ref.mediana_estimado) == "2000.00"


def test_menos_de_5_casos_na_uf_cai_para_nacional(sessao: Session) -> None:
    for preco in ["10", "20"]:
        _item(sessao, "hidrometro", preco, uf="SP", contratado=preco)
    for preco in ["30", "40", "50"]:
        _item(sessao, "hidrometro", preco, uf="PE")

    ref = _so(referencias(sessao, ["perdas"], "SP"), "hidrometro")

    assert (ref.abrangencia, ref.uf) == ("nacional", None)
    assert (ref.casos_estimado, ref.mediana_estimado) == (5, Decimal("30.00"))
    assert ref.casos_contratado == 2
    # Sem UF no edital, nacional.
    assert _so(referencias(sessao, ["perdas"], None), "hidrometro").abrangencia == "nacional"


def test_bdi_desconhecido_sai_quando_ha_casos_sem_a_marca(sessao: Session) -> None:
    for preco in ["10", "20", "30", "40", "50"]:
        _item(sessao, "hidrometro", preco)
    _item(sessao, "hidrometro", "1000", bdi_desconhecido=True)

    ref = _so(referencias(sessao, ["perdas"], "PE"), "hidrometro")
    assert (ref.casos_estimado, ref.mediana_estimado) == (5, Decimal("30.00"))


def test_bdi_desconhecido_fica_quando_faltam_casos(sessao: Session) -> None:
    _item(sessao, "hidrometro", "10")
    _item(sessao, "hidrometro", "30", bdi_desconhecido=True)

    ref = _so(referencias(sessao, ["perdas"], "PE"), "hidrometro")
    assert (ref.casos_estimado, ref.mediana_estimado) == (2, Decimal("20.00"))


def _rede(sessao: Session, n: int, dn: int, escopo: str = "completo") -> None:
    for i in range(n):
        _item(
            sessao,
            "rede_agua",
            str(100 + i),
            material="pvc",
            dn=dn,
            escopo=escopo,
            unidade="m",
            unidade_canonica="m",
        )


def test_rede_so_com_3_casos_e_ordenada_por_casos(sessao: Session) -> None:
    _rede(sessao, 3, 100)
    _rede(sessao, 4, 150)
    _rede(sessao, 2, 200)  # abaixo do corte
    _rede(sessao, 3, 100, escopo="material")  # outro escopo, outra chave
    _item(sessao, "ligacao_agua", "50")  # fora de rede: 1 caso basta

    refs = referencias(sessao, ["redes"], "PE")

    redes = [(r.dn, r.escopo, r.casos_estimado) for r in refs if r.servico == "rede_agua"]
    assert redes == [(150, "completo", 4), (100, "completo", 3), (100, "material", 3)]
    assert _so(refs, "ligacao_agua").casos_estimado == 1
    # ligacao_agua vem antes de rede_agua (ordem de config/servicos.toml).
    assert refs[0].servico == "ligacao_agua"


def test_faixa_separa_a_escavacao(sessao: Session) -> None:
    for preco in ["20", "30"]:
        _item(sessao, "escavacao_vala", preco, faixa="ate_1_5m", unidade="m3")
    _item(sessao, "escavacao_vala", "80", faixa="3_a_6m", unidade="m3")

    refs = [r for r in referencias(sessao, ["redes"], "PE") if r.servico == "escavacao_vala"]

    assert [(r.faixa, r.casos_estimado, r.mediana_estimado) for r in refs] == [
        ("ate_1_5m", 2, Decimal("25.00")),
        ("3_a_6m", 1, Decimal("80.00")),
    ]


def test_sem_linha_com_servico_ou_sem_dados_devolve_vazio(sessao: Session) -> None:
    _item(sessao, "hidrometro", "10")
    assert referencias(sessao, [], "PE") == []
    assert referencias(sessao, ["outra"], "PE") == []
    assert referencias(sessao, ["redes"], "PE") == []  # hidrômetro é de `perdas`
    # Só suspeitos: nada.
    _item(sessao, "ligacao_agua", "10", suspeito=True)
    assert referencias(sessao, ["redes"], "PE") == []
