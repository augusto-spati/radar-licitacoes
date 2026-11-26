import copy
import json
from collections.abc import Callable
from datetime import UTC, datetime
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

    assert [e["numero_controle_pncp"] for e in abertos["editais"]] == ["cedo/2026", "tarde/2026"]
    assert [e["numero_controle_pncp"] for e in todos["editais"]] == [
        "velho/2026",
        "cedo/2026",
        "tarde/2026",
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
    fora_dos_criterios = cliente.get("/api/editais", params=[("uf", "BA")]).json()

    assert [e["numero_controle_pncp"] for e in por_linha["editais"]] == ["redes-sp/2026"]
    assert [e["numero_controle_pncp"] for e in por_uf["editais"]] == ["perdas-pr/2026"]
    assert len(por_duas_ufs["editais"]) == 3
    assert fora_dos_criterios["editais"] == []


def test_linha_desconhecida_422(cliente: TestClient) -> None:
    assert cliente.get("/api/editais", params={"linha": "inexistente"}).status_code == 422


def test_detalhe_com_barra_no_id(cliente: TestClient, sessao: Session) -> None:
    numero = "00394460000141-1-000123/2026"
    registrar(sessao, [_registro(numero, "Leitura de hidrômetros e vazamento")])

    resposta = cliente.get(f"/api/editais/{numero}")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["numero_controle_pncp"] == numero
    assert set(corpo["linhas"]) == {"perdas"}
    assert corpo["orgao_cnpj"]
    assert corpo["situacao_nome"]
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

    editais = {e["numero_controle_pncp"]: e for e in cliente.get("/api/editais").json()["editais"]}

    assert editais["com-valor/2026"]["valor_estimado"] == "1369661.52"
    assert editais["sem-valor/2026"]["valor_estimado"] is None
