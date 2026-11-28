from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from radar.editais import EditalResumo
from radar.editais.filtro import Recorte, _condicoes_recorte, limite_encerra_em
from radar.editais.painel import Secao, dias_ate_encerrar, eh_novo, separar_painel

AGORA = datetime(2026, 11, 26, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))


def _edital(
    n: int,
    encerra: datetime | None,
    veredito: str | None = None,
    visto: datetime | None = None,
) -> EditalResumo:
    return EditalResumo(
        fonte="pncp",
        id_na_fonte=str(n),
        objeto="obj",
        orgao_nome="org",
        uf="SP",
        municipio=None,
        modalidade_id=None,
        modalidade_nome="m",
        valor_estimado=None,
        data_encerramento_proposta=encerra,
        link_origem=None,
        linhas=[],
        veredito=veredito,
        motivo_verificar=None,
        primeiro_visto_em=visto,
    )


def _em(dias: int, hora: int = 10, minuto: int = 0) -> datetime:
    return datetime(2026, 11, 26, hora, minuto) + timedelta(days=dias)


def _ids(secao: Secao) -> list[str]:
    return [e.id_na_fonte for e in secao.editais]


def test_dias_ate_encerrar_em_calendario_de_brasilia():
    assert dias_ate_encerrar(datetime(2026, 12, 16, 23, 59), AGORA) == 20
    assert dias_ate_encerrar(datetime(2026, 12, 17, 0, 0), AGORA) == 21
    assert dias_ate_encerrar(datetime(2026, 11, 26, 18, 0), AGORA) == 0


def test_eh_novo_ate_3_dias_inclusive():
    assert eh_novo(AGORA - timedelta(days=3), AGORA) and not eh_novo(
        AGORA - timedelta(days=3, minutes=1), AGORA
    )
    assert not eh_novo(None, AGORA)


def test_decidir_ate_20_dias_e_mais_adiante_depois():
    base = [_edital(1, _em(0)), _edital(2, _em(20, 23, 59)), _edital(3, _em(21, 0))]
    painel = separar_painel(base, AGORA, True)
    assert [s.chave for s in painel.secoes] == ["decidir", "mais_adiante"]
    assert _ids(painel.secoes[0]) == ["1", "2"]
    assert _ids(painel.secoes[1]) == ["3"]
    assert painel.decidir == painel.secoes[0].total == 2


def test_inapto_fora_das_secoes_mas_conta_em_abertos():
    base = [_edital(1, _em(1), "inapto"), _edital(2, _em(2), "apto")]
    painel = separar_painel(base, AGORA, True)
    assert (painel.decidir, painel.abertos) == (1, 2)
    assert _ids(painel.secoes[0]) == ["2"]


def test_sem_considerar_veredito_inapto_entra_em_decidir():
    base = [_edital(1, _em(1), "inapto"), _edital(2, _em(2), "apto")]
    painel = separar_painel(base, AGORA, False)
    assert (painel.decidir, painel.abertos) == (2, 2)


def test_cada_edital_numa_secao_so():
    base = [
        _edital(1, _em(1)),
        _edital(2, _em(30)),
        _edital(3, _em(5), "inapto"),
        _edital(4, _em(20, 23, 59)),
        _edital(5, _em(21, 0)),
    ]
    painel = separar_painel(base, AGORA, True)
    ids = [i for s in painel.secoes for i in _ids(s)]
    assert len(ids) == len(set(ids)) == painel.abertos - 1


def test_secao_cortada_no_limite_mas_total_conta_todos():
    cinco = [_edital(i, _em(1)) for i in range(5)]
    secao = separar_painel(cinco, AGORA, True, limite_secao=2).secoes[0]
    assert (secao.total, len(secao.editais)) == (5, 2)


def test_novos_contam_a_base_inteira():
    recente = AGORA - timedelta(days=1)
    base = [
        _edital(1, _em(1), "inapto", visto=recente),
        _edital(2, _em(40), visto=recente),
        _edital(3, _em(2), visto=AGORA - timedelta(days=10)),
        _edital(4, _em(2), visto=None),
    ]
    assert separar_painel(base, AGORA, True).novos == 2


def test_contador_e_recorte_da_lista_concordam_na_fronteira():
    """Mesma regra de `encerra_em_dias=20` da Lista: 20º dia 23:59 dentro, 21º 00:00 fora."""
    base = [
        _edital(0, datetime(2026, 12, 16, 23, 59)),
        _edital(1, datetime(2026, 12, 17, 0, 0)),
        _edital(2, datetime(2026, 12, 16, 0, 0)),
    ]
    limite = limite_encerra_em(AGORA, 20)
    pela_lista = [e.id_na_fonte for e in base if e.data_encerramento_proposta < limite]  # type: ignore[operator]
    painel = separar_painel(base, AGORA, True)
    assert _ids(painel.secoes[0]) == pela_lista == ["0", "2"]
    assert painel.decidir == len(pela_lista)
    # O recorte da Lista usa a mesma função de limite.
    condicoes = _condicoes_recorte(Recorte(encerra_em_dias=20), AGORA, None)  # type: ignore[arg-type]
    assert len(condicoes) == 1
    assert limite in condicoes[0].compile().params.values()
