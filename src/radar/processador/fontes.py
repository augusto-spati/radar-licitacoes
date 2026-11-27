"""As fontes lidas por retrato, por nome (o nome é o `coleta_retrato.fonte`)."""

from radar.coleta import FonteCompesa, FonteRetrato, FonteSanepar


def fontes_retrato() -> dict[str, FonteRetrato]:
    """Fontes lidas por retrato, na ordem em que rodam."""
    fontes: list[FonteRetrato] = [FonteSanepar(), FonteCompesa()]
    return {fonte.nome: fonte for fonte in fontes}
