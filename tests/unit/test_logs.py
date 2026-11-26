import json
import logging
from datetime import datetime

import pytest

from radar.comum import configurar_logs


def test_log_e_json_com_campos_extras(capsys: pytest.CaptureFixture[str]) -> None:
    configurar_logs("INFO")
    logging.getLogger("radar.x").info("janela_concluida", extra={"janela_id": 7})

    saida = capsys.readouterr()
    linhas = [linha for linha in (saida.out + saida.err).splitlines() if linha.strip()]
    assert len(linhas) == 1
    registro = json.loads(linhas[0])
    assert registro["evento"] == "janela_concluida"
    assert registro["janela_id"] == 7
    assert registro["nivel"] == "INFO"
    datetime.fromisoformat(registro["momento"])
