import copy
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from radar.api.app import criar_app
from radar.coleta.modelos import ColetaJanela
from radar.editais import registrar
from radar.precos import PrecoItem

RAIZ = Path(__file__).resolve().parent.parent.parent
AMOSTRA: dict[str, Any] = json.loads(
    (RAIZ / "tests" / "dados" / "registro_pregao.json").read_text(encoding="utf-8")
)
AGORA = datetime(2026, 11, 26, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))


class _SessaoEmprestada:
    """Entrega a sessão do teste sem fechá-la ao fim de cada requisição."""

    def __init__(self, sessao: Session) -> None:
        self._sessao = sessao

    def __enter__(self) -> Session:
        return self._sessao

    def __exit__(
        self,
        tipo: type[BaseException] | None,
        erro: BaseException | None,
        pilha: TracebackType | None,
    ) -> None:
        return None


@pytest.fixture
def config_filtro(tmp_path: Path) -> Path:
    caminho = tmp_path / "filtro.toml"
    caminho.write_text(
        'ufs = ["SP", "PR"]\nmodalidades = [6]\n\n'
        '[linhas]\nperdas = ["hidrometro", "vazamento"]\nredes = ["rede de agua"]\n',
        encoding="utf-8",
    )
    return caminho


@pytest.fixture
def cliente(sessao: Session, config_filtro: Path) -> TestClient:
    fabrica = cast(Callable[[], Session], lambda: _SessaoEmprestada(sessao))
    return TestClient(criar_app(fabrica, config_filtro, relogio=lambda: AGORA))


def _registro(
    numero: str,
    objeto: str,
    uf: str = "SP",
    encerramento: str | None = "2026-12-11T10:00:00",
    valor: float | None = 1369661.52,
) -> dict[str, Any]:
    registro = copy.deepcopy(AMOSTRA)
    registro["numeroControlePNCP"] = numero
    registro["objetoCompra"] = objeto
    registro["unidadeOrgao"]["ufSigla"] = uf
    registro["dataEncerramentoProposta"] = encerramento
    registro["valorTotalEstimado"] = valor
    registro["situacaoCompraId"] = 1
    return registro


def test_status_sem_coleta_devolve_nulo(cliente: TestClient) -> None:
    resposta = cliente.get("/api/status")

    assert resposta.status_code == 200
    assert resposta.json() == {"atualizado_em": None}


def test_status_com_coleta_devolve_iso_com_fuso(cliente: TestClient, sessao: Session) -> None:
    sessao.add(
        ColetaJanela(
            endpoint="publicacao",
            data_ini=AGORA.date(),
            data_fim=AGORA.date(),
            modalidade=6,
            status="concluida",
            paginas=1,
            registros=1,
            concluida_em=datetime(2026, 11, 26, 15, 30, tzinfo=UTC),
        )
    )
    sessao.flush()

    atualizado = cliente.get("/api/status").json()["atualizado_em"]

    assert datetime.fromisoformat(atualizado) == datetime(2026, 11, 26, 15, 30, tzinfo=UTC)
    assert atualizado.endswith("-03:00")


def test_lista_abertos_ordenados_por_prazo(cliente: TestClient, sessao: Session) -> None:
    registrar(
        sessao,
        [
            _registro("tarde/2026", "Conserto de vazamento", encerramento="2026-12-20T10:00:00"),
            _registro("cedo/2026", "Conserto de vazamento", encerramento="2026-12-01T10:00:00"),
            _registro("velho/2026", "Conserto de vazamento", encerramento="2026-11-20T10:00:00"),
        ],
    )

    abertos = cliente.get("/api/editais").json()
    todos = cliente.get("/api/editais", params={"todos": "true"}).json()

    assert [e["id_publico"] for e in abertos["editais"]] == ["pncp~cedo/2026", "pncp~tarde/2026"]
    assert [e["id_publico"] for e in todos["editais"]] == [
        "pncp~velho/2026",
        "pncp~cedo/2026",
        "pncp~tarde/2026",
    ]
    assert abertos["editais"][0]["data_encerramento_proposta"] == "2026-12-01T10:00:00"
    assert abertos["editais"][0]["linhas"] == ["perdas"]
    assert abertos["atualizado_em"] is None


def test_lista_filtra_linha_e_uf(cliente: TestClient, sessao: Session) -> None:
    registrar(
        sessao,
        [
            _registro("perdas-sp/2026", "Conserto de vazamento", "SP"),
            _registro("perdas-pr/2026", "Conserto de vazamento", "PR"),
            _registro("redes-sp/2026", "Manutenção de rede de água", "SP"),
        ],
    )

    por_linha = cliente.get("/api/editais", params={"linha": "redes"}).json()
    por_uf = cliente.get("/api/editais", params=[("uf", "PR")]).json()
    por_duas_ufs = cliente.get("/api/editais", params=[("uf", "PR"), ("uf", "SP")]).json()

    assert [e["id_publico"] for e in por_linha["editais"]] == ["pncp~redes-sp/2026"]
    assert [e["id_publico"] for e in por_uf["editais"]] == ["pncp~perdas-pr/2026"]
    assert len(por_duas_ufs["editais"]) == 3


@pytest.mark.parametrize(
    ("params", "campo"),
    [({"linha": "inexistente"}, "linha"), ([("uf", "BA")], "uf")],
)
def test_linha_ou_uf_fora_dos_criterios_422_da_validacao(
    cliente: TestClient, params: Any, campo: str
) -> None:
    # Enum vindo do filtro.toml: o FastAPI valida e responde no formato padrão.
    resposta = cliente.get("/api/editais", params=params)

    assert resposta.status_code == 422
    [erro] = resposta.json()["detail"]
    assert erro["loc"][:2] == ["query", campo]
    assert erro["type"] == "enum"


def test_openapi_traz_linhas_e_ufs_dos_criterios(cliente: TestClient) -> None:
    esquemas = cliente.app.openapi()["components"]["schemas"]  # type: ignore[attr-defined]

    assert esquemas["Linha"]["enum"] == ["perdas", "redes"]
    assert esquemas["UF"]["enum"] == ["SP", "PR"]


def test_detalhe_com_barra_no_id(cliente: TestClient, sessao: Session) -> None:
    numero = "00394460000141-1-000123/2026"
    registrar(sessao, [_registro(numero, "Leitura de hidrômetros e vazamento")])

    resposta = cliente.get(f"/api/editais/pncp~{numero}")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert (corpo["fonte"], corpo["id_publico"]) == ("pncp", f"pncp~{numero}")
    assert corpo["link_origem"]  # o da fonte ou, na falta, o do PNCP
    assert set(corpo["linhas"]) == {"perdas"}
    assert corpo["orgao_cnpj"]
    assert corpo["situacao_nome"]
    assert corpo["situacao_normalizada"] == "aberta"
    assert corpo["data_publicacao"]
    assert "data_abertura_proposta" in corpo


def test_detalhe_inexistente_404(cliente: TestClient) -> None:
    resposta = cliente.get("/api/editais/nao/existe/2026")

    assert resposta.status_code == 404
    assert resposta.json() == {"detail": "edital não encontrado"}


def test_valor_sai_como_string_decimal(cliente: TestClient, sessao: Session) -> None:
    registrar(
        sessao,
        [
            _registro("com-valor/2026", "Conserto de vazamento", valor=1369661.52),
            _registro("sem-valor/2026", "Conserto de vazamento", valor=None),
        ],
    )

    editais = {e["id_publico"]: e for e in cliente.get("/api/editais").json()["editais"]}

    assert editais["pncp~com-valor/2026"]["valor_estimado"] == "1369661.52"
    assert editais["pncp~sem-valor/2026"]["valor_estimado"] is None


def _gravar_analise(
    sessao: Session, id_na_fonte: str, pagina: int = 4, segundo_documento: bool = False
) -> None:
    from radar.analise import AnaliseEdital, DocumentoEdital, carregar_config_ia

    modelo = carregar_config_ia(RAIZ / "config" / "ia.toml").modelo_padrao
    item = {
        "campo": "capital_social_minimo",
        "situacao": "atende",
        "exigido": "R$ 250.000,00",
        "perfil": "R$ 1.000.000,00",
        "motivo": "perfil ≥ exigido",
        "trecho": "capital social mínimo de R$ 250.000,00",
        "pagina": pagina,
    }
    sessao.add_all(
        [
            AnaliseEdital(
                fonte="pncp",
                id_na_fonte=id_na_fonte,
                modelo=modelo,
                versao_prompt="abc123def456",
                hash_documentos="h",
                veredito="apto",
                incompleta=False,
                exigencias={},
                comparacao={"veredito": "apto", "itens": [item]},
                trechos=1,
                trechos_invalidos=0,
                achados_descartados=0,
                truncado=False,
                tokens_entrada=10,
                tokens_saida=5,
                custo_usd=0,
                criada_em=datetime(2026, 11, 27, 12, tzinfo=UTC),
            ),
            DocumentoEdital(
                fonte="pncp",
                id_na_fonte=id_na_fonte,
                sequencial=1,
                titulo="edital.pdf",
                tipo_nome="Edital",
                url="https://pncp.gov.br/pncp-api/v1/orgaos/1/compras/2026/298/arquivos/1",
                sha256="s",
                paginas=12,
                pagina_inicial=1,
                status="extraido",
                baixado_em=datetime(2026, 11, 27, 12, tzinfo=UTC),
            ),
        ]
    )
    if segundo_documento:
        sessao.add(
            DocumentoEdital(
                fonte="pncp",
                id_na_fonte=id_na_fonte,
                sequencial=2,
                titulo="termo de referencia.pdf",
                tipo_nome="Termo de Referência",
                url="https://pncp.gov.br/pncp-api/v1/orgaos/1/compras/2026/298/arquivos/2",
                sha256="t",
                paginas=5,
                pagina_inicial=13,
                status="extraido",
                baixado_em=datetime(2026, 11, 27, 12, tzinfo=UTC),
            )
        )
    sessao.flush()


def test_analise_404_sem_analise(cliente: TestClient) -> None:
    assert cliente.get("/api/analises/pncp~78200482000110-1-000298/2026").status_code == 404
    assert cliente.get("/api/analises/sem-fonte").status_code == 404


def test_analise_com_barra_no_id(cliente: TestClient, sessao: Session) -> None:
    _gravar_analise(sessao, "78200482000110-1-000298/2026")

    resposta = cliente.get("/api/analises/pncp~78200482000110-1-000298/2026")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["veredito"] == "apto"
    assert corpo["id_publico"] == "pncp~78200482000110-1-000298/2026"
    assert corpo["criada_em"] == "2026-11-27T09:00:00-03:00"
    assert "segundo o que foi extraído" in corpo["aviso"].lower()
    assert corpo["itens"][0]["pagina"] == 4
    assert corpo["itens"][0]["documento"] == "edital.pdf"
    assert corpo["documentos"] == [
        {
            "titulo": "edital.pdf",
            "url": "https://pncp.gov.br/pncp-api/v1/orgaos/1/compras/2026/298/arquivos/1",
            "paginas": 12,
            "status": "extraido",
        }
    ]
    codificado = cliente.get("/api/analises/pncp~78200482000110-1-000298%2F2026")
    assert codificado.status_code == 200


def test_analise_sem_config_ia_da_503_e_o_resto_funciona(
    sessao: Session, config_filtro: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RADAR_CONFIG_IA", str(tmp_path / "nao-existe.toml"))
    fabrica = cast(Callable[[], Session], lambda: _SessaoEmprestada(sessao))
    cliente = TestClient(criar_app(fabrica, config_filtro, relogio=lambda: AGORA))

    assert cliente.get("/api/analises/pncp~1-1-1/2026").status_code == 503
    assert cliente.get("/api/status").status_code == 200


def test_analise_devolve_documento_e_pagina_dentro_do_pdf(
    cliente: TestClient, sessao: Session
) -> None:
    # Página 15 do texto juntado = página 3 do 2º documento (que começa na 13).
    _gravar_analise(sessao, "78200482000110-1-000298/2026", pagina=15, segundo_documento=True)

    [item] = cliente.get("/api/analises/pncp~78200482000110-1-000298/2026").json()["itens"]

    assert item["documento"] == "termo de referencia.pdf"
    assert item["pagina"] == 3


@pytest.mark.parametrize(
    "conteudo",
    [
        "isto nao e toml = = =",
        'regiao = "us-east-2"\n',
        'regiao = "sa-east-1"\nmodelo_padrao = "x"',
        'regiao = "us-east-2"\nteto_mensal_usd = "dez"\n',
    ],
)
def test_analise_com_config_ia_invalida_da_503_e_o_resto_funciona(
    sessao: Session,
    config_filtro: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    conteudo: str,
) -> None:
    arquivo = tmp_path / "ia.toml"
    arquivo.write_text(conteudo, encoding="utf-8")
    monkeypatch.setenv("RADAR_CONFIG_IA", str(arquivo))
    fabrica = cast(Callable[[], Session], lambda: _SessaoEmprestada(sessao))
    cliente = TestClient(criar_app(fabrica, config_filtro, relogio=lambda: AGORA))

    assert cliente.get("/api/analises/pncp~1-1-1/2026").status_code == 503
    assert cliente.get("/api/status").status_code == 200


def test_analise_com_teto_nao_decimal_da_503_e_o_resto_funciona(
    sessao: Session, config_filtro: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ia = (RAIZ / "config" / "ia.toml").read_text(encoding="utf-8")
    assert "teto_mensal_usd = 2.0" in ia
    arquivo = tmp_path / "ia.toml"
    arquivo.write_text(ia.replace("teto_mensal_usd = 2.0", 'teto_mensal_usd = "dez"'), "utf-8")
    monkeypatch.setenv("RADAR_CONFIG_IA", str(arquivo))
    fabrica = cast(Callable[[], Session], lambda: _SessaoEmprestada(sessao))
    cliente = TestClient(criar_app(fabrica, config_filtro, relogio=lambda: AGORA))

    assert cliente.get("/api/analises/pncp~1-1-1/2026").status_code == 503
    assert cliente.get("/api/status").status_code == 200


def _gravar_resultado(sessao: Session, id_na_fonte: str, desconto: str | None = "0.2") -> None:
    from decimal import Decimal

    from radar.editais import ResultadoBusca

    sessao.add(
        ResultadoBusca(
            fonte="pncp",
            id_na_fonte=id_na_fonte,
            buscado_em=AGORA,
            itens=1,
            situacao="homologada",
            desconto=None if desconto is None else Decimal(desconto),
            valor_estimado_considerado=Decimal("100"),
            valor_homologado_considerado=Decimal("80"),
            vencedor_ni="111",
            vencedor_nome="Acme Ltda",
            vencedor_porte="ME",
        )
    )
    sessao.flush()


def test_historico_do_orgao(cliente: TestClient, sessao: Session) -> None:
    registrar(
        sessao,
        [
            _registro("alvo/2026", "Conserto de vazamento", encerramento="2026-12-11T10:00:00"),
            _registro("velho/2025", "Troca de hidrometro", encerramento="2026-09-10T10:00:00"),
        ],
    )
    _gravar_resultado(sessao, "velho/2025")

    resposta = cliente.get("/api/historicos/pncp~alvo/2026")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["contagens"] == {"homologada": 1}
    assert corpo["total"] == 1
    assert corpo["limite_inexequivel"] == "0.75"
    [passada] = corpo["ultimas"]
    assert passada["id_publico"] == "pncp~velho/2025"
    assert passada["data"] == "2026-09-10T10:00:00-03:00"
    assert passada["desconto"] == "0.2"
    assert passada["valor_estimado"] == "100"
    assert passada["vencedor_nome"] == "Acme Ltda"
    assert passada["origem"] == "PNCP"
    assert corpo["vencedores"][0]["nome"] == "Acme Ltda"
    assert corpo["vencedores"][0]["vitorias"] == 1
    assert corpo["reguas"][0]["poucos_casos"] is True
    codificado = cliente.get("/api/historicos/pncp~alvo%2F2026")
    assert codificado.json() == corpo


def test_historico_da_compesa_vem_do_tcepe(cliente: TestClient, sessao: Session) -> None:
    from radar.editais import ResultadoExterno
    from radar.editais.modelos import Edital

    compesa = "09769035000164"
    sessao.add(
        Edital(
            fonte="compesa",
            id_na_fonte="2026LC213CEL-1",
            objeto="Implantacao de rede de agua",
            orgao_cnpj=compesa,
            orgao_nome="COMPESA",
            uf="PE",
            modalidade_nome="Licitação",
            situacao_original="Em andamento",
            situacao_normalizada="aberta",
            valor_estimado=Decimal("3000000"),
            data_encerramento_proposta=datetime(2026, 12, 11, 10, 0),
            data_atualizacao=datetime(2026, 11, 26, 10, 0),
            bruto={"numero": "LC 213/2026 CEL-1"},
        )
    )
    sessao.add(
        ResultadoExterno(
            fonte="tce_pe",
            id_na_fonte="60120073",
            orgao_cnpj=compesa,
            orgao_nome="COMPESA",
            uf="PE",
            objeto="EXECUÇÃO DAS OBRAS E SERVIÇOS DA ADUTORA DO AGRESTE",
            numero="68/2026",
            linhas=["redes"],
            data=datetime(2026, 4, 15),
            data_resultado=datetime(2026, 7, 1, tzinfo=ZoneInfo("America/Sao_Paulo")),
            situacao="homologada",
            valor_estimado=Decimal("21883600.48"),
            valor_homologado=Decimal("20345351.99"),
            desconto=Decimal("0.0703"),
            vencedor_ni="10514594000102",
            vencedor_nome="EMCOSA EMPRESA DE CONSTRUCOES E SANEAMENTO LTDA",
        )
    )
    sessao.flush()

    corpo = cliente.get("/api/historicos/compesa~2026LC213CEL-1").json()

    assert (corpo["total"], corpo["contagens"]) == (1, {"homologada": 1})
    [passada] = corpo["ultimas"]
    assert passada["origem"] == "TCE-PE"
    assert passada["id_publico"] == "tce_pe~60120073"
    assert passada["data"] == "2026-04-15T00:00:00-03:00"
    assert passada["desconto"] == "0.0703"
    assert corpo["vencedores"][0]["nome"].startswith("EMCOSA")
    assert corpo["limite_inexequivel"] == "0.70"
    # Régua redes × PE × acima de 5 mi? O edital vale 3 mi: faixa 1–5 mi, sem o caso de 21 mi;
    # com poucos casos vem a régua só da linha, que tem o caso do TCE-PE.
    assert [r["casos"] for r in corpo["reguas"]] == [0, 1]


def test_historico_vazio_devolve_200(cliente: TestClient, sessao: Session) -> None:
    registrar(sessao, [_registro("alvo/2026", "Conserto de vazamento")])

    resposta = cliente.get("/api/historicos/pncp~alvo/2026")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert (corpo["total"], corpo["contagens"], corpo["ultimas"], corpo["vencedores"]) == (
        0,
        {},
        [],
        [],
    )
    assert corpo["reguas"][0]["casos"] == 0


def test_historico_404_para_edital_inexistente(cliente: TestClient) -> None:
    assert cliente.get("/api/historicos/pncp~nao/existe").status_code == 404
    assert cliente.get("/api/historicos/sem-fonte").status_code == 404


def _gravar_preco(sessao: Session, chave: str, preco: str, uf: str = "PE", item: str = "1") -> None:
    sessao.add(
        PrecoItem(
            origem="tce_pe",
            chave_origem=f"{chave}:{item}",
            uf=uf,
            orgao_nome="Compesa",
            processo="802/1/2026",  # rótulo repetido: o processo é o prefixo da chave
            descricao="HIDROMETRO FORNECIMENTO E INSTALACAO",
            unidade="UN",
            preco_com_bdi=Decimal(preco),
            preco_contratado_com_bdi=Decimal(preco) * Decimal("0.8"),
            data_base=date(2025, 6, 1),
            servico="hidrometro",
            unidade_canonica="un",
            fator_unidade=Decimal(1),
        )
    )
    sessao.flush()


def test_precos_do_edital(cliente: TestClient, sessao: Session) -> None:
    registrar(sessao, [_registro("alvo/2026", "Troca de hidrometro")])
    for i, preco in enumerate(["100", "200", "300"]):
        _gravar_preco(sessao, f"h{i}", preco)
    # Segundo item do 1º processo: conta como item, não como processo (mediana dele: 120).
    _gravar_preco(sessao, "h0", "140", item="2")

    resposta = cliente.get("/api/precos/pncp~alvo/2026")

    assert resposta.status_code == 200
    assert resposta.json() == {
        "uf": "SP",
        "referencias": [
            {
                "servico": "hidrometro",
                "rotulo": "Hidrômetro, fornecimento e instalação",
                "linha": "perdas",
                "material": None,
                "dn": None,
                "escopo": None,
                "faixa": None,
                "unidade": "un",
                "abrangencia": "outras_ufs",
                "ufs": ["PE"],
                "origens": ["tce_pe"],
                "processos_estimado": 3,
                "itens_estimado": 4,
                "mediana_estimado": "200.00",
                "processos_contratado": 3,
                "itens_contratado": 4,
                "mediana_contratado": "160.00",
                "mediana_estimado_contratados": "200.00",
                "poucos_casos": False,
                "data_base_inicio": "2025-06-01",
                "data_base_fim": "2025-06-01",
            }
        ],
    }
    assert cliente.get("/api/precos/pncp~alvo%2F2026").json() == resposta.json()


def test_precos_vazio_sem_linha_com_servico(cliente: TestClient, sessao: Session) -> None:
    registrar(sessao, [_registro("alvo/2026", "Pavimentacao de vias")])
    _gravar_preco(sessao, "h0", "100")

    resposta = cliente.get("/api/precos/pncp~alvo/2026")

    assert resposta.status_code == 200
    assert resposta.json() == {"uf": "SP", "referencias": []}


def test_precos_vazio_sem_dados(cliente: TestClient, sessao: Session) -> None:
    registrar(sessao, [_registro("alvo/2026", "Troca de hidrometro")])

    resposta = cliente.get("/api/precos/pncp~alvo/2026")

    assert resposta.status_code == 200
    assert resposta.json()["referencias"] == []


def test_precos_404_para_edital_inexistente(cliente: TestClient) -> None:
    assert cliente.get("/api/precos/pncp~nao/existe").status_code == 404
    assert cliente.get("/api/precos/sem-fonte").status_code == 404
