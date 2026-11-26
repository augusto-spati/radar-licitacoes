from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.coleta import PaginaColetada
from radar.coleta.sanepar import FonteSanepar
from radar.editais import registrar_normalizados
from radar.editais.modelos import Edital

DADOS = Path(__file__).resolve().parent.parent / "dados" / "fontes"
BASE = "https://www.sanepar.com.br"


def _paginas(detalhe: str) -> list[PaginaColetada]:
    lista = (DADOS / "lista-oe-abertura-p1.html").read_text(encoding="utf-8")
    return [
        PaginaColetada(f"{BASE}/licitacoes?Tipo=OE&Situacao=Abertura&page=0", "html", lista),
        PaginaColetada(f"{BASE}/licitacao/detalhe/352/26", "html", detalhe),
    ]


def test_anexo_novo_muda_o_hash_e_atualiza(sessao: Session) -> None:
    original = (DADOS / "detalhe-345-26-vazamentos.html").read_text(encoding="utf-8")
    novo = (
        '<a href="/licitacao/arquivo/229999" class="sli-occurrence-link">ERRATA</a>\n'
        '<a href="/licitacao/arquivo/221508"'
    )
    com_anexo = original.replace('<a href="/licitacao/arquivo/221508"', novo, 1)
    assert com_anexo != original
    fonte = FonteSanepar()

    primeiro = registrar_normalizados(sessao, fonte.extrair(_paginas(original)))
    igual = registrar_normalizados(sessao, fonte.extrair(_paginas(original)))
    mudou = registrar_normalizados(sessao, fonte.extrair(_paginas(com_anexo)))

    assert (primeiro.inseridos, primeiro.invalidos) == (20, 0)
    assert (igual.atualizados, igual.ignorados) == (0, 20)
    assert (mudou.atualizados, mudou.ignorados) == (1, 19)
    edital = sessao.scalars(
        select(Edital).where(Edital.fonte == "sanepar", Edital.id_na_fonte == "OE-352/26")
    ).one()
    assert "229999" in str(edital.bruto)
