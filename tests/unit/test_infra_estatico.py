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
    # Sem maximum_concurrency na radar-editais: com ele, os pollers ociosos não são
    # otimizados e o custo de ReceiveMessage do SQS sobe; o limite fica só na radar-janelas.
    assert "maximum_concurrency" not in fila
    assert len(re.findall(r'resource\s+"aws_cloudwatch_metric_alarm"', fila)) == 3


def _analise_tf() -> str:
    return (RAIZ / "infra" / "prod" / "analise.tf").read_text(encoding="utf-8")


def test_bedrock_so_foundation_model_in_region() -> None:
    analise = _analise_tf()
    assert "bedrock:InvokeModel" in analise
    assert "arn:aws:bedrock:${var.regiao}::foundation-model/" in analise
    for proibido in ("inference-profile", "bedrock:*", '"us.', '"global.', '"eu.', '"apac.'):
        assert proibido not in analise, f"analise.tf contém {proibido}"
    for arquivo in (RAIZ / "infra").rglob("*.tf"):
        if ".terraform" in arquivo.parts or arquivo.name == "analise.tf":
            continue
        assert "bedrock:" not in arquivo.read_text(encoding="utf-8"), f"bedrock em {arquivo}"


def test_modelos_ia_recusa_perfil() -> None:
    variaveis = re.sub(r"\s+", " ", (RAIZ / "infra" / "prod" / "variables.tf").read_text("utf-8"))
    bloco = variaveis[variaveis.index('variable "modelos_ia"') :]
    assert "validation" in bloco
    assert r'regex("^(us|eu|apac|global)\\.", m)' in bloco
    assert '"amazon.nova-lite-v1:0"' in bloco


def test_bucket_documentos_privado_e_com_expiracao() -> None:
    analise = re.sub(r"\s+", " ", _analise_tf())
    assert 'bucket = "radar-documentos-${data.aws_caller_identity.atual.account_id}"' in analise
    for chave in ("block_public_acls", "block_public_policy", "ignore_public_acls"):
        assert f"{chave} = true" in analise
    assert "restrict_public_buckets = true" in analise
    assert 'sse_algorithm = "AES256"' in analise
    assert "days = 180" in analise


def test_analisar_usa_familia_do_coletor() -> None:
    analise = re.sub(r"\s+", " ", _analise_tf())
    assert 'Command = ["analisar", "--fontes", "pncp,sanepar,compesa"]' in analise
    assert "TaskDefinition = local.familia_coletor" in analise
    assert "arn:aws:scheduler:::aws-sdk:ecs:runTask" in analise
    assert 'schedule_expression = "cron(0 9 * * ? *)"' in analise
    assert 'schedule_expression_timezone = "America/Sao_Paulo"' in analise
    assert "TaskRoleArn = aws_iam_role.analisador_task.arn" in analise
    coletor = (RAIZ / "infra" / "prod" / "coletor.tf").read_text(encoding="utf-8")
    assert "aws_iam_role.analisador_task.arn" in coletor, "Scheduler sem PassRole da role nova"


def test_analise_sem_alarme_novo() -> None:
    assert "aws_cloudwatch_metric_alarm" not in _analise_tf()


def test_api_lambda_le_config_ia() -> None:
    api = (RAIZ / "infra" / "prod" / "api.tf").read_text(encoding="utf-8")
    assert 'RADAR_CONFIG_IA     = "/var/task/config/ia.toml"' in api


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
    # Confiança do Scheduler: aws:SourceAccount e aws:SourceArn da agenda do planejador.
    assert "data.aws_iam_policy_document.scheduler_assume_planejador.json" in janelas
    assert '"aws:SourceAccount"' in janelas and '"aws:SourceArn"' in janelas
    # O aws:SourceArn do Scheduler é o do grupo de agendas, não o da agenda.
    assert ':schedule-group/default"' in janelas
    assert 'nome_agenda_planejador = "radar-planejar-diario"' in janelas
    # Invocação assíncrona do planejador sem retry da Lambda (o retry é o do Scheduler).
    assert 'resource "aws_lambda_function_event_invoke_config" "planejador"' in janelas
    assert re.search(r"maximum_retry_attempts\s+= 0", janelas)
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
    assert 'command     = ["atualizar", "--fontes", "sanepar,compesa,dae_jundiai"]' in coletor
    # O agendamento sobrescreve o command: só retratos, sem o PNCP (que vai pelas janelas).
    assert 'command = ["atualizar", "--fontes", "sanepar,compesa,dae_jundiai"]' in coletor
    assert "pncp" not in "".join(re.findall(r"command\s*=\s*\[[^\]]*\]", coletor))
    assert '{ name = "RADAR_FILA_JANELAS_URL", value = aws_sqs_queue.janelas.url }' in coletor
    assert "aws_sqs_queue.janelas.arn" in _tf("fila.tf")  # o relay do coletor pode enviar


def test_resultados_tem_agenda_diaria_0500_antes_da_coleta() -> None:
    coletor = _tf("coletor.tf")
    bloco = re.sub(r"\s+", " ", coletor)
    assert 'name = "radar-resultados-diario"' in bloco
    # 05:00, antes da coleta das 06:00: as duas não disputam o pncp.gov.br.
    assert 'schedule_expression = "cron(0 5 * * ? *)"' in bloco
    assert 'schedule_expression_timezone = "America/Sao_Paulo"' in bloco
    assert 'command = ["resultados", "atualizar"]' in coletor
    assert "pncp" not in "".join(re.findall(r"command\s*=\s*\[[^\]]*\]", coletor))


def test_tcepe_tem_agenda_semanal_domingo_0400() -> None:
    coletor = _tf("coletor.tf")
    bloco = re.sub(r"\s+", " ", coletor)
    assert 'resource "aws_scheduler_schedule" "tcepe_semanal"' in bloco
    assert 'name = "radar-tcepe-semanal"' in bloco
    assert 'schedule_expression = "cron(0 4 ? * SUN *)"' in bloco
    assert 'command = ["tcepe", "atualizar"]' in coletor
    # mesmo alvo, role e rede do resultados_diario
    trecho = bloco[bloco.index('"tcepe_semanal"') :]
    assert "role_arn = aws_iam_role.scheduler_coletor.arn" in trecho
    assert "task_definition_arn = local.arn_familia_coletor" in trecho
    assert "security_groups = [aws_security_group.coletor.id]" in trecho


def test_coletor_sai_pela_porta_do_portal_da_compesa() -> None:
    # Sem esta regra o security group descarta a saída para portalscl.compesa.com.br:8743.
    regra = re.sub(r"\s+", " ", _tf("coletor.tf"))
    assert 'resource "aws_vpc_security_group_egress_rule" "coletor_compesa"' in regra
    assert "from_port = 8743 to_port = 8743" in regra


def test_deploy_atualiza_as_lambdas_novas() -> None:
    deploy = (RAIZ / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    for funcao in ("radar-coletora", "radar-planejador"):
        assert f"update-function-code --function-name {funcao}" in deploy
        assert f"wait function-updated-v2 --function-name {funcao}" in deploy
    oidc = _tf("github_oidc.tf")
    assert "aws_lambda_function.coletora.arn" in oidc
    assert "aws_lambda_function.planejador.arn" in oidc


def test_cli_imprime_a_linha_que_o_filtro_do_alarme_conta() -> None:
    """O filtro `radar-coletor-execucoes` conta linhas `resultado:`; a CLI precisa imprimi-las."""
    tf = (RAIZ / "infra" / "prod" / "coletor.tf").read_text(encoding="utf-8")
    bloco = re.search(
        r'resource "aws_cloudwatch_log_metric_filter" "coletor_execucoes" \{(.*?)\n\}', tf, re.S
    )
    assert bloco, "filtro radar-coletor-execucoes nao encontrado em coletor.tf"
    achado = re.search(r'pattern\s*=\s*"\\"([^"\\]+)\\""', bloco.group(1))
    assert achado, "padrao do filtro radar-coletor-execucoes nao encontrado em coletor.tf"
    termo = achado.group(1)
    cli = (RAIZ / "src" / "radar" / "cli.py").read_text(encoding="utf-8")
    assert f"{termo} " in cli, f"cli.py nao imprime {termo!r}; o alarme nao-rodou dispararia"


def _todos_tf() -> str:
    return "\n".join(
        arquivo.read_text(encoding="utf-8")
        for arquivo in (RAIZ / "infra").rglob("*.tf")
        if ".terraform" not in arquivo.parts
    )


def test_no_maximo_dez_alarmes() -> None:
    """Plano grátis: 10 alarmes padrão. Conta todos os de infra/ (hoje 9)."""
    alarmes = re.findall(r'resource\s+"aws_cloudwatch_metric_alarm"\s+"(\w+)"', _todos_tf())
    assert len(alarmes) <= 10, alarmes


def test_no_maximo_dez_metricas_customizadas() -> None:
    """Plano grátis: 10 métricas customizadas; cada combinação de dimensões conta uma.

    Os filtros de log do Terraform mais o catálogo do código (hoje 3 + 6 = 9).
    """
    from math import prod

    from radar.comum.metricas import CATALOGO

    filtros = set(re.findall(r'metric_transformation\s*\{\s*name\s*=\s*"(\w+)"', _todos_tf()))
    do_codigo = sum(
        prod(len(v) for v in d.valores_dimensao.values()) if d.dimensoes else 1
        for d in CATALOGO.values()
    )
    assert not filtros & set(CATALOGO), "nome repetido entre filtro e catálogo"
    assert len(filtros) + do_codigo <= 10, (sorted(filtros), do_codigo)


def test_um_dashboard() -> None:
    assert len(re.findall(r'resource\s+"aws_cloudwatch_dashboard"', _todos_tf())) == 1


def test_spans_por_runbook() -> None:
    obs = _tf("observabilidade.tf")
    # aws/spans é reservado: quem cria é o X-Ray; o .tf não pode declará-lo.
    assert 'resource "aws_cloudwatch_log_group" "spans"' not in obs
    assert "update-trace-segment-destination --destination CloudWatchLogs" in obs
    assert "put-retention-policy --log-group-name aws/spans --retention-in-days 7" in obs


def test_sem_application_signals() -> None:
    tudo = _todos_tf()
    for proibido in (
        "application_signals",
        "applicationsignals",
        "CloudWatchAgentServerPolicy",
        "aws-otel-collector",
        "tracing_config",
    ):
        assert proibido not in tudo, proibido


def test_alarmes_de_slo() -> None:
    obs = re.sub(r"[ \t]+", " ", _tf("observabilidade.tf"))
    frescor = re.search(r'"frescor_estourado" \{(.*?)\n\}', obs, re.S)
    erro = re.search(r'"paginas_com_erro" \{(.*?)\n\}', obs, re.S)
    assert frescor and erro
    for trecho in (
        'alarm_name = "radar-frescor-estourado"',
        'metric_name = "FrescorHoras"',
        'statistic = "Maximum"',
        "period = 86400",
        "evaluation_periods = 1",
        "datapoints_to_alarm = 1",
        "threshold = 36",
        'treat_missing_data = "notBreaching"',
    ):
        assert trecho in frescor.group(1), trecho
    assert 'breaching"' not in frescor.group(1).replace('"notBreaching"', "")
    for trecho in (
        'alarm_name = "radar-paginas-com-erro"',
        'metric_name = "PaginaComErro"',
        'statistic = "Average"',
        "period = 86400",
        "threshold = 0.1",
        'treat_missing_data = "notBreaching"',
    ):
        assert trecho in erro.group(1), trecho
    coletor = _tf("coletor.tf")
    for nome in ("frescor_estourado", "paginas_com_erro"):
        assert f"aws_cloudwatch_metric_alarm.{nome}.arn" in coletor


def test_telemetria_ligada_nos_processos() -> None:
    coletor = _tf("coletor.tf")
    assert '{ name = "RADAR_TELEMETRIA", value = "xray" }' in coletor
    assert '{ name = "RADAR_METRICAS", value = "cloudwatch" }' in coletor
    assert '{ name = "RADAR_TRACES_FRACAO", value = "0.25" }' in coletor
    for arquivo in ("fila.tf", "janelas.tf"):
        texto = re.sub(r"[ \t]+", " ", _tf(arquivo))
        assert 'RADAR_TELEMETRIA = "xray"' in texto, arquivo
        assert 'RADAR_METRICAS = "emf"' in texto, arquivo
        assert 'RADAR_TRACES_FRACAO = "0.25"' in texto, arquivo
    obs = _tf("observabilidade.tf")
    for role in ("coletor_task", "processador", "coletora", "planejador", "analisador_task"):
        assert f"aws_iam_role.{role}.name" in obs, role
    assert "AWSXrayWriteOnlyAccess" in obs
    assert '"cloudwatch:namespace"' in obs and '"Radar"' in obs


def test_bucket_documentos_nega_transporte_sem_tls() -> None:
    analise = re.sub(r"\s+", " ", _analise_tf())
    assert 'resource "aws_s3_bucket_policy" "documentos"' in analise
    assert 'effect = "Deny"' in analise
    assert 'variable = "aws:SecureTransport"' in analise
    assert 'values = ["false"]' in analise
    assert 'actions = ["s3:*"]' in analise
    assert "depends_on = [aws_s3_bucket_public_access_block.documentos]" in analise


def test_analisador_ganha_so_a_telemetria_do_coletor() -> None:
    """A role do `radar analisar` (mesma task do coletor) publica traces e métricas Radar."""
    obs = re.sub(r"[ \t]+", " ", _tf("observabilidade.tf"))
    assert "analisador = aws_iam_role.analisador_task.name" in obs
    bloco = re.search(
        r'resource "aws_iam_role_policy" "analisador_metricas" \{(.*?)\n\}', obs, re.S
    )
    assert bloco, "PutMetricData do analisador ausente"
    assert "role = aws_iam_role.analisador_task.id" in bloco.group(1)
    assert "policy = data.aws_iam_policy_document.coletor_metricas.json" in bloco.group(1)
    analise = _analise_tf()
    assert "cloudwatch" not in analise and "xray" not in analise.lower()


def test_xray_pode_escrever_os_spans_nos_logs() -> None:
    """Política de recurso do CloudWatch Logs da doc Enable-TransactionSearch (passo 1)."""
    obs = re.sub(r"\s+", " ", _tf("observabilidade.tf"))
    assert 'resource "aws_cloudwatch_log_resource_policy" "transaction_search"' in obs
    bloco = obs[obs.index('data "aws_iam_policy_document" "transaction_search"') :]
    bloco = bloco[: bloco.index('resource "aws_cloudwatch_log_resource_policy"')]
    for trecho in (
        'actions = ["logs:PutLogEvents"]',
        'identifiers = ["xray.amazonaws.com"]',
        "log-group:aws/spans:*",
        "log-group:/aws/application-signals/data:*",
        'test = "ArnLike"',
        'variable = "aws:SourceArn"',
        '"arn:aws:xray:${var.regiao}:${data.aws_caller_identity.atual.account_id}:*"',
        'test = "StringEquals"',
        'variable = "aws:SourceAccount"',
        "values = [data.aws_caller_identity.atual.account_id]",
    ):
        assert trecho in bloco, trecho


def test_perfil_real_so_do_bucket_privado_e_desligado_por_padrao() -> None:
    analise = re.sub(r"\s+", " ", _analise_tf())
    assert '"s3://${aws_s3_bucket.documentos.bucket}/perfil/perfil.toml"' in analise
    assert ': "/app/config/perfil.toml"' in analise
    variaveis = re.sub(r"\s+", " ", _tf("variables.tf"))
    assert re.search(r'variable "perfil_no_s3" \{[^}]*default = false', variaveis)


def test_auditoria_diaria_com_alarme() -> None:
    coletor = re.sub(r"\s+", " ", _tf("coletor.tf"))
    assert 'name = "radar-auditoria-diaria"' in coletor
    assert 'schedule_expression = "cron(0 11 * * ? *)"' in coletor
    assert 'command = ["auditar", "--endpoint", "atualizacao"]' in coletor
    assert 'pattern = "\\"auditoria_falhou\\""' in coletor
    assert 'alarm_name = "radar-auditoria-falhou"' in coletor


def test_site_e_api_com_cabecalhos_de_seguranca() -> None:
    front = re.sub(r"\s+", " ", _tf("front.tf"))
    # Politica propria (a gerenciada nao tem CSP) ligada no site e na /api.
    assert 'resource "aws_cloudfront_response_headers_policy" "seguranca"' in front
    assert 'data "aws_cloudfront_response_headers_policy"' not in front
    ligacoes = front.count(
        "response_headers_policy_id = aws_cloudfront_response_headers_policy.seguranca.id"
    )
    assert ligacoes == 2
    assert "access_control_max_age_sec = 63072000" in front
    assert "include_subdomains = true" in front
    assert "content_type_options { override = true }" in front
    assert 'frame_option = "DENY"' in front
    assert 'referrer_policy = "strict-origin-when-cross-origin"' in front
    assert "content_security_policy = local.csp_site" in front
    assert 'header = "Permissions-Policy"' in front


def test_csp_so_libera_o_proprio_site_e_o_cognito() -> None:
    front = re.sub(r"\s+", " ", _tf("front.tf"))
    for diretiva in (
        "\"default-src 'self'\"",
        "\"script-src 'self'\"",
        "\"font-src 'self'\"",
        "\"connect-src 'self' https://cognito-idp.${var.regiao}.amazonaws.com\"",
        "\"object-src 'none'\"",
        "\"base-uri 'self'\"",
        "\"frame-ancestors 'none'\"",
    ):
        assert diretiva in front
    # Script nunca inline nem eval; nada de curinga liberando qualquer origem.
    csp = front[front.index("csp_site = join") : front.index("resource", front.index("csp_site"))]
    assert "unsafe-eval" not in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp
    assert " * " not in csp and "'*'" not in csp and 'https:"' not in csp


def test_site_com_http3() -> None:
    assert re.search(r'http_version\s*=\s*"http2and3"', _tf("front.tf"))


def test_imagens_apontam_os_configs_copiados() -> None:
    """O pacote instalado não fica ao lado de config/: cada config lido em produção precisa
    de variável de ambiente nas duas imagens (a 1ª coleta do TCE-PE caiu por isso)."""
    for arquivo, raiz in (("coletor.Dockerfile", "/app"), ("api-lambda.Dockerfile", "/var/task")):
        texto = (RAIZ / "docker" / arquivo).read_text(encoding="utf-8")
        assert f"RADAR_CONFIG_FILTRO={raiz}/config/filtro.toml" in texto
        assert f"RADAR_CONFIG_SERVICOS={raiz}/config/servicos.toml" in texto
