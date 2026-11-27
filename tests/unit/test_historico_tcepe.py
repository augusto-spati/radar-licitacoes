"""Processo da Compesa no TCE-PE → caso do histórico do órgão (etapa 7a.3), com amostras reais."""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, get_args
from zoneinfo import ZoneInfo

from radar.editais import (
    ROTULOS_FONTE,
    OrigemHistorico,
    alvo_historico_tcepe,
    caso_tcepe,
    contratos_do_processo,
    vencedor_tcepe,
)
from radar.editais.externos import VencedorExterno, numero_licitacao
from radar.editais.modelos import Fonte, FontesExternas

DADOS = Path(__file__).resolve().parents[1] / "dados" / "tcepe"
COMPESA = "09769035000164"


def _ler(nome: str) -> Any:
    return json.loads((DADOS / f"{nome}.json").read_text(encoding="utf-8"))


def _adutora() -> dict[str, Any]:
    return dict(_ler("processos_compesa_historico")[0])


def test_caso_da_adutora_do_agreste() -> None:
    processo = _adutora()
    vencedor = VencedorExterno("10514594000102", "EMCOSA EMPRESA DE CONSTRUCOES E SANEAMENTO LTDA")
    caso = caso_tcepe(processo, ["redes"], vencedor)
    assert caso is not None
    assert (caso.fonte, caso.id_na_fonte) == ("tce_pe", "60120073")
    assert (caso.orgao_cnpj, caso.uf, caso.situacao) == (COMPESA, "PE", "homologada")
    assert caso.numero == "68/2026"  # numeroModalidade "068" de 2026: "LC 068/2026" no portal
    assert caso.valor_estimado == Decimal("21883600.4800")
    assert caso.valor_homologado == Decimal("20345351.9900")
    # 1 − 20.345.351,99 ÷ 21.883.600,48
    assert caso.desconto == Decimal("0.0703")
    assert caso.data == datetime(2026, 4, 15)  # sessão de abertura
    assert caso.data_resultado == datetime(2026, 7, 1, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert caso.vencedor_ni == "10514594000102"
    assert caso.linhas == ["redes"]
    assert caso.vencedor_porte is None


def test_so_compesa_homologada_com_linha() -> None:
    processo = _adutora()
    assert alvo_historico_tcepe(processo)
    prefeitura = {**processo, "unidadeJurisdicionadaId": "462"}
    assert not alvo_historico_tcepe(prefeitura)
    assert caso_tcepe(prefeitura, ["redes"], None) is None
    for estagio in ("Concluído - Revogado", "Concluído - Anulado", "Em andamento"):
        fora = {**processo, "estagioNome": estagio}
        assert not alvo_historico_tcepe(fora)
        assert caso_tcepe(fora, ["redes"], None) is None
    assert caso_tcepe({**processo, "valorHomologado": "0"}, ["redes"], None) is None
    assert caso_tcepe(processo, [], None) is None  # sem linha do filtro.toml
    # U+FFFD no lugar do acento (como o TCE-PE às vezes manda) ainda é homologado.
    assert alvo_historico_tcepe(
        {**processo, "estagioNome": "Conclu\ufffddo - Adjudicado/Homologado"}
    )


def test_sigiloso_ou_sem_estimado_fica_sem_desconto() -> None:
    processo = _adutora()
    for variacao in ({"orcamentoSigiloso": "1"}, {"valorGlobal": "0"}, {"valorGlobal": ""}):
        caso = caso_tcepe({**processo, **variacao}, ["redes"], None)
        assert caso is not None
        assert caso.desconto is None and caso.valor_estimado is None
        assert caso.valor_homologado == Decimal("20345351.9900")


def test_sem_sessao_usa_publicacao() -> None:
    processo = {k: v for k, v in _adutora().items() if k != "dataSessaoAbertura"}
    caso = caso_tcepe(processo, ["redes"], None)
    assert caso is not None and caso.data == datetime(2026, 3, 17)


def test_contrato_que_bate_com_o_homologado() -> None:
    processo = _adutora()
    instrumentos = _ler("instrumentos_compesa_4044")
    outro = {**instrumentos[0], "instrumentoJuridicoId": "1", "ValorGlobalAtualizado": "1000.00"}
    escolhidos = contratos_do_processo(processo, [outro, *instrumentos])
    assert [i["instrumentoJuridicoId"] for i in escolhidos] == ["60332570"]
    assert contratos_do_processo(processo, [outro]) == []


def test_lotes_somam_o_homologado() -> None:
    processo = {**_adutora(), "valorHomologado": "300"}
    a = {"instrumentoJuridicoId": "1", "ValorGlobalAtualizado": "100"}
    b = {"instrumentoJuridicoId": "2", "ValorGlobalAtualizado": "200"}
    assert contratos_do_processo(processo, [a, b]) == [a, b]
    pj_a = [
        {
            "participanteNome": "A LTDA",
            "participanteDocumento": "11111111000111",
            "tipoParticipanteId": "PJ",
        }
    ]
    pj_b = [
        {
            "participanteNome": "B LTDA",
            "participanteDocumento": "22222222000122",
            "tipoParticipanteId": "PJ",
        }
    ]
    # Vitória vai para quem levou o maior valor.
    assert vencedor_tcepe([(a, pj_a), (b, pj_b)]) == VencedorExterno("22222222000122", "B LTDA")


def test_vencedor_do_contrato_e_fallback_nos_participantes() -> None:
    instrumento = _ler("instrumentos_compesa_4044")[0]
    contratada = _ler("participantes_instrumento_60332570")
    esperado = VencedorExterno("10514594000102", "EMCOSA EMPRESA DE CONSTRUCOES E SANEAMENTO LTDA")
    assert vencedor_tcepe([(instrumento, contratada)]) == esperado
    # Sem contrato: o participante do processo com valor homologado (o não habilitado não tem).
    assert vencedor_tcepe([], _ler("participantes_processo_60120073")) == esperado
    assert vencedor_tcepe([], []) is None


def test_pessoa_fisica_sem_cpf() -> None:
    pf = [
        {
            "participanteNome": "FULANA",
            "participanteDocumento": "***094194**",
            "tipoParticipanteId": "PF",
        }
    ]
    vencedor = vencedor_tcepe([({"ValorGlobalAtualizado": "1"}, pf)])
    assert vencedor == VencedorExterno(None, "Pessoa física")


def test_numero_da_licitacao_do_portal() -> None:
    assert numero_licitacao("LC 213/2026 CEL-1") == "213/2026"
    assert numero_licitacao("LC 068/2026 CPL") == "68/2026"
    assert numero_licitacao("ECR 001/2026") == "1/2026"
    assert numero_licitacao(None) is None


def test_fracassado_e_deserto_viram_caso_sem_valores_de_resultado() -> None:
    vencedor = VencedorExterno("10514594000102", "EMCOSA")
    for estagio, situacao in (
        ("Concluído - Fracassado", "fracassada"),
        ("Concluído - Deserto", "deserta"),
    ):
        processo = {**_adutora(), "estagioNome": estagio}
        assert alvo_historico_tcepe(processo)
        caso = caso_tcepe(processo, ["redes"], vencedor)
        assert caso is not None
        assert caso.situacao == situacao
        # Como a fracassada do PNCP: estimado para a tela, sem homologado, desconto nem vencedor.
        assert caso.valor_estimado == Decimal("21883600.4800")
        assert (caso.valor_homologado, caso.desconto) == (None, None)
        assert (caso.vencedor_ni, caso.vencedor_nome, caso.vencedor_porte) == (None, None, None)


def test_toda_fonte_do_historico_tem_rotulo() -> None:
    fontes = {Fonte.pncp.value, *get_args(FontesExternas)}
    assert set(ROTULOS_FONTE) == fontes
    assert set(ROTULOS_FONTE.values()) == set(get_args(OrigemHistorico))
    assert ROTULOS_FONTE["tce_pe"] == "TCE-PE"
