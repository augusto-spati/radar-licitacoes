from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from radar.api.app import criar_app
from radar.comum import criar_engine, obter_config

RAIZ = Path(__file__).resolve().parent.parent.parent


def test_config_le_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_DATABASE_URL", "x")
    assert obter_config().database_url == "x"


def test_config_filtro_padrao_nao_depende_do_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RADAR_CONFIG_FILTRO", raising=False)
    monkeypatch.chdir(tmp_path)

    caminho = obter_config().config_filtro

    assert caminho.is_absolute()
    assert caminho == RAIZ / "config" / "filtro.toml"
    assert caminho.is_file()


def test_config_filtro_le_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RADAR_CONFIG_FILTRO", str(tmp_path / "outro.toml"))
    assert obter_config().config_filtro == tmp_path / "outro.toml"


def test_criar_app_sem_config_explicita_funciona_fora_da_raiz(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RADAR_CONFIG_FILTRO", raising=False)
    monkeypatch.chdir(tmp_path)

    def sem_banco() -> Session:
        raise RuntimeError("não acessa o banco")

    app = criar_app(sem_banco)

    assert "/api/editais" in app.openapi()["paths"]


def test_engine_com_pool_padrao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RADAR_DB_NULLPOOL", raising=False)
    engine = criar_engine(obter_config())
    assert not isinstance(engine.pool, NullPool)


def test_engine_com_nullpool_para_serverless(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_DB_NULLPOOL", "true")
    engine = criar_engine(obter_config())
    assert isinstance(engine.pool, NullPool)


_URL_JANELAS = "https://sqs.us-east-2.amazonaws.com/1/radar-janelas"


def test_config_le_urls_da_fila_de_janelas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_FILA_JANELAS_URL", _URL_JANELAS)
    monkeypatch.setenv("RADAR_FILA_JANELAS_FALHAS_URL", _URL_JANELAS + "-falhas")

    config = obter_config()

    assert config.fila_janelas_url == _URL_JANELAS
    assert config.fila_janelas_falhas_url == _URL_JANELAS + "-falhas"
