import pytest

from radar.comum import obter_config


def test_config_le_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_DATABASE_URL", "x")
    assert obter_config().database_url == "x"
