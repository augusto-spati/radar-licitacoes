from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

RAIZ = Path(__file__).resolve().parents[2]


def test_cadeia_linear_com_as_planilhas_depois_do_resultado_externo() -> None:
    config = AlembicConfig(str(RAIZ / "alembic.ini"))
    config.set_main_option("script_location", str(RAIZ / "migrations"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0014"]
    assert script.get_revision("0014").down_revision == "0013"
    assert script.get_revision("0013").down_revision == "0012"
    assert script.get_revision("0012").down_revision == "0011"
    assert script.get_revision("0011").down_revision == "0010"
    assert script.get_revision("0010").down_revision == "0009"
    assert script.get_revision("0009").down_revision == "0008"
    assert script.get_revision("0008").down_revision == "0007"
