from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.coleta import PaginaColetada
from radar.coleta.dae_jundiai import URL_LISTA, FonteDaeJundiai
from radar.editais import registrar_normalizados
from radar.editais.modelos import Edital

DADOS = Path(__file__).resolve().parent.parent / "dados" / "fontes"


def test_pregoes_reais_gravam_com_a_fonte_nova_e_sem_mudanca_na_segunda(sessao: Session) -> None:
    lista = (DADOS / "dae-jundiai-lista-2026-p3.html").read_text(encoding="utf-8")
    paginas = [PaginaColetada(f"{URL_LISTA}#exercicio=2026&pagina=3", "html", lista)]
    fonte = FonteDaeJundiai(relogio=lambda: datetime(2026, 11, 27, 15, tzinfo=UTC))

    primeiro = registrar_normalizados(sessao, fonte.extrair(paginas))
    segundo = registrar_normalizados(sessao, fonte.extrair(paginas))

    assert (primeiro.inseridos, primeiro.invalidos) == (10, 0)
    assert (segundo.atualizados, segundo.ignorados) == (0, 10)
    edital = sessao.scalars(
        select(Edital).where(Edital.fonte == "dae_jundiai", Edital.id_na_fonte == "PE-2026/00063")
    ).one()
    assert edital.situacao_normalizada == "aberta"
    assert edital.data_encerramento_proposta == datetime(2026, 12, 18, 9, 0)
    assert edital.uf == "SP" and edital.orgao_cnpj == "03582243000173"
