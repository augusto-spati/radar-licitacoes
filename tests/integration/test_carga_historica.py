from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.coleta import ErroPNCP, HistoricoCarga, carregar_historico
from radar.coleta.pncp import Pagina

MES = date(2026, 10, 1)
TERMOS = ["rede de agua"]


class ClienteFalso:
    def __init__(self, falhar_em: tuple[str, int] | None = None) -> None:
        self.chamadas: list[tuple[Any, ...]] = []
        self.falhar_em = falhar_em

    def buscar(
        self,
        endpoint: str,
        ini: date,
        fim: date,
        modalidade: int,
        pagina: int,
        uf: str | None = None,
    ) -> Pagina:
        self.chamadas.append((uf, modalidade, ini, fim, pagina))
        if self.falhar_em == (uf, pagina):
            self.falhar_em = None
            raise ErroPNCP("HTTP 500")
        registros = [
            {"id": f"{uf}-{pagina}-casa", "objetoCompra": "Obra de REDE DE ÁGUA"},
            {"id": f"{uf}-{pagina}-nao", "objetoCompra": "Compra de canetas"},
        ]
        return Pagina(registros=registros, total_paginas=2, numero_pagina=pagina, bruto={})


class Gravador:
    def __init__(self) -> None:
        self.ids: list[str] = []

    def __call__(self, sessao: Session, registros: list[dict[str, Any]]) -> int:
        self.ids += [r["id"] for r in registros]
        return len(registros)


def carregar(sessao: Session, cliente: ClienteFalso, gravador: Gravador, **kw: Any) -> Any:
    return carregar_historico(
        sessao,
        cliente,
        ["PR", "SP"],
        [6],
        [MES],
        TERMOS,
        gravador,
        pausa_s=0,
        **kw,  # type: ignore[arg-type]
    )


def test_carga_grava_so_o_que_casa_e_nao_repete(sessao: Session) -> None:
    cliente, gravador = ClienteFalso(), Gravador()
    resumo = carregar(sessao, cliente, gravador)
    assert (resumo.combinacoes, resumo.concluidas, resumo.falhas) == (2, 2, 0)
    assert (resumo.paginas, resumo.lidos, resumo.gravados) == (4, 8, 4)
    assert sorted(gravador.ids) == ["PR-1-casa", "PR-2-casa", "SP-1-casa", "SP-2-casa"]
    assert cliente.chamadas[0][3] == date(2026, 10, 31)
    cargas = sessao.scalars(select(HistoricoCarga)).all()
    assert {c.status for c in cargas} == {"concluida"}

    cliente2 = ClienteFalso()
    resumo2 = carregar(sessao, cliente2, gravador)
    assert cliente2.chamadas == []
    assert (resumo2.concluidas, resumo2.paginas) == (2, 0)


def test_retoma_na_pagina_da_falha(sessao: Session) -> None:
    cliente, gravador = ClienteFalso(falhar_em=("PR", 2)), Gravador()
    resumo = carregar(sessao, cliente, gravador)
    assert (resumo.concluidas, resumo.falhas) == (1, 1)
    pr = sessao.scalars(select(HistoricoCarga).where(HistoricoCarga.uf == "PR")).one()
    assert (pr.status, pr.proxima_pagina, pr.erro) == ("falha", 2, "HTTP 500")

    cliente2 = ClienteFalso()
    resumo2 = carregar(sessao, cliente2, gravador)
    assert [c[4] for c in cliente2.chamadas] == [2]
    assert (resumo2.concluidas, resumo2.falhas) == (2, 0)
    assert gravador.ids.count("PR-1-casa") == 1


def test_refazer_reabre_o_mes(sessao: Session) -> None:
    gravador = Gravador()
    carregar(sessao, ClienteFalso(), gravador)
    cliente = ClienteFalso()
    carregar(sessao, cliente, gravador, refazer={MES})
    assert len(cliente.chamadas) == 4


def test_erro_do_gravar_desfaz_a_pagina_e_libera_a_trava(sessao: Session) -> None:
    import uuid
    from datetime import timedelta

    import pytest

    from radar.coleta import obter_trava

    def gravar_quebrado(sessao: Session, registros: list[dict[str, Any]]) -> int:
        raise ValueError("gravar quebrou")

    with pytest.raises(ValueError, match="gravar quebrou"):
        carregar_historico(
            sessao,
            ClienteFalso(),
            ["PR"],
            [6],
            [MES],
            TERMOS,
            gravar_quebrado,
            pausa_s=0,  # type: ignore[arg-type]
        )
    carga = sessao.scalars(select(HistoricoCarga)).one()
    assert carga.proxima_pagina == 1
    assert carga.registros_lidos == 0
    assert obter_trava(sessao, "historico", uuid.uuid4(), timedelta(minutes=1))


def test_retoma_combinacao_em_andamento(sessao: Session) -> None:
    gravador = Gravador()
    cliente = ClienteFalso(falhar_em=("PR", 2))
    carregar(sessao, cliente, gravador)
    pr = sessao.scalars(select(HistoricoCarga).where(HistoricoCarga.uf == "PR")).one()
    pr.status = "em_andamento"  # queda simulada: o processo morreu sem marcar falha
    sessao.commit()
    cliente2 = ClienteFalso()
    resumo = carregar(sessao, cliente2, gravador)
    assert [c[4] for c in cliente2.chamadas] == [2]
    assert (resumo.concluidas, resumo.falhas) == (2, 0)


def test_trava_de_outro_dono_impede_a_carga(sessao: Session) -> None:
    import uuid
    from datetime import timedelta

    import pytest

    from radar.coleta import obter_trava

    assert obter_trava(sessao, "historico", uuid.uuid4(), timedelta(minutes=5))
    cliente = ClienteFalso()
    with pytest.raises(RuntimeError):
        carregar(sessao, cliente, Gravador())
    assert cliente.chamadas == []
