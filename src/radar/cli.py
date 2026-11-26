"""Porta de entrada de terminal."""

import typer

from radar import __version__

app = typer.Typer(help="Radar de licitações.", no_args_is_help=True)


@app.command()
def versao() -> None:
    """Mostra a versão instalada."""
    typer.echo(__version__)
