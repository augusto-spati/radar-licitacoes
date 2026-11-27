from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from radar.editais import (
    Criterios,
    Regua,
    ResultadoBusca,
    ResultadoItem,
    faixa_de_valor,
    historico_do_edital,
)
from radar.editais.modelos import Edital

FUSO = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 11, 27, 12, 0, tzinfo=FUSO)
CRITERIOS = Criterios(
    linhas={"perdas": ["hidrometro"], "redes": ["rede de agua"]}, ufs=["SP", "PR"], modalidades=[6]
)
CNPJ = "12345678000199"
OUTRO = "98765432000111"
SANEPAR = "76484013000145"


def _edital(
    sessao: Session,
    id_na_fonte: str,
    objeto: str = "Troca de hidrometro",
    cnpj: str | None = CNPJ,
    uf: str = "SP",
    valor: str | None = "500000",
    dias: int = 30,
    fonte: str = "pncp",
) -> None:
    sessao.add(
        Edital(
            fonte=fonte,
            id_na_fonte=id_na_fonte,
            objeto=objeto,
            orgao_cnpj=cnpj,
            orgao_nome="Órgão Teste",
            uf=uf,
            modalidade_id=6,
            modalidade_nome="Pregão",
            situacao_original="x",
            situacao_normalizada="encerrada",
            valor_estimado=None if valor is None else Decimal(valor),
            data_encerramento_proposta=AGORA.replace(tzinfo=None) - timedelta(days=dias),
            data_atualizacao=AGORA.replace(tzinfo=None),
            bruto={},
        )
    )


def _resultado(
    sessao: Session,
    id_na_fonte: str,
    situacao: str = "homologada",
    desconto: str | None = "0.20",
    ni: str | None = None,
    nome: str | None = None,
    homologado: str | None = "80",
    fonte: str = "pncp",
) -> None:
    sessao.add(
        ResultadoBusca(
            fonte=fonte,
            id_na_fonte=id_na_fonte,
            buscado_em=AGORA,
            itens=1,
            situacao=situacao,
            desconto=None if desconto is None else Decimal(desconto),
            valor_estimado_considerado=Decimal("100") if homologado else None,
            valor_homologado_considerado=None if homologado is None else Decimal(homologado),
            vencedor_ni=ni,
            vencedor_nome=nome,
            vencedor_porte="ME" if nome else None,
        )
    )


def _passada(sessao: Session, id_na_fonte: str, dias: int = 30, **resultado: str | None) -> None:
    _edital(sessao, id_na_fonte, dias=dias)
    _resultado(sessao, id_na_fonte, **resultado)  # type: ignore[arg-type]


def test_contagens_ultimas_e_vencedores(sessao: Session) -> None:
    _edital(sessao, "alvo", dias=-5)  # o edital da ficha, ainda aberto
    _passada(sessao, "h1", dias=100, ni="111", nome="Acme", homologado="50")
    _passada(sessao, "h2", dias=60, ni="111", nome="Acme Ltda", homologado="50")
    _passada(sessao, "h3", dias=40, ni="222", nome="Beta", homologado="900")
    _passada(sessao, "f1", dias=20, situacao="fracassada", desconto=None, homologado=None)
    # Fora do recorte: outro órgão, sem linha, encerrado há mais de 24 meses, sem resultado.
    _edital(sessao, "outro", cnpj=OUTRO)
    _resultado(sessao, "outro", ni="333", nome="Gama")
    _edital(sessao, "semlinha", objeto="Compra de cadeiras")
    _resultado(sessao, "semlinha", ni="333", nome="Gama")
    _passada(sessao, "antigo", dias=800, ni="333", nome="Gama")
    _edital(sessao, "sembusca")
    sessao.flush()
    # h1 tem resultado recente: passa à frente pela data do resultado.
    sessao.add(
        ResultadoItem(
            fonte="pncp",
            id_na_fonte="h1",
            numero_item=1,
            descricao="d",
            orcamento_sigiloso=False,
            situacao="homologado",
            data_resultado=AGORA - timedelta(days=1),
        )
    )
    sessao.flush()

    historico = historico_do_edital(sessao, CRITERIOS, "pncp", "alvo", AGORA)

    assert historico is not None
    assert historico.contagens == {"fracassada": 1, "homologada": 3}
    assert historico.total == 4
    assert [u.id_publico for u in historico.ultimas] == ["pncp~h1", "pncp~f1", "pncp~h3", "pncp~h2"]
    primeira = historico.ultimas[0]
    assert (primeira.situacao, primeira.desconto, primeira.parcial) == (
        "homologada",
        Decimal("0.20"),
        False,
    )
    assert (primeira.valor_estimado, primeira.valor_homologado) == (Decimal(100), Decimal(50))
    # O nome vem do resultado mais recente (h1, pela data do resultado), como em `ultimas`.
    assert [(v.nome, v.vitorias) for v in historico.vencedores] == [("Acme", 2), ("Beta", 1)]
    assert historico.vencedores[0].desconto_mediano == Decimal("0.2000")
    assert historico.limite_inexequivel == Decimal("0.75")


def test_desempate_de_vencedores_pela_soma_homologada(sessao: Session) -> None:
    _edital(sessao, "alvo", dias=-5)
    _passada(sessao, "a", ni="111", nome="Pequena", homologado="10")
    _passada(sessao, "b", ni="222", nome="Grande", homologado="1000")
    _passada(sessao, "c", nome="Pessoa física", homologado="5000")
    _passada(sessao, "d", nome="Pessoa física", homologado="5000")
    sessao.flush()

    historico = historico_do_edital(sessao, CRITERIOS, "pncp", "alvo", AGORA)

    assert historico is not None
    # Pessoa física não se soma entre contratações: cada uma é uma vitória à parte.
    assert [(v.nome, v.vitorias) for v in historico.vencedores] == [
        ("Pessoa física", 1),
        ("Pessoa física", 1),
        ("Grande", 1),
        ("Pequena", 1),
    ]


def test_regua_com_seis_casos(sessao: Session) -> None:
    _edital(sessao, "alvo", dias=-5, valor="800000")
    for i, desconto in enumerate(["0.10", "0.12", "0.15", "0.20", "0.25", "0.30"]):
        _passada(sessao, f"r{i}", desconto=desconto)
    # Fora da régua: outra faixa, outra UF, parcial, homologada sem desconto, outra linha.
    _edital(sessao, "caro", valor="2000000")
    _resultado(sessao, "caro", desconto="0.90")
    _edital(sessao, "pr", uf="PR")
    _resultado(sessao, "pr", desconto="0.90")
    _passada(sessao, "parcial", situacao="parcial", desconto="0.90")
    _passada(sessao, "sigiloso", desconto=None)
    _edital(sessao, "rede", objeto="Obra de rede de agua")
    _resultado(sessao, "rede", desconto="0.90")
    sessao.flush()

    historico = historico_do_edital(sessao, CRITERIOS, "pncp", "alvo", AGORA)

    assert historico is not None
    assert historico.reguas == [
        Regua(
            linha="perdas",
            uf="SP",
            faixa="ate_1m",
            casos=6,
            p25=Decimal("0.1275"),
            mediana=Decimal("0.1750"),
            p75=Decimal("0.2375"),
            poucos_casos=False,
        )
    ]
    assert historico.contagens == {"homologada": 10, "parcial": 1}


def test_regua_com_poucos_casos_traz_a_da_linha(sessao: Session) -> None:
    _edital(sessao, "alvo", dias=-5, valor="800000")
    for i, desconto in enumerate(["0.10", "0.20", "0.30"]):
        _passada(sessao, f"r{i}", desconto=desconto)
    _edital(sessao, "pr", uf="PR", valor="9000000")
    _resultado(sessao, "pr", desconto="0.40")
    sessao.flush()

    historico = historico_do_edital(sessao, CRITERIOS, "pncp", "alvo", AGORA)

    assert historico is not None
    assert historico.reguas == [
        Regua(
            "perdas",
            "SP",
            "ate_1m",
            3,
            Decimal("0.1500"),
            Decimal("0.2000"),
            Decimal("0.2500"),
            True,
        ),
        Regua(
            "perdas", None, None, 4, Decimal("0.1750"), Decimal("0.2500"), Decimal("0.3250"), True
        ),
    ]


def test_edital_sem_linha_usa_regua_de_todas_as_linhas(sessao: Session) -> None:
    _edital(sessao, "alvo", objeto="Serviço de limpeza", dias=-5, valor=None)
    _passada(sessao, "h", desconto="0.10")
    _edital(sessao, "rede", objeto="Obra de rede de agua")
    _resultado(sessao, "rede", desconto="0.30")
    sessao.flush()

    historico = historico_do_edital(sessao, CRITERIOS, "pncp", "alvo", AGORA)

    assert historico is not None
    # Sem valor, a régua não restringe faixa; com poucos casos, vem a de todas as UFs.
    assert [(r.linha, r.uf, r.faixa, r.casos) for r in historico.reguas] == [
        (None, "SP", None, 2),
        (None, None, None, 2),
    ]


def test_edital_da_sanepar_usa_o_cnpj_e_limite_de_estatal(sessao: Session) -> None:
    _edital(sessao, "OBR-1/2026", cnpj=SANEPAR, uf="PR", dias=-5, fonte="sanepar")
    _edital(sessao, "pregao", cnpj=SANEPAR, uf="PR")
    _resultado(sessao, "pregao", ni="111", nome="Acme")
    sessao.flush()

    historico = historico_do_edital(sessao, CRITERIOS, "sanepar", "OBR-1/2026", AGORA)

    assert historico is not None
    assert historico.orgao_cnpj == SANEPAR
    assert historico.limite_inexequivel == Decimal("0.70")
    assert historico.total == 1
    assert [u.id_publico for u in historico.ultimas] == ["pncp~pregao"]


def test_orgao_sem_historico_e_edital_inexistente(sessao: Session) -> None:
    _edital(sessao, "alvo", dias=-5)
    _edital(sessao, "semcnpj", cnpj=None, dias=-5)
    sessao.flush()

    historico = historico_do_edital(sessao, CRITERIOS, "pncp", "alvo", AGORA)

    assert historico is not None
    assert (historico.total, historico.contagens) == (0, {})
    assert (historico.ultimas, historico.vencedores) == ([], [])
    assert [(r.casos, r.mediana, r.poucos_casos) for r in historico.reguas] == [
        (0, None, True),
        (0, None, True),
    ]
    sem_cnpj = historico_do_edital(sessao, CRITERIOS, "pncp", "semcnpj", AGORA)
    assert sem_cnpj is not None and sem_cnpj.total == 0
    assert historico_do_edital(sessao, CRITERIOS, "pncp", "nao-existe", AGORA) is None


def test_faixas_de_valor() -> None:
    assert faixa_de_valor(None) is None
    assert faixa_de_valor(Decimal(1_000_000)) == "ate_1m"
    assert faixa_de_valor(Decimal("1000000.01")) == "1m_a_5m"
    assert faixa_de_valor(Decimal(5_000_000)) == "1m_a_5m"
    assert faixa_de_valor(Decimal("5000000.01")) == "acima_5m"


def test_parcial_conta_vitoria_mas_nao_entra_na_mediana(sessao: Session) -> None:
    _edital(sessao, "alvo", dias=-5)
    _passada(sessao, "h", ni="111", nome="Acme", desconto="0.20")
    _passada(sessao, "p", ni="111", nome="Acme", situacao="parcial", desconto="0.60")
    sessao.flush()

    historico = historico_do_edital(sessao, CRITERIOS, "pncp", "alvo", AGORA)

    assert historico is not None
    vencedor = historico.vencedores[0]
    assert (vencedor.vitorias, vencedor.desconto_mediano) == (2, Decimal("0.20"))


def test_o_proprio_edital_fica_fora_da_regua(sessao: Session) -> None:
    for i, desconto in enumerate(["0.10", "0.12", "0.15", "0.20", "0.25", "0.30"]):
        _passada(sessao, f"r{i}", desconto=desconto)
    _passada(sessao, "alvo", dias=10, desconto="0.90", ni="111", nome="Acme")
    sessao.flush()

    historico = historico_do_edital(sessao, CRITERIOS, "pncp", "alvo", AGORA)

    assert historico is not None
    assert [(r.casos, r.mediana) for r in historico.reguas] == [(6, Decimal("0.1750"))]
    # Fora da régua, mas é histórico do órgão: entra nas contagens, últimas e vencedores.
    assert historico.total == 7
    assert historico.ultimas[0].id_publico == "pncp~alvo"
    assert historico.vencedores[0].nome == "Acme"


def test_regua_usa_todos_os_orgaos_e_o_historico_so_o_do_edital(sessao: Session) -> None:
    _edital(sessao, "alvo", dias=-5)
    _passada(sessao, "meu", desconto="0.10", ni="111", nome="Acme")
    for i, desconto in enumerate(["0.20", "0.30", "0.40", "0.50"]):
        _edital(sessao, f"outro{i}", cnpj=OUTRO)
        _resultado(sessao, f"outro{i}", desconto=desconto, ni="222", nome="Beta")
    # Outro órgão, mas fora da linha, da UF ou da faixa: não entra.
    _edital(sessao, "outro-rede", cnpj=OUTRO, objeto="Obra de rede de agua")
    _resultado(sessao, "outro-rede", desconto="0.90")
    _edital(sessao, "outro-pr", cnpj=OUTRO, uf="PR")
    _resultado(sessao, "outro-pr", desconto="0.90")
    _edital(sessao, "outro-caro", cnpj=OUTRO, valor="2000000")
    _resultado(sessao, "outro-caro", desconto="0.90")
    sessao.flush()

    historico = historico_do_edital(sessao, CRITERIOS, "pncp", "alvo", AGORA)

    assert historico is not None
    assert [(r.linha, r.uf, r.faixa, r.casos, r.mediana) for r in historico.reguas] == [
        ("perdas", "SP", "ate_1m", 5, Decimal("0.3000"))
    ]
    assert historico.total == 1 and historico.contagens == {"homologada": 1}
    assert [u.id_publico for u in historico.ultimas] == ["pncp~meu"]
    assert [v.nome for v in historico.vencedores] == ["Acme"]


def test_regua_de_edital_sem_cnpj_usa_todos_os_orgaos(sessao: Session) -> None:
    _edital(sessao, "alvo", cnpj=None, dias=-5)
    _passada(sessao, "h", desconto="0.10")
    sessao.flush()

    historico = historico_do_edital(sessao, CRITERIOS, "pncp", "alvo", AGORA)

    assert historico is not None and historico.total == 0
    assert historico.reguas[0].casos == 1


def test_valor_zero_nao_tem_faixa(sessao: Session) -> None:
    assert faixa_de_valor(Decimal(0)) is None
    assert faixa_de_valor(Decimal(-1)) is None
    _edital(sessao, "alvo", dias=-5, valor="0")
    _passada(sessao, "h", desconto="0.10")
    _edital(sessao, "zero", valor="0")
    _resultado(sessao, "zero", desconto="0.30", homologado=None)
    sessao.flush()

    historico = historico_do_edital(sessao, CRITERIOS, "pncp", "alvo", AGORA)

    assert historico is not None
    # Sem faixa, a régua não filtra por valor: entram o de 500 mil e o de valor zero.
    assert [(r.linha, r.uf, r.faixa, r.casos) for r in historico.reguas[:1]] == [
        ("perdas", "SP", None, 2)
    ]
    zero = next(u for u in historico.ultimas if u.id_publico == "pncp~zero")
    assert zero.valor_estimado is None


def test_agora_sem_fuso_e_erro(sessao: Session) -> None:
    with pytest.raises(ValueError, match="fuso"):
        historico_do_edital(sessao, CRITERIOS, "pncp", "x", AGORA.replace(tzinfo=None))


COMPESA = "09769035000164"


def _caso_tcepe(
    sessao: Session,
    id_na_fonte: str,
    desconto: str | None = "0.10",
    linhas: tuple[str, ...] = ("redes",),
    dias: int = 60,
    numero: str | None = None,
    cnpj: str = COMPESA,
    vencedor: tuple[str | None, str] | None = ("10514594000102", "EMCOSA LTDA"),
    estimado: str = "2000000",
) -> None:
    from radar.editais import ResultadoExterno

    sessao.add(
        ResultadoExterno(
            fonte="tce_pe",
            id_na_fonte=id_na_fonte,
            orgao_cnpj=cnpj,
            orgao_nome="COMPESA",
            uf="PE",
            objeto="Adutora do Agreste",
            modalidade_nome="Procedimento de Licitação Próprio - Eletrônico",
            numero=numero,
            linhas=list(linhas),
            data=AGORA.replace(tzinfo=None) - timedelta(days=dias),
            data_resultado=AGORA - timedelta(days=dias - 10),
            situacao="homologada",
            valor_estimado=Decimal(estimado),
            valor_homologado=Decimal(estimado) * (1 - Decimal(desconto or 0)),
            desconto=None if desconto is None else Decimal(desconto),
            vencedor_ni=None if vencedor is None else vencedor[0],
            vencedor_nome=None if vencedor is None else vencedor[1],
        )
    )


CRITERIOS_PE = Criterios(
    linhas={"perdas": ["hidrometro"], "redes": ["rede de agua", "adutora"]},
    ufs=["PE"],
    modalidades=[6],
)


def _edital_compesa(sessao: Session, numero: str = "LC 213/2026 CEL-1") -> None:
    sessao.add(
        Edital(
            fonte="compesa",
            id_na_fonte="2026LC213CEL-1",
            objeto="Implantação de adutora",
            orgao_cnpj=COMPESA,
            orgao_nome="COMPESA",
            uf="PE",
            modalidade_nome="Licitação",
            situacao_original="Em andamento",
            situacao_normalizada="aberta",
            valor_estimado=Decimal("3000000"),
            data_encerramento_proposta=AGORA.replace(tzinfo=None) + timedelta(days=10),
            data_atualizacao=AGORA.replace(tzinfo=None),
            bruto={"numero": numero},
        )
    )


def test_edital_da_compesa_ve_os_processos_do_tcepe(sessao: Session) -> None:
    _edital_compesa(sessao)
    _caso_tcepe(sessao, "1", desconto="0.10")
    _caso_tcepe(sessao, "2", desconto="0.20", dias=90)
    _caso_tcepe(sessao, "3", desconto="0.30", dias=120, vencedor=("99999999000199", "OUTRA LTDA"))
    # Fora: mais de 24 meses, linha que não existe mais no filtro, outro órgão.
    _caso_tcepe(sessao, "velho", dias=800)
    _caso_tcepe(sessao, "semlinha", linhas=("comercial",))
    _caso_tcepe(sessao, "outro", cnpj=OUTRO)
    sessao.flush()

    historico = historico_do_edital(sessao, CRITERIOS_PE, "compesa", "2026LC213CEL-1", AGORA)

    assert historico is not None
    assert historico.contagens == {"homologada": 3}
    assert [u.id_publico for u in historico.ultimas] == ["tce_pe~1", "tce_pe~2", "tce_pe~3"]
    assert {u.origem for u in historico.ultimas} == {"TCE-PE"}
    primeira = historico.ultimas[0]
    assert (primeira.valor_estimado, primeira.desconto) == (Decimal(2000000), Decimal("0.10"))
    assert primeira.vencedor_nome == "EMCOSA LTDA"
    assert [(v.nome, v.vitorias) for v in historico.vencedores] == [
        ("EMCOSA LTDA", 2),
        ("OUTRA LTDA", 1),
    ]
    assert historico.vencedores[0].desconto_mediano == Decimal("0.1500")
    assert historico.limite_inexequivel == Decimal("0.70")
    # Régua redes × PE × 1–5 mi: os 3 da Compesa e o do outro órgão (régua usa todos).
    [regua, *_] = historico.reguas
    assert (regua.linha, regua.uf, regua.faixa, regua.casos) == ("redes", "PE", "1m_a_5m", 4)
    assert regua.poucos_casos


def test_regua_junta_pncp_e_tcepe_sem_o_proprio_processo(sessao: Session) -> None:
    _edital_compesa(sessao)
    # O processo do TCE-PE com o número do edital (LC 213/2026) é o próprio edital.
    _caso_tcepe(sessao, "proprio", desconto="0.50", numero="213/2026")
    _caso_tcepe(sessao, "a", desconto="0.10", numero="68/2026")
    _edital(
        sessao, "pncp-pe", objeto="Implantação de adutora", cnpj=OUTRO, uf="PE", valor="2000000"
    )
    _resultado(sessao, "pncp-pe", desconto="0.30")
    sessao.flush()

    historico = historico_do_edital(sessao, CRITERIOS_PE, "compesa", "2026LC213CEL-1", AGORA)

    assert historico is not None
    assert historico.contagens == {"homologada": 2}  # o próprio entra nas contagens
    regua = historico.reguas[0]
    assert regua.casos == 2
    assert regua.mediana == Decimal("0.2000")


def test_edital_do_pncp_em_pe_tem_a_compesa_na_regua(sessao: Session) -> None:
    _edital(sessao, "alvo", objeto="Rede de agua", cnpj=OUTRO, uf="PE", valor="2000000", dias=-5)
    _caso_tcepe(sessao, "a", desconto="0.12")
    sessao.flush()

    historico = historico_do_edital(sessao, CRITERIOS_PE, "pncp", "alvo", AGORA)

    assert historico is not None
    assert historico.total == 0  # outro órgão
    assert historico.reguas[0].casos == 1
    assert historico.reguas[0].mediana == Decimal("0.1200")
