"""Interpretação do TCE-PE: pré-filtro, BDI, contrato certo e errado, suspeito e gravação."""

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.precos import (
    PrecoItem,
    apagar_processos,
    bdi_fracao,
    casar_itens,
    contratados_gravados,
    escolher_contrato,
    gravar_precos,
    itens_de_processo,
    processo_de_saneamento,
    processo_homologado,
    processos_gravados,
)

DADOS = Path(__file__).resolve().parent.parent / "dados" / "tcepe"
TERMOS = ["rede coletora de esgoto", "esgotamento sanitario", "rede de agua"]


def _ler(nome: str) -> Any:
    return json.loads((DADOS / f"{nome}.json").read_text(encoding="utf-8"))


def _processo(processo_id: str) -> dict[str, Any]:
    return next(p for p in _ler("processos") if p["processoContratacaoId"] == processo_id)


def _instrumento(instrumento_id: str) -> dict[str, Any]:
    return next(i for i in _ler("instrumentos") if i["instrumentoJuridicoId"] == instrumento_id)


def _pedra() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    return (
        _processo("60116414"),
        _ler("orcamento_60116414"),
        _instrumento("60309578"),
        _ler("itens_instrumento_60309578"),
    )


def _compesa() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    return (
        _processo("60116598"),
        _ler("orcamento_60116598"),
        _instrumento("60314628"),
        _ler("itens_instrumento_60314628"),
    )


# --- pré-filtro ---


@pytest.mark.parametrize(
    ("objeto", "termo", "esperado"),
    [
        ("EXECUÇÃO DE REDE COLETORA DE ESGOTO NO BAIRRO X", "rede coletora de esgoto", True),
        ("AMPLIAÇÃO DAS REDES COLETORAS DE ESGOTOS", "rede coletora de esgoto", True),
        ("AMPLIAÇÃO DE REDE DE �GUA EM SURUBIM", "rede de agua", True),
        ("AMPLIA��O DE REDES DE DISTRIBUI��O", "ampliacao de rede", True),
        ("Aquisição de merenda escolar", "rede de agua", False),
        ("Pavimentação de vias", "rede de agua", False),
    ],
)
def test_processo_de_saneamento(objeto: str, termo: str, esperado: bool) -> None:
    assert processo_de_saneamento({"objeto": objeto}, [termo]) is esperado


def test_pre_filtro_nas_amostras() -> None:
    pedra, _, _, _ = _pedra()
    compesa, _, _, _ = _compesa()
    assert processo_de_saneamento(pedra, TERMOS)
    assert processo_de_saneamento(compesa, TERMOS)
    assert not processo_de_saneamento(_processo("60136399"), TERMOS)
    assert not processo_de_saneamento({"objeto": None}, TERMOS)


def test_processo_homologado() -> None:
    assert processo_homologado(_processo("60116414"))
    assert not processo_homologado({"estagioNome": "Em andamento", "valorHomologado": "10"})
    assert not processo_homologado(
        {"estagioNome": "Concluído - Adjudicado/Homologado", "valorHomologado": "0.0000"}
    )


@pytest.mark.parametrize(
    ("bruto", "fracao"),
    [
        ("0.2467", Decimal("0.2467")),
        ("20.9700", Decimal("0.2097")),
        ("0", Decimal("0")),
        ("", None),
        (None, None),
        ("abc", None),
        ("-1", None),
    ],
)
def test_bdi_fracao(bruto: str | None, fracao: Decimal | None) -> None:
    assert bdi_fracao(bruto) == fracao


# --- itens e contrato ---


def test_pedra_itens_classificados_com_contratado() -> None:
    processo, orcamento, instrumento, itens = _pedra()

    novos = itens_de_processo(processo, orcamento, [(instrumento, itens)], TERMOS)

    # Só os itens que casam com um serviço-padrão (4 dos 20).
    assert sorted(n.servico for n in novos) == [
        "escavacao_vala",
        "ligacao_esgoto",
        "ligacao_esgoto",
        "rede_esgoto",
    ]
    tubo = next(n for n in novos if n.servico == "rede_esgoto")
    assert (tubo.material, tubo.dn, tubo.escopo) == ("pvc", 200, "completo")
    # O unitário do TCE-PE já inclui o BDI; o BDI fica como fração.
    assert tubo.preco_com_bdi == Decimal("145.9800")
    assert tubo.preco_sem_bdi is None
    assert tubo.bdi == Decimal("0.2097")
    assert tubo.bdi_desconhecido is False
    assert tubo.preco_contratado_com_bdi == Decimal("109.5000")
    assert tubo.suspeito is False
    assert tubo.quantidade == Decimal("200")
    assert (tubo.fonte_referencia, tubo.codigo_referencia) == ("SINAPI-N", "90696")
    # Item repetido (mesma descrição e quantidade) casa com cada contrato uma vez.
    ligacoes = [n for n in novos if n.servico == "ligacao_esgoto"]
    assert [n.preco_contratado_com_bdi for n in ligacoes] == [Decimal("345.0500")] * 2
    assert len({n.chave_origem for n in novos}) == 4
    assert all(n.chave_origem.startswith("60116414:") for n in novos)
    assert {(n.origem, n.uf, n.municipio, n.processo) for n in novos} == {
        ("tce_pe", "PE", "Pedra", "462/016/2026")
    }
    assert tubo.data is not None and tubo.data.isoformat() == "2026-04-15"
    # Data-base do preço: o DataReferencia do item (em Pedra, a data do orçamento).
    assert tubo.data_base is not None and tubo.data_base.isoformat() == "2026-01-29"
    escavacao = next(n for n in novos if n.servico == "escavacao_vala")
    assert escavacao.faixa == "ate_1_5m"
    assert tubo.faixa is None


def test_compesa_bdi_em_fracao_e_contrato_pelo_valor_global() -> None:
    processo, orcamento, instrumento, itens = _compesa()

    novos = itens_de_processo(processo, orcamento, [(instrumento, itens)], TERMOS)

    assert [n.servico for n in novos] == ["escavacao_vala", "escavacao_vala"]
    assert [n.preco_contratado_com_bdi for n in novos] == [Decimal("8.3500"), Decimal("12.6500")]
    assert {n.bdi for n in novos} == {Decimal("0.2467")}
    assert {n.municipio for n in novos} == {None}
    # Orçamento da Compesa com base de preço de maio/2025, homologado em 2026.
    assert {n.data_base.isoformat() for n in novos if n.data_base} == {"2025-05-01"}
    assert {n.faixa for n in novos} == {"ate_1_5m"}


def test_contrato_de_outro_processo_e_descartado() -> None:
    """Review Focus 2: o filtro por número trouxe contrato com total diferente do homologado."""
    processo, orcamento, _, itens = _pedra()
    outro = _instrumento("60314628")  # 3.079.999,76, não 580.240,72

    novos = itens_de_processo(
        processo, orcamento, [(outro, _ler("itens_instrumento_60314628"))], TERMOS
    )

    assert len(novos) == 4
    assert all(n.preco_contratado_com_bdi is None for n in novos)
    assert all(n.suspeito is False for n in novos)


def _escalar(itens: list[dict[str, Any]], fator: str) -> list[dict[str, Any]]:
    return [dict(i, ValorTotal=str(Decimal(i["ValorTotal"]) * Decimal(fator))) for i in itens]


def test_contrato_fora_da_tolerancia_de_dois_por_cento() -> None:
    processo, orcamento, instrumento, itens = _pedra()  # homologado 580.240,72
    fora = dict(instrumento, ValorGlobalAtualizado="595000.00")  # +2,5%
    itens_fora = _escalar(itens, "1.025")
    dentro = dict(instrumento, ValorGlobalAtualizado="590000.00")  # +1,7%

    assert escolher_contrato(processo, [(fora, itens_fora)]) is None
    assert escolher_contrato(processo, [(dentro, itens_fora)]) is itens_fora


def test_contrato_a_menos_1_3_por_cento_e_aceito() -> None:
    """Real: Belo Jardim (60127312), contrato 7.499.999,48 contra homologado 7.598.076,01."""
    processo, orcamento, instrumento, itens = _pedra()
    pelo_global = dict(instrumento, ValorGlobalAtualizado="572697.59")  # -1,3%
    itens_longe = _escalar(itens, "0.9")
    so_itens = {k: v for k, v in instrumento.items() if k != "ValorGlobalAtualizado"}
    itens_menos = _escalar(itens, "0.987")  # -1,3% pela soma dos itens

    assert escolher_contrato(processo, [(pelo_global, itens_longe)]) is itens_longe
    assert escolher_contrato(processo, [(so_itens, itens_menos)]) is itens_menos


def test_escolhe_o_contrato_certo_entre_varios() -> None:
    processo, orcamento, instrumento, itens = _pedra()
    outro = _instrumento("60314628")
    outros_itens = _ler("itens_instrumento_60314628")

    escolhido = escolher_contrato(processo, [(outro, outros_itens), (instrumento, itens)])

    assert escolhido is itens


def test_contrato_sem_valor_global_vale_pela_soma_dos_itens() -> None:
    processo, orcamento, instrumento, itens = _pedra()
    sem_global = {k: v for k, v in instrumento.items() if k != "ValorGlobalAtualizado"}

    assert escolher_contrato(processo, [(sem_global, itens)]) is itens


def test_lotes_com_vencedores_diferentes_somam_o_homologado() -> None:
    processo, orcamento, instrumento, itens = _pedra()
    metade = len(itens) // 2
    lote1, lote2 = itens[:metade], itens[metade:]
    contratos = [
        (dict(instrumento, ValorGlobalAtualizado="1"), lote1),
        (dict(instrumento, ValorGlobalAtualizado="1"), lote2),
    ]

    novos = itens_de_processo(processo, orcamento, contratos, TERMOS)

    assert all(n.preco_contratado_com_bdi is not None for n in novos)


def test_razao_fora_da_faixa_marca_suspeito() -> None:
    processo, orcamento, instrumento, itens = _pedra()
    itens = copy.deepcopy(itens)
    for item in itens:
        if item["itemDescricao"].startswith("TUBO DE PVC"):
            item["ValorUnitario"] = str(Decimal("145.98") * 89)  # o caso de 89×
        if item["itemDescricao"].startswith("ESCAVA"):
            item["ValorUnitario"] = "2.00"  # 0,18 do estimado

    novos = itens_de_processo(processo, orcamento, [(instrumento, itens)], TERMOS)

    por_servico = {n.servico: n for n in novos}
    assert por_servico["rede_esgoto"].suspeito is True
    assert por_servico["escavacao_vala"].suspeito is True
    assert all(not n.suspeito for n in novos if n.servico == "ligacao_esgoto")


def test_casa_pela_posicao_quando_so_a_quantidade_difere() -> None:
    _, orcamento, _, itens = _pedra()
    itens = copy.deepcopy(itens)
    itens[5]["Quantidade"] = "210.0000"  # TUBO DE PVC ... DN 200: 200 no orçamento

    casados = casar_itens(orcamento, itens)

    assert casados[5] is itens[5]
    assert len(casados) == 20


def test_sem_posicao_quando_as_listas_tem_tamanhos_diferentes() -> None:
    _, orcamento, _, itens = _pedra()
    itens = copy.deepcopy(itens)
    itens[5]["Quantidade"] = "210.0000"
    del itens[0]

    casados = casar_itens(orcamento, itens)

    assert 5 not in casados
    assert len(casados) == 18


def test_descricao_diferente_nao_casa_pela_posicao() -> None:
    orcamento = [{"itemDescricao": "TUBO PVC DN 150", "Quantidade": "10"}]
    contrato = [{"itemDescricao": "TUBO PEAD DE 160", "Quantidade": "12"}]

    assert casar_itens(orcamento, contrato) == {}


def test_descricao_casa_ignorando_espaco_quebra_e_acento() -> None:
    orcamento = [{"itemDescricao": "ESCAVAÇÃO\tMECANIZADA\nDE VALA", "Quantidade": "10.0000"}]
    contrato = [
        {"itemDescricao": "outro", "Quantidade": "1"},
        {"itemDescricao": "escavacao mecanizada de  vala", "Quantidade": "10"},
    ]

    assert casar_itens(orcamento, contrato) == {0: contrato[1]}


def test_processo_fora_do_pre_filtro_nao_gera_itens() -> None:
    _, orcamento, instrumento, itens = _pedra()
    processo = dict(_processo("60116414"), objeto="Aquisição de merenda escolar")

    assert itens_de_processo(processo, orcamento, [(instrumento, itens)], TERMOS) == []


def test_item_sem_preco_fica_de_fora() -> None:
    processo, orcamento, instrumento, itens = _pedra()
    orcamento = copy.deepcopy(orcamento)
    for item in orcamento:
        if item["itemDescricao"].startswith("TUBO DE PVC"):
            item["ValorUnitario"] = "0.0000"

    novos = itens_de_processo(processo, orcamento, [(instrumento, itens)], TERMOS)

    assert "rede_esgoto" not in {n.servico for n in novos}


# --- gravação ---


def _contar(sessao: Session) -> int:
    return sessao.scalar(select(func.count()).select_from(PrecoItem)) or 0


def test_gravar_precos_upsert_idempotente(sessao: Session) -> None:
    processo, orcamento, instrumento, itens = _pedra()
    novos = itens_de_processo(processo, orcamento, [(instrumento, itens)], TERMOS)

    assert gravar_precos(sessao, novos) == 4
    assert gravar_precos(sessao, novos) == 4
    assert _contar(sessao) == 4

    # Nova rodada sem o contrato (ex.: contrato aditado) atualiza a mesma linha.
    sem_contrato = itens_de_processo(processo, orcamento, [], TERMOS)
    assert gravar_precos(sessao, sem_contrato) == 4
    sessao.expire_all()
    linhas = sessao.scalars(select(PrecoItem)).all()
    assert len(linhas) == 4
    assert all(linha.preco_contratado_com_bdi is None for linha in linhas)
    tubo = next(linha for linha in linhas if linha.servico == "rede_esgoto")
    assert (tubo.material, tubo.dn, tubo.escopo, tubo.unidade_canonica) == (
        "pvc",
        200,
        "completo",
        "m",
    )
    assert tubo.bdi == Decimal("0.2097")
    assert tubo.fator_unidade == Decimal("1")
    assert tubo.data_base is not None
    escavacao = next(linha for linha in linhas if linha.servico == "escavacao_vala")
    assert escavacao.faixa == "ate_1_5m"


def test_pre_filtro_usa_os_termos_da_config_por_padrao() -> None:
    pedra, _, _, _ = _pedra()
    compesa, _, _, _ = _compesa()
    assert processo_de_saneamento(pedra)
    assert processo_de_saneamento(compesa)
    assert not processo_de_saneamento(_processo("60136399"))  # caixas d'agua de escolas
    # Os termos da 7a sozinhos nao pegam os dois casos de referencia.
    termos_7a = ["rede de agua", "rede de esgoto", "ligacao de agua", "ligacao de esgoto"]
    assert not processo_de_saneamento(pedra, termos_7a)
    assert not processo_de_saneamento(compesa, termos_7a)


def test_gravar_lista_vazia(sessao: Session) -> None:
    assert gravar_precos(sessao, []) == 0
    assert _contar(sessao) == 0


# --- Fix round 2: duplicados, unidade e limpeza ---


def _lotes_iguais(precos: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    descricao = "LIGAÇÃO PREDIAL DE ESGOTO TIPO II EM PISO CIMENTADO"
    orcamento = [
        {
            "itemId": f"9{n}",
            "itemDescricao": descricao,
            "UnidadeMedida": "Ud",
            "Quantidade": "50",
            "ValorUnitario": "460.06",
        }
        for n in range(2)
    ]
    contrato = [
        {
            "itemDescricao": descricao,
            "UnidadeMedida": "Ud",
            "Quantidade": "50.0000",
            "ValorUnitario": preco,
        }
        for preco in precos
    ]
    return orcamento, contrato


def test_dois_lotes_iguais_com_vencedores_de_precos_diferentes_nao_casam() -> None:
    processo = _processo("60116414")
    orcamento, contrato = _lotes_iguais(["345.05", "401.10"])  # vencedor A e vencedor B
    total = sum(Decimal(c["ValorUnitario"]) * 50 for c in contrato)
    instrumento = {"ValorGlobalAtualizado": str(total)}
    processo = dict(processo, valorHomologado=str(total))

    novos = itens_de_processo(processo, orcamento, [(instrumento, contrato)], TERMOS)

    assert [n.servico for n in novos] == ["ligacao_esgoto", "ligacao_esgoto"]
    assert all(n.preco_contratado_com_bdi is None for n in novos)
    assert casar_itens(orcamento, contrato) == {}


def test_lotes_iguais_com_o_mesmo_preco_casam() -> None:
    orcamento, contrato = _lotes_iguais(["345.05", "345.05"])

    assert casar_itens(orcamento, contrato) == {0: contrato[0], 1: contrato[1]}


def test_chave_repetida_com_contagem_diferente_nao_casa() -> None:
    orcamento, contrato = _lotes_iguais(["345.05"])  # 2 no orçamento, 1 no contrato

    assert casar_itens(orcamento, contrato) == {}


def test_unidade_diferente_nao_casa_nem_pela_posicao() -> None:
    orcamento = [{"itemDescricao": "TUBO PVC DN 150", "UnidadeMedida": "M", "Quantidade": "1000"}]
    contrato = [{"itemDescricao": "TUBO PVC DN 150", "UnidadeMedida": "KM", "Quantidade": "1"}]

    assert casar_itens(orcamento, contrato) == {}


def test_unidade_diferente_nao_casa_pela_chave() -> None:
    orcamento = [{"itemDescricao": "TUBO PVC DN 150", "UnidadeMedida": "M", "Quantidade": "1"}]
    contrato = [{"itemDescricao": "TUBO PVC DN 150", "UnidadeMedida": "KM", "Quantidade": "1"}]

    assert casar_itens(orcamento, contrato) == {}


def test_grafias_da_mesma_unidade_casam() -> None:
    orcamento = [{"itemDescricao": "LIGACAO", "UnidadeMedida": "UN", "Quantidade": "5"}]
    contrato = [{"itemDescricao": "LIGACAO", "UnidadeMedida": "Ud.", "Quantidade": "5"}]

    assert casar_itens(orcamento, contrato) == {0: contrato[0]}


def test_contrato_sem_unidade_so_casa_pela_posicao_com_a_mesma_quantidade() -> None:
    orcamento = [
        {"itemDescricao": "TUBO PVC DN 150", "UnidadeMedida": "M", "Quantidade": "1000"},
        {"itemDescricao": "TUBO PVC DN 200", "UnidadeMedida": "M", "Quantidade": "10"},
    ]
    contrato = [
        {"itemDescricao": "TUBO PVC DN 150", "Quantidade": "1"},
        {"itemDescricao": "TUBO PVC DN 200", "Quantidade": "10"},
    ]

    assert casar_itens(orcamento, contrato) == {1: contrato[1]}


def test_gravar_apaga_itens_antigos_do_mesmo_processo(sessao: Session) -> None:
    pedra = _pedra()
    compesa = _compesa()
    novos_pedra = itens_de_processo(pedra[0], pedra[1], [(pedra[2], pedra[3])], TERMOS)
    novos_compesa = itens_de_processo(compesa[0], compesa[1], [(compesa[2], compesa[3])], TERMOS)
    gravar_precos(sessao, novos_pedra + novos_compesa)
    assert _contar(sessao) == 6

    # Nova rodada de Pedra em que só o tubo continua casando com um serviço.
    so_tubo = [n for n in novos_pedra if n.servico == "rede_esgoto"]
    assert gravar_precos(sessao, so_tubo) == 1

    sessao.expire_all()
    restantes = sessao.scalars(select(PrecoItem)).all()
    assert sorted((linha.processo, linha.servico) for linha in restantes) == [
        ("462/016/2026", "rede_esgoto"),
        ("802/4015/2026", "escavacao_vala"),
        ("802/4015/2026", "escavacao_vala"),
    ]


def test_chave_repetida_no_lote_avisa(sessao: Session, caplog: pytest.LogCaptureFixture) -> None:
    processo, orcamento, instrumento, itens = _pedra()
    novos = itens_de_processo(processo, orcamento, [(instrumento, itens)], TERMOS)

    with caplog.at_level("WARNING", logger="radar.precos.tcepe"):
        assert gravar_precos(sessao, [novos[0], novos[0]]) == 1

    assert "preco_item_chave_repetida" in caplog.text


def test_limpeza_e_pelo_id_do_processo_nao_pelo_rotulo(sessao: Session) -> None:
    # Dois processos com o mesmo "uj/numero/ano" (2.245 casos na listagem de 2026).
    processo, orcamento, instrumento, itens = _pedra()
    gemeo = dict(processo, processoContratacaoId="99999999")
    novos = itens_de_processo(processo, orcamento, [(instrumento, itens)], TERMOS)
    novos_gemeo = itens_de_processo(gemeo, orcamento, [], TERMOS)
    assert {n.processo for n in novos} == {n.processo for n in novos_gemeo}
    gravar_precos(sessao, novos + novos_gemeo)
    assert _contar(sessao) == 8

    # Regravar só o tubo do gêmeo não apaga nada do outro processo.
    gravar_precos(sessao, [n for n in novos_gemeo if n.servico == "rede_esgoto"])

    sessao.expire_all()
    chaves = sessao.scalars(select(PrecoItem.chave_origem)).all()
    assert sum(c.startswith("60116414:") for c in chaves) == 4
    assert sum(c.startswith("99999999:") for c in chaves) == 1


def test_apagar_processos_e_contratados_gravados(sessao: Session) -> None:
    processo, orcamento, instrumento, itens = _pedra()
    novos = itens_de_processo(processo, orcamento, [(instrumento, itens)], TERMOS)
    gravar_precos(sessao, novos)
    assert processos_gravados(sessao) == {"60116414"}

    anteriores = contratados_gravados(sessao, "60116414", processo["valorHomologado"])
    assert anteriores == {n.chave_origem: n.preco_contratado_com_bdi for n in novos}
    # Reaproveitar dá os mesmos itens, sem casar o contrato de novo.
    assert itens_de_processo(processo, orcamento, [], TERMOS, contratados=anteriores) == novos
    assert contratados_gravados(sessao, "60116414", "1.00") is None
    assert contratados_gravados(sessao, "6011641", processo["valorHomologado"]) is None

    assert apagar_processos(sessao, ["6011641"]) == 0  # prefixo não é o id
    assert apagar_processos(sessao, ["60116414"]) == 4
    assert _contar(sessao) == 0
