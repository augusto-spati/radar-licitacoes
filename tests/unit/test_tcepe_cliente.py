import json
from pathlib import Path

import httpx
import pytest

from radar.coleta import ClienteTcepe, ErroTcepe, NumeroSemBusca, criar_http_tcepe
from radar.coleta.tcepe import decodificar

DADOS = Path(__file__).resolve().parents[1] / "dados" / "tcepe"


def _amostra(nome: str) -> list[dict]:
    return json.loads((DADOS / nome).read_text(encoding="utf-8"))


def _envelope(entidade: str, conteudo: list[dict], limite: int = 100000) -> bytes:
    """Resposta como o TCE-PE manda: JSON em ISO-8859-1, tudo texto."""
    corpo = {
        "resposta": {
            "status": "OK",
            "entidade": entidade,
            "tamanhoResultado": str(len(conteudo)),
            "limiteResultado": str(limite),
            "conteudo": conteudo,
        }
    }
    return json.dumps(corpo, ensure_ascii=False).encode("iso-8859-1", errors="replace")


def _resposta(entidade: str, conteudo: list[dict], **kw) -> httpx.Response:
    return httpx.Response(
        200,
        content=_envelope(entidade, conteudo, **kw),
        headers={"Content-Type": "application/json;charset=ISO-8859-1"},
    )


def _cliente(handler) -> ClienteTcepe:
    http = httpx.Client(
        base_url="https://sistemas.tcepe.tc.br/DadosAbertos",
        transport=httpx.MockTransport(handler),
    )
    return ClienteTcepe(http, pausa_s=0, tentativas=3, espera_base=0)


def test_http_do_projeto():
    http = criar_http_tcepe()
    assert http.headers["User-Agent"].startswith("radar-licitacoes-estudo")
    assert str(http.base_url).startswith("https://sistemas.tcepe.tc.br/DadosAbertos")
    http.close()


def test_processos_do_ano_com_filtro_e_caminho():
    amostra = _amostra("processos.json")
    pedidos = []

    def handler(req):
        pedidos.append(req)
        return _resposta("Remessa_ProcessoContratacao", amostra)

    processos = _cliente(handler).processos(2026)
    assert [p["processoContratacaoId"] for p in processos] == [
        p["processoContratacaoId"] for p in amostra
    ]
    assert (
        pedidos[0]
        .url.raw_path.decode()
        .startswith("/DadosAbertos/Remessa_ProcessoContratacao!json?")
    )
    assert pedidos[0].url.params["anoContratacao"] == "2026"


def test_orcamento_decodifica_iso_8859_1():
    amostra = _amostra("orcamento_60116598.json")

    def handler(req):
        assert req.url.params["processoContratacaoId"] == "60116598"
        return _resposta("Remessa_ProcessoContratacaoOrcamento", amostra)

    itens = _cliente(handler).orcamento(60116598)
    assert itens[0]["itemDescricao"] == "ADMINISTRAÇÃO LOCAL"
    assert itens == amostra


def test_fffd_guardado_em_utf8_volta_a_ser_fffd():
    # como veio no ano de 2026: o resto em Latin-1, o apóstrofo guardado como U+FFFD em UTF-8
    latin = b"caixas d" + b"\xef\xbf\xbd" + "água".encode("iso-8859-1")
    assert decodificar(latin) == "caixas d�água"


def test_resposta_com_fffd_em_utf8_no_meio_do_latin1():
    registro = {"processoContratacaoId": "1", "objeto": "Olho D�Água"}
    corpo = _envelope("Remessa_ProcessoContratacaoOrcamento", [registro]).replace(
        b"?", b"\xef\xbf\xbd"
    )

    def handler(req):
        return httpx.Response(200, content=corpo)

    itens = _cliente(handler).orcamento(1)
    assert itens[0]["objeto"] == "Olho D�Água"


def test_instrumentos_com_os_tres_filtros():
    amostra = [i for i in _amostra("instrumentos.json") if i["unidadeJurisdicionadaId"] == "462"]

    def handler(req):
        assert dict(req.url.params) == {
            "unidadeJurisdicionadaId": "462",
            "NumeroProcessoContratacao": "016",
            "AnoProcessoContratacao": "2026",
        }
        return _resposta("Remessa_InstrumentoJuridico", amostra)

    instrumentos = _cliente(handler).instrumentos(462, "016", 2026)
    assert instrumentos[0]["instrumentoJuridicoId"] == "60309578"


def test_itens_do_instrumento():
    amostra = _amostra("itens_instrumento_60309578.json")

    def handler(req):
        assert req.url.params["instrumentoJuridicoId"] == "60309578"
        return _resposta("Remessa_InstrumentoJuridicoItens", amostra)

    itens = _cliente(handler).itens_instrumento("60309578")
    assert len(itens) == 20
    assert itens[0]["UnidadeMedida"] == "m²"


def test_lista_vazia():
    def handler(req):
        return _resposta("Remessa_ProcessoContratacaoOrcamento", [])

    assert _cliente(handler).orcamento(1) == []


def test_filtro_ignorado_vira_erro():
    # parâmetro desconhecido: o TCE-PE devolve registros de outros processos em silêncio
    outros = [{"processoContratacaoId": "60084093"}, {"processoContratacaoId": "60116598"}]

    def handler(req):
        return _resposta("Remessa_ProcessoContratacaoOrcamento", outros)

    with pytest.raises(ErroTcepe, match="ignorou o filtro processoContratacaoId"):
        _cliente(handler).orcamento(60116598)


def test_instrumento_de_outra_uj_vira_erro():
    amostra = _amostra("instrumentos.json")  # um da Compesa (802) e um de Pedra (462)

    def handler(req):
        return _resposta("Remessa_InstrumentoJuridico", amostra)

    with pytest.raises(ErroTcepe, match="unidadeJurisdicionadaId"):
        _cliente(handler).instrumentos(462, "016", 2026)


def test_numero_com_zeros_a_esquerda_confere():
    amostra = [i for i in _amostra("instrumentos.json") if i["unidadeJurisdicionadaId"] == "462"]

    def handler(req):
        return _resposta("Remessa_InstrumentoJuridico", amostra)

    assert len(_cliente(handler).instrumentos(462, "16", 2026)) == 1


def test_numero_por_substring_descarta_o_outro_processo():
    # Real (UJ 66, 2026): pedir o processo 44 trouxe também o contrato do processo 144.
    base = {"unidadeJurisdicionadaId": "66", "AnoProcessoContratacao": "2026"}
    registros = [
        dict(base, instrumentoJuridicoId="60320712", NumeroProcessoContratacao="44"),
        dict(base, instrumentoJuridicoId="60341704", NumeroProcessoContratacao="144"),
        dict(base, instrumentoJuridicoId="60341705", NumeroProcessoContratacao="0440"),
    ]

    def handler(req):
        return _resposta("Remessa_InstrumentoJuridico", registros)

    achados = _cliente(handler).instrumentos(66, "44", 2026)

    assert [i["instrumentoJuridicoId"] for i in achados] == ["60320712"]


def test_numero_que_nem_contem_o_pedido_vira_erro():
    registros = [
        {
            "unidadeJurisdicionadaId": "66",
            "AnoProcessoContratacao": "2026",
            "NumeroProcessoContratacao": "51",
        }
    ]

    def handler(req):
        return _resposta("Remessa_InstrumentoJuridico", registros)

    with pytest.raises(ErroTcepe, match="NumeroProcessoContratacao"):
        _cliente(handler).instrumentos(66, "44", 2026)


def test_resultado_no_limite_e_truncado():
    registros = [{"anoContratacao": "2026"}] * 3

    def handler(req):
        return _resposta("Remessa_ProcessoContratacao", registros, limite=3)

    with pytest.raises(ErroTcepe, match="limite"):
        _cliente(handler).processos(2026)


def test_json_invalido_uma_vez_depois_ok():
    amostra = _amostra("itens_instrumento_60314628.json")
    respostas = iter(
        [
            httpx.Response(200, content=b'{"resposta": {}} {"resposta": {}}'),  # Extra data
            _resposta("Remessa_InstrumentoJuridicoItens", amostra),
        ]
    )
    chamadas = []

    def handler(req):
        chamadas.append(req)
        return next(respostas)

    assert _cliente(handler).itens_instrumento(60314628) == amostra
    assert len(chamadas) == 2


def test_json_invalido_sempre_vira_erro_tcepe():
    def handler(req):
        return httpx.Response(200, content=b"{quebrado")

    with pytest.raises(ErroTcepe, match="3 tentativas"):
        _cliente(handler).orcamento(1)


def test_429_seguido_de_200_tenta_de_novo():
    estados = iter([429, 200])

    def handler(req):
        if next(estados) == 429:
            return httpx.Response(429)
        return _resposta("Remessa_ProcessoContratacaoOrcamento", [])

    assert _cliente(handler).orcamento(1) == []


def test_erro_de_transporte_tenta_de_novo():
    estados = iter(["falha", "ok"])

    def handler(req):
        if next(estados) == "falha":
            raise httpx.ConnectError("caiu")
        return _resposta("Remessa_ProcessoContratacaoOrcamento", [])

    assert _cliente(handler).orcamento(1) == []


def test_500_sempre_vira_erro_tcepe():
    chamadas = []

    def handler(req):
        chamadas.append(req)
        return httpx.Response(500, content=b"ERRO")

    with pytest.raises(ErroTcepe):
        _cliente(handler).orcamento(1)
    assert len(chamadas) == 3


def test_404_nao_tenta_de_novo():
    chamadas = []

    def handler(req):
        chamadas.append(req)
        return httpx.Response(404, content=b"nada")

    with pytest.raises(ErroTcepe, match="HTTP 404"):
        _cliente(handler).orcamento(1)
    assert len(chamadas) == 1


def test_status_diferente_de_ok_vira_erro():
    def handler(req):
        return httpx.Response(200, json={"resposta": {"status": "ERRO", "conteudo": []}})

    with pytest.raises(ErroTcepe, match="status"):
        _cliente(handler).orcamento(1)


def test_id_nao_numerico_nem_chama():
    def handler(req):  # pragma: no cover - não deve ser chamado
        raise AssertionError("não devia chamar")

    cliente = _cliente(handler)
    with pytest.raises(ErroTcepe, match="numérico"):
        cliente.orcamento("abc")
    with pytest.raises(ErroTcepe, match="numérico"):
        cliente.instrumentos("x", "016", 2026)
    with pytest.raises(NumeroSemBusca, match="sem número"):
        cliente.instrumentos(462, " ", 2026)


def test_numero_nao_numerico_busca_e_confere_por_igualdade():
    # Real (2026-11-27): UJ 800, processo "009.CELOSE" devolveu o contrato 072/2026 dele. Números
    # assim ("CELOEII-023", "260202DE00003") não podem derrubar o processo.
    base = {"unidadeJurisdicionadaId": "800", "AnoProcessoContratacao": "2026"}
    registros = [
        dict(base, instrumentoJuridicoId="60314776", NumeroProcessoContratacao="009.CELOSE"),
        dict(base, instrumentoJuridicoId="1", NumeroProcessoContratacao="1009.CELOSE"),
    ]
    pedidos: list[httpx.URL] = []

    def handler(req):
        pedidos.append(req.url)
        return _resposta("Remessa_InstrumentoJuridico", registros)

    achados = _cliente(handler).instrumentos(800, "009.celose", 2026)

    assert [i["instrumentoJuridicoId"] for i in achados] == ["60314776"]
    assert pedidos[0].params["NumeroProcessoContratacao"] == "009.celose"


def test_numero_nao_numerico_de_outro_processo_vira_erro():
    registros = [
        {
            "unidadeJurisdicionadaId": "800",
            "AnoProcessoContratacao": "2026",
            "NumeroProcessoContratacao": "CELOEII-023",
        }
    ]

    def handler(req):
        return _resposta("Remessa_InstrumentoJuridico", registros)

    with pytest.raises(ErroTcepe, match="NumeroProcessoContratacao"):
        _cliente(handler).instrumentos(800, "009.CELOSE", 2026)


def test_registro_sem_o_campo_do_filtro_vira_erro():
    # Sem o campo não dá para conferir: trata como filtro ignorado (mesmo com valor "0").
    def handler(req):
        return _resposta("Remessa_ProcessoContratacaoOrcamento", [{"itemId": "1"}])

    with pytest.raises(ErroTcepe, match="ignorou o filtro"):
        _cliente(handler).orcamento(0)


def test_pausa_de_cortesia_entre_requisicoes(monkeypatch):
    relogio = [100.0]
    dormidas: list[float] = []

    def dormir(segundos):
        dormidas.append(segundos)
        relogio[0] += segundos

    monkeypatch.setattr("radar.coleta.tcepe.time.monotonic", lambda: relogio[0])
    monkeypatch.setattr("radar.coleta.tcepe.time.sleep", dormir)

    def handler(req):
        relogio[0] += 0.25  # a requisição leva 0,25 s
        return _resposta("Remessa_ProcessoContratacaoOrcamento", [])

    http = httpx.Client(
        base_url="https://sistemas.tcepe.tc.br/DadosAbertos",
        transport=httpx.MockTransport(handler),
    )
    cliente = ClienteTcepe(http, pausa_s=1.0, tentativas=1, espera_base=0)
    cliente.orcamento(1)
    cliente.orcamento(2)
    cliente.orcamento(3)

    # A primeira sai na hora; as seguintes esperam o que falta de 1 s desde a anterior.
    assert dormidas == [pytest.approx(0.75), pytest.approx(0.75)]


def test_amostras_reais_sem_cpf_e_coerentes():
    for arquivo in DADOS.glob("*.json"):
        assert "cpf" not in arquivo.read_text(encoding="utf-8").lower(), arquivo.name
    orcamento = _amostra("orcamento_60116414.json")
    contratado = _amostra("itens_instrumento_60309578.json")
    assert [o["itemDescricao"] for o in orcamento] == [c["itemDescricao"] for c in contratado]
    total = sum(float(c["ValorTotal"]) for c in contratado)
    pedra = next(p for p in _amostra("processos.json") if p["processoContratacaoId"] == "60116414")
    assert total == pytest.approx(float(pedra["valorHomologado"]), abs=0.05)
