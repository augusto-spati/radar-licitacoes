"""Modelo multi-fonte: migração da chave, registro normalizado, reconciliação e filtro."""

import copy
import json
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, inspect, select, text, update
from sqlalchemy.orm import Session

from radar.api.app import criar_app
from radar.editais import (
    Criterios,
    filtrar,
    obter_edital,
    reconciliar_retrato,
    registrar,
    registrar_normalizados,
)
from radar.editais.modelos import Edital

RAIZ = Path(__file__).resolve().parent.parent.parent
AMOSTRA: dict[str, Any] = json.loads(
    (RAIZ / "tests" / "dados" / "registro_pregao.json").read_text(encoding="utf-8")
)
FUSO = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 11, 26, 12, 0, tzinfo=FUSO)
CRITERIOS = Criterios(
    linhas={"perdas": ["vazamento", "hidrometro"]}, ufs=["SP", "PR", "PE"], modalidades=[6]
)


def _normalizado(
    fonte: str = "sanepar",
    id_na_fonte: str = "OE-345/26",
    objeto: str = "Pesquisa de vazamentos não visíveis",
    uf: str = "PR",
    situacao: str = "aberta",
    encerramento: str | None = "2026-12-10T09:00:00",
    anexos: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "fonte": fonte,
        "id_na_fonte": id_na_fonte,
        "objeto": objeto,
        "orgao_nome": "Companhia de Saneamento do Paraná - Sanepar",
        "orgao_cnpj": None,
        "uf": uf,
        "municipio": None,
        "modalidade_nome": "Licitação Eletrônica",
        "situacao_original": "Abertura",
        "situacao_normalizada": situacao,
        "valor_estimado": None,
        "data_publicacao": "2026-11-20T00:00:00",
        "data_abertura_proposta": None,
        "data_encerramento_proposta": encerramento,
        "link_origem": None,
        "anexos": anexos if anexos is not None else [{"nome": "Edital.pdf", "id": "a1"}],
        "bruto": {"linha": "<tr>...</tr>"},
    }


def _pncp(numero: str, objeto: str, uf: str = "SP", modalidade: int = 6) -> dict[str, Any]:
    registro = copy.deepcopy(AMOSTRA)
    registro["numeroControlePNCP"] = numero
    registro["objetoCompra"] = objeto
    registro["unidadeOrgao"]["ufSigla"] = uf
    registro["modalidadeId"] = modalidade
    registro["situacaoCompraId"] = 1
    return registro


def _edital(sessao: Session, fonte: str, id_na_fonte: str) -> Edital:
    sessao.expire_all()
    edital = sessao.get(Edital, (fonte, id_na_fonte))
    assert edital is not None
    return edital


# --- Migração 0003 -------------------------------------------------------------------------


@pytest.fixture
def banco_migracao(engine: Engine) -> Iterator[tuple[AlembicConfig, Engine]]:
    """Banco vazio no mesmo servidor do teste, para subir e descer migrações à mão."""
    nome = f"migracao_{uuid.uuid4().hex[:8]}"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conexao:
        conexao.execute(text(f"CREATE DATABASE {nome}"))
    url = engine.url.set(database=nome)
    config = AlembicConfig(str(RAIZ / "alembic.ini"))
    config.set_main_option("script_location", str(RAIZ / "migrations"))
    config.attributes["url"] = url.render_as_string(hide_password=False)
    alvo = create_engine(url)
    try:
        yield config, alvo
    finally:
        alvo.dispose()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conexao:
            conexao.execute(text(f"DROP DATABASE {nome}"))


_INSERT_0002 = text(
    """
    INSERT INTO edital (numero_controle_pncp, objeto, orgao_cnpj, orgao_nome, uf, municipio,
        modalidade_id, situacao_id, situacao_nome, valor_estimado, data_publicacao,
        data_abertura_proposta, data_encerramento_proposta, data_atualizacao, link_origem, bruto)
    VALUES (:numero, 'Conserto de vazamento', '13127340000120', 'SAAE', 'SP', 'Holambra',
        6, :situacao, :nome, 10, '2026-11-01', NULL, '2026-12-10 10:00', '2026-11-02', NULL,
        CAST(:bruto AS jsonb))
    """
)


def test_migracao_preenche_pncp(banco_migracao: tuple[AlembicConfig, Engine]) -> None:
    config, alvo = banco_migracao
    command.upgrade(config, "0002")
    situacoes = {1: "Divulgada no PNCP", 2: "Revogada", 3: "Anulada", 4: "Suspensa"}
    with alvo.begin() as conexao:
        for situacao, nome in situacoes.items():
            conexao.execute(
                _INSERT_0002,
                {
                    "numero": f"00394460000141-1-00000{situacao}/2026",
                    "situacao": situacao,
                    "nome": nome,
                    "bruto": json.dumps({"modalidadeNome": "Pregão - Eletrônico"}),
                },
            )

    command.upgrade(config, "0003")

    with alvo.connect() as conexao:
        linhas = conexao.execute(
            text(
                "SELECT fonte, id_na_fonte, modalidade_nome, situacao_original,"
                " situacao_normalizada, visto_em, hash_conteudo FROM edital ORDER BY id_na_fonte"
            )
        ).all()
    assert [tuple(linha) for linha in linhas] == [
        ("pncp", "00394460000141-1-000001/2026", "Pregão - Eletrônico", "Divulgada no PNCP",
         "aberta", None, None),
        ("pncp", "00394460000141-1-000002/2026", "Pregão - Eletrônico", "Revogada",
         "encerrada", None, None),
        ("pncp", "00394460000141-1-000003/2026", "Pregão - Eletrônico", "Anulada",
         "encerrada", None, None),
        ("pncp", "00394460000141-1-000004/2026", "Pregão - Eletrônico", "Suspensa",
         "outra", None, None),
    ]  # fmt: skip
    inspetor = inspect(alvo)
    assert inspetor.get_pk_constraint("edital")["constrained_columns"] == ["fonte", "id_na_fonte"]
    colunas = {c["name"]: c for c in inspetor.get_columns("edital")}
    assert "numero_controle_pncp" not in colunas
    assert colunas["modalidade_id"]["nullable"] and colunas["situacao_id"]["nullable"]
    assert not colunas["situacao_normalizada"]["nullable"]
    indices = {i["name"]: i["column_names"] for i in inspetor.get_indexes("edital")}
    assert indices["ix_edital_situacao_encerramento"] == [
        "situacao_normalizada",
        "data_encerramento_proposta",
    ]


def test_downgrade_volta(banco_migracao: tuple[AlembicConfig, Engine]) -> None:
    config, alvo = banco_migracao
    command.upgrade(config, "0003")
    with Session(alvo) as sessao:
        registrar(sessao, [_pncp("00394460000141-1-000123/2026", "Conserto de vazamento")])
        registrar_normalizados(sessao, [_normalizado()])
        sessao.commit()

    command.downgrade(config, "0002")

    with alvo.connect() as conexao:
        linhas = conexao.execute(
            text("SELECT numero_controle_pncp, situacao_nome FROM edital")
        ).all()
    assert [tuple(linha) for linha in linhas] == [
        ("00394460000141-1-000123/2026", "Divulgada no PNCP")
    ]
    inspetor = inspect(alvo)
    assert inspetor.get_pk_constraint("edital")["constrained_columns"] == ["numero_controle_pncp"]
    colunas = {c["name"]: c for c in inspetor.get_columns("edital")}
    assert "fonte" not in colunas and "situacao_normalizada" not in colunas
    assert not colunas["modalidade_id"]["nullable"]
    command.upgrade(config, "head")  # e sobe de novo sem erro


# --- registrar_normalizados ---------------------------------------------------------------


def test_registrar_normalizados_insere_e_hash_igual_ignora(sessao: Session) -> None:
    primeiro = registrar_normalizados(sessao, [_normalizado()])
    segundo = registrar_normalizados(sessao, [_normalizado()])

    assert (primeiro.inseridos, primeiro.atualizados, primeiro.ignorados) == (1, 0, 0)
    assert (segundo.inseridos, segundo.atualizados, segundo.ignorados) == (0, 0, 1)
    edital = _edital(sessao, "sanepar", "OE-345/26")
    assert edital.situacao_normalizada == "aberta"
    assert edital.modalidade_id is None and edital.situacao_id is None
    assert edital.hash_conteudo is not None and len(edital.hash_conteudo) == 64
    assert edital.visto_em is not None


def test_hash_muda_atualiza(sessao: Session) -> None:
    registrar_normalizados(sessao, [_normalizado()])
    hash_antes = _edital(sessao, "sanepar", "OE-345/26").hash_conteudo

    anexo_novo = [{"nome": "Edital.pdf", "id": "a1"}, {"nome": "Retificacao.pdf", "id": "a2"}]
    resultado = registrar_normalizados(sessao, [_normalizado(anexos=anexo_novo)])

    assert (resultado.inseridos, resultado.atualizados, resultado.ignorados) == (0, 1, 0)
    assert _edital(sessao, "sanepar", "OE-345/26").hash_conteudo != hash_antes


def test_visto_em_atualiza_sem_mudar(sessao: Session) -> None:
    registrar_normalizados(sessao, [_normalizado()])
    antigo = datetime(2020, 1, 1, tzinfo=UTC)
    sessao.execute(update(Edital).values(visto_em=antigo))

    resultado = registrar_normalizados(sessao, [_normalizado()])

    assert resultado.ignorados == 1
    visto = _edital(sessao, "sanepar", "OE-345/26").visto_em
    assert visto is not None and visto > antigo


def test_registrar_normalizados_invalido_nao_derruba_lote(sessao: Session) -> None:
    ruim = _normalizado(id_na_fonte="OE-1/26")
    ruim["situacao_normalizada"] = "talvez"
    resultado = registrar_normalizados(sessao, [ruim, _normalizado(fonte="compesa")])

    assert (resultado.invalidos, resultado.inseridos) == (1, 1)


def test_pncp_preenche_campos_normalizados(sessao: Session) -> None:
    registrar(sessao, [_pncp("00394460000141-1-000123/2026", "Conserto de vazamento")])

    edital = _edital(sessao, "pncp", "00394460000141-1-000123/2026")
    assert edital.modalidade_nome == "Pregão - Eletrônico"
    assert edital.situacao_original == "Divulgada no PNCP"
    assert edital.situacao_normalizada == "aberta"
    assert edital.hash_conteudo is not None


# --- reconciliar_retrato ------------------------------------------------------------------


def test_reconciliar_so_fonte_e_abertos(sessao: Session) -> None:
    registrar_normalizados(
        sessao,
        [
            _normalizado(id_na_fonte="OE-1/26"),
            _normalizado(id_na_fonte="OE-2/26", situacao="em_julgamento"),
            _normalizado(id_na_fonte="OE-3/26"),  # continua na lista
            _normalizado(id_na_fonte="OE-4/26", situacao="outra"),
            _normalizado(fonte="compesa", id_na_fonte="OE-1/26"),  # outra fonte
        ],
    )
    registrar(sessao, [_pncp("00394460000141-1-000123/2026", "Conserto de vazamento")])

    marcados = reconciliar_retrato(sessao, "sanepar", {"OE-3/26"}, AGORA)

    assert marcados == 2
    for chave in ("OE-1/26", "OE-2/26"):
        edital = _edital(sessao, "sanepar", chave)
        assert (edital.situacao_normalizada, edital.situacao_original) == (
            "encerrada",
            "saiu da lista",
        )
    assert _edital(sessao, "sanepar", "OE-3/26").situacao_normalizada == "aberta"
    assert _edital(sessao, "sanepar", "OE-4/26").situacao_normalizada == "outra"
    assert _edital(sessao, "compesa", "OE-1/26").situacao_normalizada == "aberta"
    assert _edital(sessao, "pncp", "00394460000141-1-000123/2026").situacao_normalizada == (
        "aberta"
    )


def test_edital_que_volta_a_lista_reabre(sessao: Session) -> None:
    registrar_normalizados(sessao, [_normalizado()])
    reconciliar_retrato(sessao, "sanepar", set(), AGORA)

    resultado = registrar_normalizados(sessao, [_normalizado()])

    assert resultado.atualizados == 1
    assert _edital(sessao, "sanepar", "OE-345/26").situacao_normalizada == "aberta"


def test_reconciliar_recusa_pncp(sessao: Session) -> None:
    with pytest.raises(ValueError, match="pncp"):
        reconciliar_retrato(sessao, "pncp", set(), AGORA)


# --- filtro -------------------------------------------------------------------------------


def test_filtrar_tres_fontes_e_modalidade_so_pncp(sessao: Session) -> None:
    registrar(
        sessao,
        [
            _pncp("00394460000141-1-000001/2026", "Conserto de vazamento"),
            _pncp("00394460000141-1-000002/2026", "Conserto de vazamento", modalidade=4),
        ],
    )
    registrar_normalizados(
        sessao,
        [
            _normalizado(fonte="sanepar", id_na_fonte="OE-345/26"),
            _normalizado(fonte="compesa", id_na_fonte="2026LC213CEL-1", uf="PE"),
            _normalizado(fonte="compesa", id_na_fonte="2026LC1-1", uf="BA"),  # UF fora
        ],
    )

    resultado = filtrar(sessao, CRITERIOS, AGORA)

    assert sorted(r.id_publico for r in resultado) == [
        "compesa~2026LC213CEL-1",
        "pncp~00394460000141-1-000001/2026",
        "sanepar~OE-345/26",
    ]


def test_prazo_nulo_nao_e_aberto(sessao: Session) -> None:
    registrar_normalizados(
        sessao,
        [
            _normalizado(id_na_fonte="OE-1/26", encerramento=None),
            _normalizado(id_na_fonte="OE-2/26", situacao="em_julgamento"),
        ],
    )

    assert filtrar(sessao, CRITERIOS, AGORA) == []
    assert len(filtrar(sessao, CRITERIOS, AGORA, somente_abertos=False)) == 2


def test_obter_edital_por_fonte_e_id(sessao: Session) -> None:
    registrar_normalizados(sessao, [_normalizado()])

    edital = obter_edital(sessao, "sanepar", "OE-345/26", CRITERIOS)

    assert edital is not None
    assert (edital.fonte, edital.id_na_fonte, edital.id_publico) == (
        "sanepar",
        "OE-345/26",
        "sanepar~OE-345/26",
    )
    assert edital.linhas == ["perdas"]
    assert obter_edital(sessao, "compesa", "OE-345/26", CRITERIOS) is None


# --- API ----------------------------------------------------------------------------------


class _SessaoEmprestada:
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
def cliente(sessao: Session, tmp_path: Path) -> TestClient:
    caminho = tmp_path / "filtro.toml"
    caminho.write_text(
        'ufs = ["SP", "PR", "PE"]\nmodalidades = [6]\n\n[linhas]\nperdas = ["vazamento"]\n',
        encoding="utf-8",
    )
    fabrica = cast(Callable[[], Session], lambda: _SessaoEmprestada(sessao))
    return TestClient(criar_app(fabrica, caminho, relogio=lambda: AGORA))


def test_api_detalhe_por_id_publico(cliente: TestClient, sessao: Session) -> None:
    numero = "00394460000141-1-000123/2026"
    registrar(sessao, [_pncp(numero, "Conserto de vazamento")])
    registrar_normalizados(
        sessao, [_normalizado(), _normalizado(fonte="compesa", id_na_fonte="2026LC213CEL-1")]
    )

    pncp = cliente.get(f"/api/editais/pncp~{numero}")
    pncp_codificado = cliente.get("/api/editais/pncp~00394460000141-1-000123%2F2026")
    sanepar = cliente.get("/api/editais/sanepar~OE-345%2F26")
    compesa = cliente.get("/api/editais/compesa~2026LC213CEL-1")

    assert pncp.status_code == 200
    corpo = pncp.json()
    assert (corpo["fonte"], corpo["id_publico"]) == ("pncp", f"pncp~{numero}")
    assert "numero_controle_pncp" not in corpo
    assert corpo["linhas"] == ["perdas"]
    assert pncp_codificado.json()["id_publico"] == f"pncp~{numero}"
    assert sanepar.status_code == 200
    assert sanepar.json()["id_publico"] == "sanepar~OE-345/26"
    assert sanepar.json()["modalidade_id"] is None
    assert sanepar.json()["link_origem"] == "https://www.sanepar.com.br/licitacao/detalhe/345/26"
    assert compesa.json()["link_origem"] == (
        "https://servicos.compesa.com.br/licitacoes-e-contratos/"
    )


@pytest.mark.parametrize(
    "caminho",
    [
        "/api/editais/00394460000141-1-000123/2026",  # sem fonte
        "/api/editais/sabesp~1",  # fonte desconhecida
        "/api/editais/sanepar~",  # id vazio
        "/api/editais/~OE-345/26",
        "/api/editais/sanepar~OE-999/26",  # formato certo, edital inexistente
    ],
)
def test_api_id_publico_invalido_404(cliente: TestClient, sessao: Session, caminho: str) -> None:
    registrar_normalizados(sessao, [_normalizado()])

    resposta = cliente.get(caminho)

    assert resposta.status_code == 404
    assert resposta.json() == {"detail": "edital não encontrado"}


def test_api_lista_traz_fonte_e_id_publico(cliente: TestClient, sessao: Session) -> None:
    registrar_normalizados(sessao, [_normalizado()])

    [edital] = cliente.get("/api/editais").json()["editais"]

    assert (edital["fonte"], edital["id_publico"]) == ("sanepar", "sanepar~OE-345/26")
    assert edital["modalidade_nome"] == "Licitação Eletrônica"


def test_openapi_fonte_e_enum(cliente: TestClient) -> None:
    esquemas = cliente.app.openapi()["components"]["schemas"]  # type: ignore[attr-defined]

    assert esquemas["Fonte"]["enum"] == ["pncp", "sanepar", "compesa"]
    assert "numero_controle_pncp" not in esquemas["EditalResumoOut"]["properties"]


def test_valor_decimal_no_hash_estavel(sessao: Session) -> None:
    com_valor = _normalizado(fonte="compesa", id_na_fonte="2026LC213CEL-1", uf="PE")
    com_valor["valor_estimado"] = "845000.10"
    registrar_normalizados(sessao, [com_valor])
    igual = copy.deepcopy(com_valor)
    igual["valor_estimado"] = Decimal("845000.10")

    assert registrar_normalizados(sessao, [igual]).ignorados == 1
    assert _edital(sessao, "compesa", "2026LC213CEL-1").valor_estimado == Decimal("845000.10")


def test_chave_composta_na_tabela(sessao: Session) -> None:
    registrar_normalizados(
        sessao, [_normalizado(fonte="sanepar"), _normalizado(fonte="compesa", uf="PE")]
    )

    chaves = sessao.execute(select(Edital.fonte, Edital.id_na_fonte).order_by(Edital.fonte)).all()

    assert [tuple(c) for c in chaves] == [("compesa", "OE-345/26"), ("sanepar", "OE-345/26")]
