from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.analise import (
    PerfilResumo,
    carregar_perfil,
    gravar_perfil_resumo,
    ler_perfil_resumo,
)

RAIZ = Path(__file__).resolve().parents[2]
PERFIL = carregar_perfil(RAIZ / "config" / "perfil.toml")
AGORA = datetime(2026, 11, 28, 12, tzinfo=UTC)


def test_sem_linha_le_none(sessao: Session) -> None:
    assert ler_perfil_resumo(sessao) is None


def test_gravar_cria_e_depois_atualiza_a_mesma_linha(sessao: Session) -> None:
    gravar_perfil_resumo(sessao, PERFIL, AGORA)
    sessao.flush()
    assert ler_perfil_resumo(sessao) is not None
    outro = PERFIL.model_copy(
        update={"exemplo": False, "uf_sede": "SP", "valor_maximo_obra": Decimal("3000000")}
    )
    gravar_perfil_resumo(sessao, outro, AGORA)
    sessao.flush()
    lido = ler_perfil_resumo(sessao)
    assert lido is not None
    assert sessao.scalar(select(func.count()).select_from(PerfilResumo)) == 1
    assert (lido.exemplo, lido.uf_sede, lido.valor_maximo_obra) == (False, "SP", Decimal("3000000"))
    assert lido.atualizado_em == AGORA
