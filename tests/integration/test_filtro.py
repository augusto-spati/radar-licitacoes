import copy
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from radar.analise.modelos import AnaliseEdital
from radar.editais import (
    Criterios,
    Recorte,
    carregar_criterios,
    filtrar,
    listar_abertos,
    obter_edital,
    registrar,
)

RAIZ = Path(__file__).resolve().parent.parent.parent
AMOSTRA: dict[str, Any] = json.loads(
    (RAIZ / "tests" / "dados" / "registro_pregao.json").read_text(encoding="utf-8")
)
FUSO = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 11, 26, 12, 0, tzinfo=FUSO)

CRITERIOS = Criterios(
    linhas={
        "perdas": ["hidrometro", "vazamento"],
        "comercial": ["leitura de hidrometro", "leitura"],
        "redes": ["rede de agua"],
    },
    ufs=["SP", "PR"],
    modalidades=[6],
)


def _registro(
    numero: str,
    objeto: str,
    uf: str = "SP",
    encerramento: str | None = "2026-12-11T10:00:00",
    situacao_id: int = 1,
) -> dict[str, Any]:
    registro = copy.deepcopy(AMOSTRA)
    registro["numeroControlePNCP"] = numero
    registro["objetoCompra"] = objeto
    registro["unidadeOrgao"]["ufSigla"] = uf
    registro["dataEncerramentoProposta"] = encerramento
    registro["situacaoCompraId"] = situacao_id
    return registro


def test_termo_sem_acento_acha_texto_com_acento(sessao: Session) -> None:
    registrar(
        sessao,
        [_registro("1/2026", "Prestação de serviços de LEITURA DE HIDRÔMETROS")],
    )

    resultado = filtrar(sessao, CRITERIOS, AGORA)

    assert [r.id_na_fonte for r in resultado] == ["1/2026"]
    assert set(resultado[0].linhas) == {"comercial", "perdas"}


def test_filtra_uf(sessao: Session) -> None:
    objeto = "Manutenção de rede de água"
    registrar(sessao, [_registro("sp/2026", objeto, "SP"), _registro("ba/2026", objeto, "BA")])

    resultado = filtrar(sessao, CRITERIOS, AGORA)

    assert [r.id_na_fonte for r in resultado] == ["sp/2026"]


def test_somente_abertos(sessao: Session) -> None:
    registrar(
        sessao,
        [_registro("velho/2026", "Conserto de vazamento", encerramento="2026-11-25T10:00:00")],
    )

    assert filtrar(sessao, CRITERIOS, AGORA) == []
    assert len(filtrar(sessao, CRITERIOS, AGORA, somente_abertos=False)) == 1


def test_nao_casa_termo_solto(sessao: Session) -> None:
    registrar(sessao, [_registro("onibus/2026", "Contratação de locação de ônibus")])

    assert filtrar(sessao, CRITERIOS, AGORA) == []


def test_carregar_criterios() -> None:
    criterios = carregar_criterios(RAIZ / "config" / "filtro.toml")

    assert len(criterios.linhas) == 4
    assert len(criterios.ufs) == 4


def test_listar_abertos_ignora_texto_e_respeita_uf_prazo_e_limite(sessao: Session) -> None:
    registrar(
        sessao,
        [
            _registro("tarde/2026", "Compra de canetas", encerramento="2026-12-20T10:00:00"),
            _registro("cedo/2026", "Conserto de vazamento", encerramento="2026-12-01T10:00:00"),
            _registro("velho/2026", "Compra de papel", encerramento="2026-11-20T10:00:00"),
            _registro("ba/2026", "Compra de lapis", "BA", encerramento="2026-12-02T10:00:00"),
        ],
    )

    resultado = listar_abertos(sessao, CRITERIOS, AGORA, limite=10)

    assert [r.id_na_fonte for r in resultado] == ["cedo/2026", "tarde/2026"]
    assert resultado[0].linhas == ["perdas"]
    assert resultado[1].linhas == []
    assert len(listar_abertos(sessao, CRITERIOS, AGORA, limite=1)) == 1


def _analise(
    sessao: Session,
    id_na_fonte: str,
    veredito: str,
    criada_em: datetime,
    modelo: str = "m",
    itens: tuple[tuple[str, str], ...] = (),
    id_: int | None = None,
) -> None:
    analise = AnaliseEdital(
        fonte="pncp",
        id_na_fonte=id_na_fonte,
        modelo=modelo,
        versao_prompt=f"v{criada_em.isoformat()}{veredito}",
        hash_documentos="h",
        veredito=veredito,
        incompleta=False,
        exigencias={},
        comparacao={"itens": [{"campo": c, "situacao": s} for c, s in itens]},
        trechos=0,
        trechos_invalidos=0,
        achados_descartados=0,
        truncado=False,
        tokens_entrada=0,
        tokens_saida=0,
        custo_usd=Decimal("0"),
        criada_em=criada_em,
    )
    if id_ is not None:
        analise.id = id_
    sessao.add(analise)
    sessao.flush()


def _ids(sessao: Session, recorte: Recorte, **kw: Any) -> list[str]:
    return [r.id_na_fonte for r in filtrar(sessao, CRITERIOS, AGORA, recorte=recorte, **kw)]


def _fixar_visto(sessao: Session, id_na_fonte: str, quando: datetime) -> None:
    sessao.execute(
        text("UPDATE edital SET primeiro_visto_em = :q WHERE id_na_fonte = :i"),
        {"q": quando, "i": id_na_fonte},
    )


def _dia(dia: int, mes: int = 11, hora: int = 12) -> datetime:
    return datetime(2026, mes, dia, hora, 0, tzinfo=FUSO)


def test_resumo_traz_veredito_da_analise_mais_nova_do_modelo(sessao: Session) -> None:
    registrar(sessao, [_registro("a/2026", "Conserto de vazamento")])
    _analise(sessao, "a/2026", "apto", _dia(20))
    _analise(sessao, "a/2026", "inapto", _dia(25))
    _analise(sessao, "a/2026", "verificar", _dia(26), modelo="outro")

    assert filtrar(sessao, CRITERIOS, AGORA, modelo_analise="m")[0].veredito == "inapto"


def test_empate_de_criada_em_fica_com_o_maior_id(sessao: Session) -> None:
    registrar(sessao, [_registro("a/2026", "Conserto de vazamento")])
    _analise(sessao, "a/2026", "apto", _dia(25), id_=900001)
    _analise(sessao, "a/2026", "inapto", _dia(25), id_=900002)

    assert filtrar(sessao, CRITERIOS, AGORA, modelo_analise="m")[0].veredito == "inapto"


def test_motivo_verificar_e_o_campo_do_primeiro_item_verificar(sessao: Session) -> None:
    registrar(sessao, [_registro("a/2026", "Conserto de vazamento")])
    _analise(
        sessao,
        "a/2026",
        "verificar",
        _dia(25),
        itens=(
            ("capital_social_minimo", "atende"),
            ("atestados", "verificar"),
            ("documentos", "verificar"),
        ),
    )

    resumo = filtrar(sessao, CRITERIOS, AGORA, modelo_analise="m")[0]

    assert resumo.motivo_verificar == "atestados"


def test_sem_analise_ou_sem_modelo_veredito_nulo(sessao: Session) -> None:
    registrar(sessao, [_registro("a/2026", "Conserto de vazamento")])

    resumo = filtrar(sessao, CRITERIOS, AGORA, modelo_analise="m")[0]
    assert (resumo.veredito, resumo.motivo_verificar) == (None, None)
    _analise(sessao, "a/2026", "apto", _dia(25))
    resumo = filtrar(sessao, CRITERIOS, AGORA)[0]
    assert (resumo.veredito, resumo.motivo_verificar) == (None, None)


def test_encerra_em_dias_conta_dias_de_calendario_em_brasilia(sessao: Session) -> None:
    registrar(
        sessao,
        [
            _registro("dia20/2026", "Conserto de vazamento", encerramento="2026-12-16T23:59:00"),
            _registro("dia21/2026", "Conserto de vazamento", encerramento="2026-12-17T00:00:00"),
        ],
    )

    assert _ids(sessao, Recorte(encerra_em_dias=20)) == ["dia20/2026"]


def test_encerra_em_dias_zero_e_so_hoje(sessao: Session) -> None:
    registrar(
        sessao,
        [
            _registro("hoje/2026", "Conserto de vazamento", encerramento="2026-11-26T18:00:00"),
            _registro("amanha/2026", "Conserto de vazamento", encerramento="2026-11-27T08:00:00"),
        ],
    )

    assert _ids(sessao, Recorte(encerra_em_dias=0)) == ["hoje/2026"]


def test_novos_sao_os_vistos_ha_ate_3_dias(sessao: Session) -> None:
    registrar(
        sessao,
        [
            _registro("entra/2026", "Conserto de vazamento"),
            _registro("fora/2026", "Conserto de vazamento"),
        ],
    )
    _fixar_visto(sessao, "entra/2026", AGORA - timedelta(days=3))
    _fixar_visto(sessao, "fora/2026", AGORA - timedelta(days=3, minutes=1))

    assert _ids(sessao, Recorte(novos=True)) == ["entra/2026"]


def test_vereditos_filtra_e_sem_analise_casa_nulo(sessao: Session) -> None:
    registrar(
        sessao,
        [
            _registro("apto/2026", "Conserto de vazamento", encerramento="2026-12-01T10:00:00"),
            _registro("inapto/2026", "Conserto de vazamento", encerramento="2026-12-02T10:00:00"),
            _registro("nada/2026", "Conserto de vazamento", encerramento="2026-12-03T10:00:00"),
        ],
    )
    _analise(sessao, "apto/2026", "apto", _dia(25))
    _analise(sessao, "inapto/2026", "inapto", _dia(25))

    recorte = Recorte(vereditos=frozenset({"apto", "sem_analise"}))
    assert _ids(sessao, recorte, modelo_analise="m") == ["apto/2026", "nada/2026"]
    assert _ids(sessao, Recorte(vereditos=frozenset({"inapto"})), modelo_analise="m") == [
        "inapto/2026"
    ]


def test_limite_nulo_traz_todos(sessao: Session) -> None:
    registrar(sessao, [_registro(f"{i}/2026", "Conserto de vazamento") for i in range(3)])

    assert len(filtrar(sessao, CRITERIOS, AGORA, limite=None)) == 3
    assert len(filtrar(sessao, CRITERIOS, AGORA, limite=2)) == 2


def test_obter_edital_traz_veredito_e_primeiro_visto_em(sessao: Session) -> None:
    registrar(sessao, [_registro("a/2026", "Conserto de vazamento")])
    _analise(sessao, "a/2026", "apto", _dia(25))
    visto = AGORA - timedelta(days=1)
    _fixar_visto(sessao, "a/2026", visto)

    detalhe = obter_edital(sessao, "pncp", "a/2026", CRITERIOS, modelo_analise="m")

    assert detalhe is not None
    assert detalhe.veredito == "apto"
    assert detalhe.primeiro_visto_em == visto
    assert listar_abertos(sessao, CRITERIOS, AGORA, 10, modelo_analise="m")[0].veredito == "apto"
