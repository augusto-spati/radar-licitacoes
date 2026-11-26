"""Grava a fixture do front com editais reais do banco (SÓ LEITURA).

Uso: uv run python scripts/exportar_exemplo.py --saida frontend/src/dados/exemplo.json --maximo 40
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from radar.api.exemplo import exportar_exemplo
from radar.comum import criar_engine, fabrica_sessao, obter_config
from radar.editais import carregar_criterios

RAIZ = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument("--saida", type=Path, default=Path("frontend/src/dados/exemplo.json"))
    analisador.add_argument("--maximo", type=int, default=40)
    analisador.add_argument("--config", type=Path, default=RAIZ / "config" / "filtro.toml")
    args = analisador.parse_args()

    criterios = carregar_criterios(args.config)
    agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
    with fabrica_sessao(criar_engine(obter_config()))() as sessao:
        dados = exportar_exemplo(sessao, criterios, agora, args.maximo)
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    texto = json.dumps(dados, indent=2, ensure_ascii=False) + "\n"
    args.saida.write_bytes(texto.encode("utf-8"))
    com_linhas = sum(1 for e in dados["editais"] if e["linhas"])
    print(f"{len(dados['editais'])} editais ({com_linhas} com linhas) em {args.saida}")
