"""The cloud foundation is configuration, and configuration can be read.

These tests read the Terraform, Docker and workflow files as text. They apply nothing
and contact nothing. They exist so that the properties the design depends on (nothing
public, no secret value, a dispatcher that cannot read source credentials) fail a test
when someone edits them away, instead of failing in production.
"""

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
BOOTSTRAP = REPO / "infra" / "bootstrap"
PROD = REPO / "infra" / "prod"
WORKFLOWS = REPO / ".github" / "workflows"

IDENTITIES = {
    "ms-scheduler",
    "ms-dispatch",
    "ms-steam-players",
    "ms-wikimedia",
    "ms-catalogue",
    "ms-warehouse",
    "ms-features",
    "ms-parity",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def terraform(root: Path) -> str:
    return "\n".join(read(path) for path in sorted(root.glob("*.tf")))


def code(text: str) -> str:
    """The configuration without its comments, so prose cannot satisfy or trip a check."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def every_infra_file() -> list[Path]:
    return sorted(p for p in (REPO / "infra").rglob("*") if p.is_file())


def grants(text: str, name: str) -> list[list[str]]:
    """The rows of a `name = [ [...], ... ]` grant table in the Terraform."""
    block = re.search(rf"{name}\s*=\s*\[(.*?)\n  \]", text, re.S)
    assert block, name
    return [re.findall(r'"([^"]*)"', row) for row in re.findall(r"\[([^\[\]]+)\]", block.group(1))]


# --- nothing real, nothing secret --------------------------------------------------------


def test_no_state_plan_or_variable_file_is_committed() -> None:
    names = [path.name for path in every_infra_file()]
    assert not [n for n in names if n.endswith((".tfstate", ".tfvars", ".tfplan", "tfplan"))]
    assert not [p for p in every_infra_file() if ".terraform" in p.parts]
    ignored = read(REPO / ".gitignore")
    for pattern in ("*.tfstate", "*.tfvars", ".terraform/", ".env"):
        assert pattern in ignored


def test_terraform_holds_no_secret_value_and_creates_no_secret_version() -> None:
    for root in (BOOTSTRAP, PROD):
        text = code(terraform(root))
        assert "google_secret_manager_secret_version" not in text
        assert "secret_data" not in text and "private_key" not in text
        assert "google_service_account_key" not in text  # no long-lived key, ever
        assert not re.search(r"\b[0-9A-F]{32}\b", text)  # nothing Steam-key shaped
        # Anything assigned to a credential-like name is the NAME of a secret container.
        assigned = re.findall(
            r"(?i)\w*(?:password|secret|api_key|token)\w*\s*=\s*\"([^\"]*)\"", text
        )
        assert set(assigned) <= {"steam-web-api-key", "igdb-client-id", "igdb-client-secret"}
    secrets = code(read(PROD / "secrets.tf"))
    assert set(re.findall(r'"((?:steam|igdb)-[a-z-]+)"\s*=', secrets)) == {
        "steam-web-api-key",
        "igdb-client-id",
        "igdb-client-secret",
    }
    assert "igdb-access-token" not in secrets and "oauth" not in secrets.lower()


def test_project_billing_and_github_identifiers_are_variables_not_values() -> None:
    for path in every_infra_file():
        if path.suffix not in (".tf", ".example"):
            continue
        text = read(path)
        assert not re.search(r"\b[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}\b", text.replace("000000", ""))
        assert not re.search(r"project(_id)?\s*=\s*\"[a-z][a-z0-9-]{4,}\"", code(text)), path.name
    variables = read(BOOTSTRAP / "variables.tf")
    for name in ("project_id", "billing_account_id", "github_owner_id", "github_repository_id"):
        declaration = re.search(rf'variable "{name}" \{{(.*?)\n\}}', variables, re.S)
        assert declaration and "default" not in declaration.group(1), name
    assert 'variable "project_id"' in read(PROD / "variables.tf")
    for example in (BOOTSTRAP / "bootstrap.tfvars.example", PROD / "prod.tfvars.example"):
        values = re.findall(r'^\w+\s*=\s*"([^"]*)"', read(example), re.M)
        assert values and all("REPLACE" in value for value in values)


def test_everything_is_confined_to_us_central1() -> None:
    for root in (BOOTSTRAP, PROD):
        variables = read(root / "variables.tf")
        assert 'default     = "us-central1"' in variables
        assert 'var.region == "us-central1"' in variables  # any other region is refused
    prod = code(terraform(PROD))
    assert "location = var.region" in prod and "location    = var.region" in prod
    assert not re.search(r'location\s*=\s*"US"', prod)  # never the US multi-region
    assert not re.search(r'location\s*=\s*"[a-z]', prod)  # no literal location at all


# --- nothing public ----------------------------------------------------------------------


def test_nothing_is_public() -> None:
    for root in (BOOTSTRAP, PROD):
        text = code(terraform(root))
        assert "allUsers" not in text and "allAuthenticatedUsers" not in text
        assert "google_cloud_run_v2_service" not in text  # jobs only: nothing has a URL
        assert "google_storage_bucket_acl" not in text and "predefined_acl" not in text
        assert "website" not in text and "cors" not in text
    storage = code(read(PROD / "storage.tf"))
    assert 'public_access_prevention    = "enforced"' in storage
    assert "uniform_bucket_level_access = true" in storage
    assert storage.count('resource "google_storage_bucket" "data"') == 1  # every bucket shares it


def test_the_buckets_are_the_designed_ones_and_none_can_block_deletion() -> None:
    storage = read(PROD / "storage.tf")
    names = re.findall(r"^    (\w+) = \{ soft_delete_seconds", storage, re.M)
    assert names == ["landing", "raw", "state", "ops", "tokens"]  # tfstate is made by hand
    text = code(storage)
    assert "retention_policy" not in text and "is_locked" not in text
    assert "force_destroy               = false" in text
    assert 'storage_class = "NEARLINE"' in text
    assert "state_bucket_name" in read(BOOTSTRAP / "variables.tf")
    assert 'backend "gcs"' in read(BOOTSTRAP / "versions.tf")
    assert 'backend "gcs"' in read(PROD / "versions.tf")


# --- identities and least privilege ------------------------------------------------------


def test_each_job_has_its_own_identity_and_nobody_is_owner_or_editor() -> None:
    bootstrap = read(BOOTSTRAP / "main.tf")
    declared = set(re.findall(r'^    "(ms-[a-z-]+)"\s+=', bootstrap, re.M))
    assert declared == IDENTITIES
    assert 'account_id   = "ms-deploy"' in bootstrap
    for root in (BOOTSTRAP, PROD):
        text = code(terraform(root))
        assert "roles/owner" not in text and "roles/editor" not in text
        assert "roles/iam.serviceAccountTokenCreator" not in text
        assert "roles/iam.serviceAccountKeyAdmin" not in text
    assert "google_project_iam" not in code(terraform(PROD))  # prod never edits project IAM


def test_the_dispatcher_cannot_read_any_source_secret() -> None:
    secrets = read(PROD / "secrets.tf")
    readers = {
        name: set(re.findall(r'"(ms-[a-z-]+)"', identities))
        for name, identities in re.findall(r'"([a-z-]+)"\s*=\s*\[([^\]]*)\]', secrets)
    }
    assert readers == {
        "steam-web-api-key": {"ms-steam-players", "ms-catalogue"},
        "igdb-client-id": {"ms-catalogue"},
        "igdb-client-secret": {"ms-catalogue"},
    }
    allowed = set().union(*readers.values())
    assert allowed == {"ms-steam-players", "ms-catalogue"}
    for identity in ("ms-dispatch", "ms-wikimedia", "ms-warehouse", "ms-features", "ms-parity"):
        assert identity not in allowed, identity
    # Secret access is granted in exactly one place, and never project-wide.
    for root in (BOOTSTRAP, PROD):
        text = code(terraform(root))
        assert text.count("roles/secretmanager.secretAccessor") == (1 if root is PROD else 0)
        assert "roles/secretmanager.admin" not in text
    # The jobs agree with the grants: only those two identities are handed a secret.
    jobs = read(PROD / "jobs.tf")
    with_secrets = {
        name
        for name, body in re.findall(r'"(ms-[a-z-]+)" = \{(.*?)\n    \}', jobs, re.S)
        if not re.search(r"secrets\s*=\s*\{\}", body)
    }
    assert with_secrets == {"ms-steam-players", "ms-catalogue"}
    dispatch = re.search(r'"ms-dispatch" = \{(.*?)\n    \}', jobs, re.S)
    assert dispatch and re.search(r"secrets\s*=\s*\{\}", dispatch.group(1))


def test_the_deploy_identity_can_manage_secrets_but_never_read_one() -> None:
    bootstrap = code(read(BOOTSTRAP / "main.tf"))
    role = re.search(r'"secret_container_admin" \{(.*?)\n\}', bootstrap, re.S)
    assert role
    permissions = set(re.findall(r'"(secretmanager\.[A-Za-z.]+)"', role.group(1)))
    assert "secretmanager.versions.access" not in permissions
    assert "secretmanager.versions.add" not in permissions  # values are added by the owner
    assert {"secretmanager.secrets.create", "secretmanager.secrets.setIamPolicy"} <= permissions
    deploy_roles = re.search(r"deploy_roles = \[(.*?)\]", bootstrap, re.S)
    assert deploy_roles and "secretmanager" not in deploy_roles.group(1)


def test_collectors_can_write_only_their_own_paths() -> None:
    storage = read(PROD / "storage.tf")
    table = [row for row in grants(storage, "object_grants") if len(row) == 4]
    by_identity: dict[str, list[tuple[str, str, str]]] = {}
    for identity, bucket, role, prefix in table:
        by_identity.setdefault(identity, []).append((bucket, role, prefix))
    assert set(by_identity) == IDENTITIES - {"ms-scheduler"}

    writers = ("roles/storage.objectCreator", "roles/storage.objectUser")
    assert {(b, p) for b, r, p in by_identity["ms-steam-players"] if r in writers} == {
        ("landing", "steam_current_players/"),
        ("ops", "steam_current_players/"),
        ("ops", "quota/steam_web_api/"),
    }
    assert {(b, p) for b, r, p in by_identity["ms-wikimedia"] if r in writers} == {
        ("landing", "wikimedia_pageviews/"),
        ("raw", "wikimedia_pageviews/"),
        ("ops", "wikimedia_pageviews/"),
    }
    # Steam's raw payloads are never landed: no Steam path exists in the raw bucket.
    assert not [row for row in table if row[1] == "raw" and "steam" in row[3]]
    # Only the catalogue job touches the token cache; only features writes baseline state.
    assert {row[0] for row in table if row[1] == "tokens"} == {"ms-catalogue"}
    assert {row[0] for row in table if row[3] == "baseline/" and row[2] in writers} == {
        "ms-features"
    }
    assert ("state", "roles/storage.objectViewer", "baseline/") in by_identity["ms-parity"]
    # The dispatcher writes only its own operational state.
    assert {(b, p) for b, r, p in by_identity["ms-dispatch"] if r in writers} == {
        ("ops", "dispatch/")
    }
    # No runtime identity is given a role that can delete landed data or administer a bucket.
    roles = {row[2] for row in table}
    assert roles <= {
        "roles/storage.objectViewer",
        "roles/storage.objectCreator",
        "roles/storage.objectUser",
    }
    assert not [
        r for r in table if r[1] in ("landing", "raw") and r[2] == "roles/storage.objectUser"
    ]
    # A whole-bucket grant is the exception and is read-only, except the one-owner token cache.
    whole = [(row[0], row[1], row[2]) for row in table if row[3] == ""]
    assert sorted(whole) == [
        ("ms-catalogue", "tokens", "roles/storage.objectUser"),
        ("ms-dispatch", "ops", "roles/storage.objectViewer"),
        ("ms-warehouse", "landing", "roles/storage.objectViewer"),
        ("ms-warehouse", "raw", "roles/storage.objectViewer"),
    ]


def test_only_the_scheduler_starts_the_dispatcher_and_only_the_dispatcher_starts_jobs() -> None:
    jobs = code(read(PROD / "jobs.tf"))
    assert set(re.findall(r'^    "(ms-[a-z-]+)" = \{', read(PROD / "jobs.tf"), re.M)) == (
        IDENTITIES - {"ms-scheduler"}
    )
    invokers = re.findall(
        r'resource "google_cloud_run_v2_job_iam_member" "(\w+)" \{(.*?)\n\}', jobs, re.S
    )
    assert [name for name, _ in invokers] == ["scheduler_runs_dispatch", "dispatch_runs"]
    scheduler, dispatch = (body for _, body in invokers)
    assert 'this["ms-dispatch"]' in scheduler and 'local.member["ms-scheduler"]' in scheduler
    assert 'local.member["ms-dispatch"]' in dispatch and "local.dispatched_jobs" in dispatch
    assert 'if name != "ms-dispatch"' in jobs  # the dispatcher cannot start itself
    assert jobs.count("roles/run.invoker") == 2
    assert jobs.count('resource "google_cloud_scheduler_job"') == 1  # ONE trigger
    assert 'schedule    = "*/15 * * * *"' in jobs and "paused      = var.scheduler_paused" in jobs
    paused = re.search(
        r'variable "scheduler_paused" \{(.*?)\n\}', read(PROD / "variables.tf"), re.S
    )
    assert paused and "default     = true" in paused.group(1)  # nothing runs until it is released
    assert 'cpu    = "1"' in jobs  # jobs need a full vCPU


def test_jobs_are_deployed_by_digest_and_data_access_is_per_dataset() -> None:
    variables = read(PROD / "variables.tf")
    assert "@sha256:[0-9a-f]{64}$" in variables
    assert "image = var.image" in code(read(PROD / "jobs.tf"))
    bigquery = read(PROD / "bigquery.tf")
    assert set(re.findall(r"^    (ms_\w+)\s+=", bigquery, re.M)) == {
        "ms_raw",
        "ms_catalogue",
        "ms_staging",
        "ms_marts",
        "ms_features",
        "ms_ops",
    }
    table = [row for row in grants(bigquery, "dataset_grants") if len(row) == 3]
    assert {row[0] for row in table} == {"ms-warehouse", "ms-features", "ms-parity"}
    editors = {(row[0], row[1]) for row in table if row[2] == "roles/bigquery.dataEditor"}
    assert ("ms-features", "ms_features") in editors and ("ms-parity", "ms_ops") in editors
    assert not [e for e in editors if e[0] == "ms-parity" and e[1] != "ms_ops"]
    assert not [e for e in editors if e[0] == "ms-features" and e[1] != "ms_features"]
    assert "roles/bigquery.admin" not in code(bigquery)
    assert "require_partition_filter = true" in code(bigquery)
    assert "deletion_protection = true" in code(bigquery)


def test_every_fact_table_has_a_schema_whose_partition_and_cluster_columns_exist() -> None:
    bigquery = read(PROD / "bigquery.tf")
    shape = (
        r'(\w+) = \{\s*dataset\s*=\s*"(\w+)"\s*partition\s*=\s*"(\w+)"'
        r"\s*cluster\s*=\s*\[([^\]]*)\]"
    )
    tables = re.findall(shape, bigquery)
    assert {name for name, *_ in tables} == {
        "sample_attempts",
        "period_observations",
        "source_records",
        "binding_events",
        "resolution_decisions",
        "quarantine_events",
        "bucket_evaluations",
        "collection_runs",
    }
    for name, _, partition, cluster in tables:
        schema = json.loads(read(PROD / "schemas" / f"{name}.json"))
        columns = {column["name"]: column for column in schema}
        assert (
            columns[partition]["type"] == "TIMESTAMP" and columns[partition]["mode"] == "REQUIRED"
        )
        for column in re.findall(r'"(\w+)"', cluster):
            assert column in columns, (name, column)
        assert {"landed_object", "content_sha256", "loaded_at"} <= set(columns)  # provenance
    attempts = {c["name"]: c for c in json.loads(read(PROD / "schemas" / "sample_attempts.json"))}
    # The accepted sample model, field for field, with a nullable value: missing is never zero.
    from metaspike.domain.samples import SampleAttempt

    assert set(SampleAttempt.model_fields) <= set(attempts)
    assert attempts["value"]["mode"] == "NULLABLE" and attempts["attempt_id"]["mode"] == "REQUIRED"
    assert "not_sampled" in attempts["status"]["description"]
    shadow = json.loads(read(PROD / "schemas" / "bucket_evaluations.json"))
    assert any("SHADOW ONLY" in column.get("description", "") for column in shadow)


# --- GitHub trust ------------------------------------------------------------------------


def test_github_is_trusted_by_numeric_id_branch_and_environment() -> None:
    bootstrap = read(BOOTSTRAP / "main.tf")
    for required in (
        "assertion.repository_id == '${var.github_repository_id}'",
        "assertion.repository_owner_id == '${var.github_owner_id}'",
        "assertion.ref == 'refs/heads/main'",
        "assertion.environment == '${var.github_environment}'",
        'issuer_uri = "https://token.actions.githubusercontent.com"',
        "roles/iam.workloadIdentityUser",
    ):
        assert required in bootstrap, required
    variables = read(BOOTSTRAP / "variables.tf")
    assert variables.count('can(regex("^[0-9]+$"') == 2  # ids must be numeric, not names
    assert 'default     = "production"' in variables
    budget = code(bootstrap)
    assert "google_billing_budget" in budget and "[0.25, 0.5, 0.75, 1.0]" in budget
    assert "FORECASTED_SPEND" in budget
    assert re.search(r'variable "monthly_budget_usd".*?default\s*=\s*20', variables, re.S)


# --- workflows ---------------------------------------------------------------------------


def workflow(name: str) -> str:
    return read(WORKFLOWS / name)


def test_pull_request_ci_has_no_cloud_credentials() -> None:
    ci = code(workflow("ci.yml"))
    assert "id-token" not in ci and "google-github-actions" not in ci
    assert "gcloud" not in ci and "secrets." not in ci and "vars." not in ci
    assert "terraform plan" not in ci and "terraform apply" not in ci
    assert ci.count("-backend=false") == 2
    assert re.search(r"permissions:\n  contents: read\n", ci)
    for step in (
        "uv sync --locked",
        "ruff format --check",
        "ruff check",
        "mypy",
        "uv run pytest",
        "export-schemas --check",
        "evaluate-resolver",
        "tests/test_v1_reproduction.py",
        "terraform fmt -check",
        "terraform validate",
        "pip-audit",
        "docker build",
    ):
        assert step in ci, step


def test_verification_runs_once_per_commit_and_gates_every_cloud_step() -> None:
    ci, deploy = workflow("ci.yml"), workflow("deploy.yml")
    triggers = ci[ci.index("\non:\n") : ci.index("\npermissions:")]
    assert "pull_request:" in triggers and "workflow_call:" in triggers
    assert "push:" not in triggers  # a push to main is verified by deploy, not twice
    # Deploy reuses the whole CI workflow as its first job; it has no weaker copy of it.
    verify = deploy[deploy.index("  verify:\n") : deploy.index("  build-and-plan:\n")]
    assert "uses: ./.github/workflows/ci.yml" in code(verify)
    assert "uv run pytest" not in code(deploy)  # no second, divergent test list
    # Nothing that authenticates can start unless verification passed.
    build = deploy[deploy.index("  build-and-plan:\n") : deploy.index("  apply:\n")]
    apply = deploy[deploy.index("  apply:\n") :]
    assert "needs: verify" in code(build) and "needs: build-and-plan" in code(apply)
    assert "google-github-actions/auth" not in verify
    assert code(deploy).index("needs: verify") < code(deploy).index("google-github-actions/auth")
    assert "if: always()" not in deploy and "continue-on-error" not in deploy
    # And nothing authenticates at all before the bootstrap has provided identifiers.
    assert "if: vars.GCP_WORKLOAD_IDENTITY_PROVIDER != ''" in code(build)
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in ci


def test_every_external_action_is_pinned_to_a_full_commit_sha() -> None:
    """A tag can be moved or, as happened once, simply not exist. A commit cannot."""
    pinned = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")
    seen = set()
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for reference in re.findall(r"^\s*(?:- )?uses:\s*(.+?)\s*$", read(path), re.M):
            if reference.startswith("./"):
                continue  # this repository's own reusable workflow
            assert pinned.match(reference), f"{path.name}: {reference}"
            seen.add(reference.split("@")[0])
    assert seen == {
        "actions/checkout",
        "astral-sh/setup-uv",
        "hashicorp/setup-terraform",
        "aquasecurity/trivy-action",
        "google-github-actions/auth",
        "google-github-actions/setup-gcloud",
        "actions/upload-artifact",
        "actions/download-artifact",
    }
    # One version of each action across both workflows.
    versions: dict[str, set[str]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for name, sha in re.findall(r"uses:\s*([\w./-]+)@([0-9a-f]{40})", read(path)):
            versions.setdefault(name, set()).add(sha)
    assert all(len(shas) == 1 for shas in versions.values()), versions


def test_no_workflow_holds_a_key_or_a_hard_coded_credential() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = code(read(path))
        assert "credentials_json" not in text and "service_account_key" not in text
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in text and "BEGIN PRIVATE KEY" not in text
        assert "secrets." not in text  # nothing here needs a stored secret at all
        assert not re.search(r"\b[0-9A-F]{32}\b", text)
        assert not re.search(
            r"(?i)(api[_-]?key|password|token)\s*[:=]\s*[\"']?[A-Za-z0-9/+]{16,}", text
        )
        assert not re.search(r"[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com", text)
        assert not re.search(r"projects/\d+/locations", text)  # no real provider path


def test_deployment_uses_oidc_waits_for_approval_and_shouts_about_destruction() -> None:
    deploy = workflow("deploy.yml")
    text = code(deploy)
    assert "branches: [main]" in text and "pull_request" not in text
    assert text.count("id-token: write") == 2 and text.count("environment: production") == 2
    assert "workload_identity_provider: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}" in text
    assert "service_account: ${{ vars.GCP_DEPLOY_SERVICE_ACCOUNT }}" in text
    assert "cancel-in-progress: false" in text
    # Built, scanned, then pushed; and what is deployed is the digest that was pushed.
    assert text.index("trivy-action") < text.index("docker push")
    assert "RepoDigests" in text and "image=${{ steps.push.outputs.image }}" in text
    # Plan, show, approve, then apply exactly that plan.
    assert text.index("terraform plan") < text.index("terraform apply -input=false tfplan")
    assert "GITHUB_STEP_SUMMARY" in text and "DESTRUCTIVE PLAN" in text
    assert "outputs.destructive == '0' || github.event_name == 'workflow_dispatch'" in text
    assert "terraform destroy" not in text and "-auto-approve" not in text


# --- the image ---------------------------------------------------------------------------


def test_the_image_is_slim_locked_unprivileged_and_carries_no_credentials() -> None:
    dockerfile = read(REPO / "Dockerfile")
    assert dockerfile.count("FROM python:3.12-slim-bookworm") == 2
    assert "uv sync --locked --no-dev" in dockerfile and "--no-editable" in dockerfile
    assert "USER 10001:10001" in dockerfile and 'ENTRYPOINT ["metaspike"]' in dockerfile
    instructions = code(dockerfile)
    assert "COPY . " not in instructions and "ADD " not in instructions
    assert not re.search(r"(?i)\b(ARG|ENV)\s+\w*(KEY|SECRET|TOKEN|PASSWORD)", instructions)
    assert ".env" not in instructions and "tests" not in instructions
    ignore = read(REPO / ".dockerignore").splitlines()
    assert ignore[1] == "*" or "*" in ignore[:3]  # deny by default
    allowed = [line[1:] for line in ignore if line.startswith("!")]
    assert allowed == ["pyproject.toml", "uv.lock", "README.md", "src/"]
    for never in (".env", ".metaspike/", "tests/", "infra/", ".git/", "*.tfvars"):
        assert never in ignore, never


@pytest.mark.parametrize("name", ["phase4-runbook.md"])
def test_the_runbook_says_what_an_operator_must_know(name: str) -> None:
    runbook = read(REPO / "docs" / name)
    for statement in (
        "No cloud resource exists",
        "state bucket",
        "gcloud secrets versions add",
        "--data-file=-",
        "private GitHub repository",
        "scheduler_paused",
        "https://krishnachittella.dev/privacy",
        "rounded up to the nearest 100 millisecond",
        "measured in Part A2",
        "Nothing is public",
        "SHADOW",
    ):
        assert statement in runbook, statement
    assert "every Cloud Run Job invocation bills at least one minute" not in runbook
