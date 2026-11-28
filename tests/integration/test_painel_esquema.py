"""Esquema 0015: primeiro_visto_em só na inserção, índice da última análise, perfil_resumo."""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from radar.analise import PerfilResumo
from radar.editais import reconciliar_retrato, registrar, registrar_normalizados
from radar.editais.modelos import Edital

FUSO = ZoneInfo("America/Sao_Paulo")

_INSERIR = text(
    """
    INSERT INTO edital (fonte, id_na_fonte, objeto, orgao_nome, uf, modalidade_nome,
        situacao_original, situacao_normalizada, data_publicacao, data_atualizacao, visto_em,
        bruto)
    VALUES (:fonte, :id, 'Conserto de vazamento', 'SAAE', 'SP', 'Pregão', 'Aberta', 'aberta',
        CAST(:publicacao AS timestamp), '2026-11-01', CAST(:visto AS timestamptz),
        CAST('{}' AS jsonb))
    """
)


def _normalizado(id_na_fonte: str = "OE-1/26", objeto: str = "objeto") -> dict[str, Any]:
    return {
        "fonte": "sanepar",
        "id_na_fonte": id_na_fonte,
        "objeto": objeto,
        "orgao_nome": "Sanepar",
        "orgao_cnpj": None,
        "uf": "PR",
        "municipio": None,
        "modalidade_nome": "Licitação Eletrônica",
        "situacao_original": "Abertura",
        "situacao_normalizada": "aberta",
        "valor_estimado": None,
        "data_publicacao": "2026-11-20T00:00:00",
        "data_abertura_proposta": None,
        "data_encerramento_proposta": "2026-12-10T09:00:00",
        "link_origem": None,
        "anexos": [],
        "bruto": {"linha": "x"},
    }


def test_backfill_primeiro_visto_em_nunca_parece_novo(
    banco_migracao: tuple[AlembicConfig, Engine],
) -> None:
    config, alvo = banco_migracao
    command.upgrade(config, "0014")
    with alvo.begin() as conexao:
        conexao.execute(
            _INSERIR,
            {"fonte": "pncp", "id": "recente", "publicacao": "2026-11-27 10:00", "visto": None},
        )
        conexao.execute(
            _INSERIR,
            {"fonte": "compesa", "id": "sem_publicacao", "publicacao": None, "visto": "now"},
        )
        conexao.execute(
            _INSERIR,
            {"fonte": "pncp", "id": "antigo", "publicacao": "2025-01-10 09:00", "visto": None},
        )

    command.upgrade(config, "0015")

    with alvo.connect() as conexao:
        agora_db = conexao.execute(text("SELECT now()")).scalar_one()
        linhas = conexao.execute(text("SELECT id_na_fonte, primeiro_visto_em FROM edital"))
        valores = {id_na_fonte: visto for id_na_fonte, visto in linhas}
    assert valores["recente"] <= agora_db - timedelta(days=30)
    assert valores["sem_publicacao"] <= agora_db - timedelta(days=30)
    assert valores["antigo"] == datetime(2025, 1, 10, 9, 0, tzinfo=FUSO)
    colunas = {c["name"]: c for c in inspect(alvo).get_columns("edital")}
    assert not colunas["primeiro_visto_em"]["nullable"]


def test_downgrade_0015_volta_e_sobe_de_novo(
    banco_migracao: tuple[AlembicConfig, Engine],
) -> None:
    config, alvo = banco_migracao
    command.upgrade(config, "head")
    command.downgrade(config, "0014")

    inspetor = inspect(alvo)
    assert "primeiro_visto_em" not in {c["name"] for c in inspetor.get_columns("edital")}
    assert "perfil_resumo" not in inspetor.get_table_names()
    assert "ix_analise_edital_ultima" not in {
        i["name"] for i in inspetor.get_indexes("analise_edital")
    }
    command.upgrade(config, "head")


def _primeiro_visto_em(sessao: Session, id_na_fonte: str = "OE-1/26") -> datetime:
    sessao.expire_all()
    edital = sessao.get(Edital, ("sanepar", id_na_fonte))
    assert edital is not None
    return edital.primeiro_visto_em


def test_edital_novo_ganha_primeiro_visto_em(sessao: Session) -> None:
    registrar_normalizados(sessao, [_normalizado()])

    assert _primeiro_visto_em(sessao) is not None


def _fixar(sessao: Session) -> datetime:
    fixo = datetime(2026, 10, 1, 12, 0, tzinfo=FUSO)
    sessao.execute(update(Edital).values(primeiro_visto_em=fixo))
    return fixo


def test_upsert_com_conteudo_novo_nao_muda_primeiro_visto_em(sessao: Session) -> None:
    registrar_normalizados(sessao, [_normalizado()])
    fixo = _fixar(sessao)

    registrar_normalizados(sessao, [_normalizado(objeto="objeto novo")])

    assert _primeiro_visto_em(sessao) == fixo
    edital = sessao.get(Edital, ("sanepar", "OE-1/26"))
    assert edital is not None and edital.objeto == "objeto novo"


def test_versao_mais_nova_do_pncp_nao_muda_primeiro_visto_em(sessao: Session) -> None:
    import copy
    import json
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent
    registro = json.loads((raiz / "dados" / "registro_pregao.json").read_text(encoding="utf-8"))
    numero = registro["numeroControlePNCP"]
    registrar(sessao, [registro])
    fixo = _fixar(sessao)
    novo = copy.deepcopy(registro)
    novo["dataAtualizacaoGlobal"] = "2099-01-01T00:00:00"
    novo["objetoCompra"] = "objeto novo"

    resultado = registrar(sessao, [novo])

    assert resultado.atualizados == 1
    sessao.expire_all()
    edital = sessao.get(Edital, ("pncp", numero))
    assert edital is not None
    assert edital.primeiro_visto_em == fixo and edital.objeto == "objeto novo"


def test_reconciliar_retrato_nao_muda_primeiro_visto_em(sessao: Session) -> None:
    registrar_normalizados(sessao, [_normalizado()])
    fixo = _fixar(sessao)

    reconciliar_retrato(sessao, "sanepar", set(), datetime(2026, 11, 26, 12, 0, tzinfo=FUSO))

    assert _primeiro_visto_em(sessao) == fixo


def test_perfil_resumo_so_tem_uma_linha(sessao: Session) -> None:
    sessao.add(PerfilResumo(exemplo=True, atualizado_em=datetime(2026, 11, 28, tzinfo=FUSO)))
    sessao.flush()
    assert sessao.scalars(select(PerfilResumo)).one().id == 1

    with pytest.raises(IntegrityError):
        sessao.execute(
            text("INSERT INTO perfil_resumo (id, exemplo, atualizado_em) VALUES (2, true, now())")
        )
