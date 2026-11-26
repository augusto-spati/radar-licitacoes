"""Guardas estaticas da infraestrutura: nada que cobre parado, nenhum segredo."""

import re
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
PROIBIDOS = {
    "aws_nat_gateway",
    "aws_lb",
    "aws_alb",
    "aws_elb",
    "aws_db_instance",
    "aws_rds_cluster",
    "aws_instance",
    "aws_eip",
    "aws_vpc_endpoint",
    "aws_elasticache_cluster",
    "aws_elasticache_replication_group",
    "aws_opensearch_domain",
    "aws_msk_cluster",
    "aws_kms_key",
}
MODULOS_PERMITIDOS: set[str] = set()
PADRAO_RECURSO = re.compile(r'^\s*resource\s+"([a-z0-9_]+)"', re.MULTILINE)

PADRAO_MODULO = re.compile(r'^\s*module\s+"([^"]+)"', re.MULTILINE)


def _tipos_de_recurso() -> dict[str, set[str]]:
    tipos: dict[str, set[str]] = {}
    for arquivo in (RAIZ / "infra").rglob("*.tf"):
        if ".terraform" in arquivo.parts:
            continue
        texto = arquivo.read_text(encoding="utf-8")
        for tipo in PADRAO_RECURSO.findall(texto):
            tipos.setdefault(tipo, set()).add(arquivo.relative_to(RAIZ).as_posix())
    return tipos


def test_ha_configuracao_terraform() -> None:
    assert _tipos_de_recurso(), "nenhum resource encontrado em infra/**/*.tf"


def test_nenhum_recurso_que_cobra_parado() -> None:
    achados = {t: a for t, a in _tipos_de_recurso().items() if t in PROIBIDOS}
    assert not achados, f"recursos proibidos (cobram parados): {achados}"


def test_nenhum_modulo_escondendo_recursos() -> None:
    modulos: set[str] = set()
    for arquivo in (RAIZ / "infra").rglob("*.tf"):
        if ".terraform" in arquivo.parts:
            continue
        modulos.update(PADRAO_MODULO.findall(arquivo.read_text(encoding="utf-8")))
    assert modulos <= MODULOS_PERMITIDOS, f"modulos nao permitidos: {modulos - MODULOS_PERMITIDOS}"


def test_gitignore_cobre_estado_e_segredos() -> None:
    linhas = {
        linha.strip() for linha in (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
    }
    for entrada in ("*.tfstate", "*.tfstate.*", "terraform.tfvars", ".terraform/"):
        assert entrada in linhas, f".gitignore sem {entrada}"


def test_nenhum_estado_ou_tfvars_versionado() -> None:
    saida = subprocess.run(
        ["git", "ls-files"], cwd=RAIZ, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    ruins = [
        f
        for f in saida
        if ".tfstate" in f or f.endswith("terraform.tfvars") or f.endswith(".auto.tfvars")
    ]
    assert not ruins, f"arquivos sensiveis versionados: {ruins}"
