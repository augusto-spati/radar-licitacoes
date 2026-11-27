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
    "aws_ecs_service",
    "aws_eks_cluster",
    "aws_lambda_provisioned_concurrency_config",
    "aws_secretsmanager_secret",
    "aws_route53_zone",
    "aws_wafv2_web_acl",
    "aws_ec2_transit_gateway",
    "aws_apigatewayv2_vpc_link",
}
MODULOS_PERMITIDOS: set[str] = set()
PADRAO_RECURSO = re.compile(r'^\s*resource\s+"?([a-z0-9_]+)"?', re.MULTILINE)

PADRAO_MODULO = re.compile(r'^\s*module\s+"?([^"\s{]+)"?', re.MULTILINE)


def _tipos_de_recurso() -> dict[str, set[str]]:
    tipos: dict[str, set[str]] = {}
    for arquivo in (RAIZ / "infra").rglob("*.tf"):
        if ".terraform" in arquivo.parts:
            continue
        texto = arquivo.read_text(encoding="utf-8")
        for tipo in PADRAO_RECURSO.findall(texto):
            tipos.setdefault(tipo, set()).add(arquivo.relative_to(RAIZ).as_posix())
    return tipos


def test_nenhum_tf_json() -> None:
    achados = [p for p in (RAIZ / "infra").rglob("*.tf.json") if ".terraform" not in p.parts]
    assert not achados, f"*.tf.json escapa das guardas: {achados}"


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


def test_login_fechado_e_sem_diferenciar_maiusculas() -> None:
    auth = re.sub(r"\s+", " ", (RAIZ / "infra" / "prod" / "auth.tf").read_text(encoding="utf-8"))
    assert "allow_admin_create_user_only = true" in auth, "cadastro aberto ligado"
    assert "username_configuration { case_sensitive = false }" in auth, (
        "login diferencia maiusculas"
    )
    assert "lower(e)" in auth and "lower(var.email_remetente)" in auth, "e-mails sem lower()"


def test_fila_tem_dlq_e_alarmes() -> None:
    fila = (RAIZ / "infra" / "prod" / "fila.tf").read_text(encoding="utf-8")
    assert "maxReceiveCount     = 5" in fila
    assert "visibility_timeout_seconds = 720" in fila
    assert 'function_response_types = ["ReportBatchItemFailures"]' in fila
    assert "maximum_concurrency = 2" in fila
    assert len(re.findall(r'resource\s+"aws_cloudwatch_metric_alarm"', fila)) == 3


def _tf(nome: str) -> str:
    return (RAIZ / "infra" / "prod" / nome).read_text(encoding="utf-8")


def test_janelas_tem_dlq_agenda_e_alarmes() -> None:
    janelas = _tf("janelas.tf")
    assert "maxReceiveCount     = 5" in janelas
    assert "visibility_timeout_seconds = 5400" in janelas
    assert "message_retention_seconds  = 345600" in janelas
    assert "message_retention_seconds = 1209600" in janelas
    assert "timeout       = 900" in janelas
    assert "timeout       = 120" in janelas
    assert janelas.count("memory_size   = 256") == 2
    assert "batch_size              = 1" in janelas
    assert 'function_response_types = ["ReportBatchItemFailures"]' in janelas
    assert "maximum_concurrency = 2" in janelas
    assert 'schedule_expression          = "cron(0 6 * * ? *)"' in janelas
    assert 'schedule_expression_timezone = "America/Sao_Paulo"' in janelas
    assert '["radar.processador.lambda_coletora.handler"]' in janelas
    assert '["radar.processador.lambda_planejador.handler"]' in janelas
    # Confiança do Scheduler: a mesma do coletor, com aws:SourceAccount.
    assert "data.aws_iam_policy_document.scheduler_assume.json" in janelas
    assert "aws:SourceAccount" in _tf("coletor.tf")
    assert 'pattern        = "\\"janela_concluida\\""' in janelas
    assert "threshold           = 21600" in janelas
    assert len(re.findall(r'resource\s+"aws_cloudwatch_metric_alarm"', janelas)) == 3
    # Recursos só na região do projeto: nenhum provider ou região fixa noutro lugar.
    assert "provider" not in janelas and "us-east-1" not in janelas


def test_alarmes_novos_publicam_no_topico() -> None:
    coletor = _tf("coletor.tf")
    for nome in ("janelas_dlq_com_mensagens", "janelas_fila_parada", "pncp_nao_concluiu"):
        assert f"aws_cloudwatch_metric_alarm.{nome}.arn" in coletor


def test_alarmes_cabem_no_gratis() -> None:
    total = sum(
        len(re.findall(r'resource\s+"aws_cloudwatch_metric_alarm"', arquivo.read_text("utf-8")))
        for arquivo in (RAIZ / "infra" / "prod").glob("*.tf")
    )
    assert total <= 10


def test_coletor_so_retratos() -> None:
    coletor = _tf("coletor.tf")
    assert 'command     = ["atualizar", "--fontes", "sanepar,compesa"]' in coletor
    assert '{ name = "RADAR_FILA_JANELAS_URL", value = aws_sqs_queue.janelas.url }' in coletor
    assert "aws_sqs_queue.janelas.arn" in _tf("fila.tf")  # o relay do coletor pode enviar


def test_deploy_atualiza_as_lambdas_novas() -> None:
    deploy = (RAIZ / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    for funcao in ("radar-coletora", "radar-planejador"):
        assert f"update-function-code --function-name {funcao}" in deploy
        assert f"wait function-updated-v2 --function-name {funcao}" in deploy
    oidc = _tf("github_oidc.tf")
    assert "aws_lambda_function.coletora.arn" in oidc
    assert "aws_lambda_function.planejador.arn" in oidc
