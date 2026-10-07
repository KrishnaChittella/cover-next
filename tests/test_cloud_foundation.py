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

KEY_SHAPED = "0123456789ABCDEF0123456789ABCDEF"

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


def test_source_secrets_reach_a_job_only_by_explicit_numeric_version() -> None:
    variables = read(PROD / "variables.tf")
    names = ("steam_web_api_key_version", "igdb_client_id_version", "igdb_client_secret_version")
    for name in names:
        declaration = re.search(rf'variable "{name}" \{{(.*?)\n\}}', variables, re.S)
        assert declaration, name
        body = declaration.group(1)
        assert re.search(r"default\s*=\s*null", body), name  # unset unless deliberately given
        assert 'can(regex("^[1-9][0-9]*$"' in body, name  # a positive integer, nothing else
        assert "sensitive" not in body  # a version number is metadata, not a secret
    # The pattern itself: numbers pass; "latest", aliases, zero, signs and values do not.
    accepted = re.compile(r"^[1-9][0-9]*$")
    assert all(accepted.match(v) for v in ("1", "7", "42", "1000"))
    for refused in ("latest", "LATEST", "0", "01", "-1", "1.0", " 1", "v1", "", KEY_SHAPED):
        assert not accepted.match(refused), refused
    jobs = code(read(PROD / "jobs.tf"))
    # "latest" is never a version anywhere; the word survives only in the messages that
    # explain why it is refused.
    everything = code(terraform(PROD))
    assert not re.search(r'version\s*=\s*"latest"', everything)
    mentions = [line for line in everything.splitlines() if "latest" in line]
    assert len(mentions) == 3 and all("error_message" in line for line in mentions)
    assert "if local.secret_version[secret] != null" in jobs
    assert "version = local.secret_version[env.value]" in jobs
    mapping = re.search(r"secret_version = \{(.*?)\}", jobs, re.S)
    assert mapping
    assert dict(re.findall(r'"([a-z-]+)"\s*=\s*var\.(\w+)', mapping.group(1))) == {
        "steam-web-api-key": "steam_web_api_key_version",
        "igdb-client-id": "igdb_client_id_version",
        "igdb-client-secret": "igdb_client_secret_version",
    }
    # No path for a secret VALUE: no version resource, no data field, and the deploy
    # workflow passes none of these variables.
    for root in (BOOTSTRAP, PROD):
        text = code(terraform(root))
        assert "google_secret_manager_secret_version" not in text and "secret_data" not in text
    assert "_version=" not in workflow("deploy.yml")
    example = read(PROD / "prod.tfvars.example")
    assert not re.search(r"^\w+_version\s*=", example, re.M)  # shown only as comments
    # The behaviour itself is proven by `terraform test` with a mocked provider.
    tests = read(PROD / "tests" / "source_secrets.tftest.hcl")
    assert 'mock_provider "google" {}' in tests and tests.count("command = plan") == 7
    assert "command = apply" not in tests
    for run in (
        "default_has_no_source_secret_reference_anywhere",
        "steam_version_reaches_only_the_jobs_that_use_steam",
        "catalogue_gets_only_the_individually_configured_references",
        "all_three_versions_configured",
        "latest_is_refused_for_steam",
        "latest_is_refused_for_igdb_client_id",
        "a_value_or_a_non_positive_number_is_refused",
    ):
        assert f'run "{run}"' in tests, run
    assert "terraform test" in workflow("ci.yml")


def test_alerts_on_log_metrics_depend_on_those_metrics() -> None:
    monitoring = read(PROD / "monitoring.tf")
    policies = dict(
        re.findall(
            r'resource "google_monitoring_alert_policy" "(\w+)" \{(.*?)\n\}\n', monitoring, re.S
        )
    )
    assert set(policies) == {
        "dispatcher_silent",
        "job_failures",
        "steam_refused",
        "parity_mismatch",
    }
    # These two read a counter created in this root. Each names the resource itself in
    # its filter, so Terraform orders the metric before the alert without a depends_on.
    for policy, metric in (
        ("steam_refused", "ms_steam_refused_403"),
        ("parity_mismatch", "ms_baseline_parity_mismatch"),
    ):
        assert f'google_logging_metric.counter["{metric}"].name' in policies[policy], policy
        assert "logging.googleapis.com/user/${google_logging_metric.counter" in policies[policy]
    # No alert reaches a log-based metric through a hard-coded name Terraform cannot see.
    for body in policies.values():
        for user_metric in re.findall(r"logging\.googleapis\.com/user/(\S+?)\\\"", body):
            assert user_metric.startswith("${google_logging_metric.counter["), user_metric
    # The other two read Cloud Run's own built-in metric and depend on nothing here.
    for policy in ("dispatcher_silent", "job_failures"):
        assert "run.googleapis.com/job/completed_execution_count" in policies[policy]
        assert "google_logging_metric" not in policies[policy]
    assert "time_sleep" not in monitoring and "hashicorp/time" not in terraform(PROD)


def test_the_deploy_identity_may_act_only_as_the_named_runtime_identities() -> None:
    bootstrap = code(read(BOOTSTRAP / "main.tf"))
    deploy_roles = re.search(r"deploy_roles = \[(.*?)\]", bootstrap, re.S)
    assert deploy_roles
    project_roles = set(re.findall(r'"(roles/[A-Za-z.]+)"', deploy_roles.group(1)))
    assert project_roles == {
        "roles/bigquery.admin",
        "roles/cloudscheduler.admin",
        "roles/logging.configWriter",
        "roles/monitoring.editor",
        "roles/run.admin",
        "roles/storage.admin",
    }
    # No project-level "act as any service account", for ms-deploy or anyone.
    assert "roles/iam.serviceAccountUser" not in deploy_roles.group(1)
    for resource in re.findall(
        r'resource "google_project_iam_member" "\w+" \{(.*?)\n\}', bootstrap, re.S
    ):
        assert "serviceAccountUser" not in resource
    # It is granted once, on the runtime service accounts themselves, and only those.
    assert bootstrap.count("roles/iam.serviceAccountUser") == 1
    grant = re.search(
        r'resource "google_service_account_iam_member" "deploy_acts_as_runtime" \{(.*?)\n\}',
        bootstrap,
        re.S,
    )
    assert grant
    assert "for_each = local.runtime_identities" in grant.group(1)
    assert "google_service_account.runtime[each.key].name" in grant.group(1)
    assert "google_service_account.deploy.email" in grant.group(1)
    assert "google_service_account.deploy.name" not in grant.group(1)  # never on itself
    # Every identity a job or the scheduler runs as is one of those eight.
    prod = code(terraform(PROD))
    used = set(re.findall(r'identity\s*=\s*"(ms-[a-z-]+)"', prod)) | {"ms-scheduler"}
    assert used == IDENTITIES
    assert 'service_account_email = local.sa["ms-scheduler"]' in prod
    assert "ms-deploy" not in prod  # nothing in production runs as the deployer


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
    # Recorded, not hidden: managing a secret's IAM is itself a privilege.
    assert "privileged identity" in read(BOOTSTRAP / "main.tf")


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
    # The dispatcher writes only its own operational state and its own record of
    # intent. It can create under its landing prefix and cannot read or replace there.
    assert {(b, r, p) for b, r, p in by_identity["ms-dispatch"] if r in writers} == {
        ("ops", "roles/storage.objectUser", "dispatch/"),
        ("landing", "roles/storage.objectCreator", "dispatch/"),
    }
    # Derived catalogue history has its own landing prefix, apart from any source's.
    assert ("landing", "roles/storage.objectCreator", "catalogue/") in by_identity["ms-catalogue"]
    # Nothing in the landing bucket is writable by more than create, by anyone.
    assert {row[2] for row in table if row[1] == "landing" and row[2] in writers} == {
        "roles/storage.objectCreator"
    }
    # Completion markers need no grant of their own: each collector already holds
    # read and write by exact name under its own operations prefix, and nobody was
    # given a listing for them.
    assert len(table) == 26
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


def test_dataset_access_is_authoritative_and_default_entries_cannot_return() -> None:
    bigquery = code(read(PROD / "bigquery.tf"))
    # One whole policy per dataset. Nothing is merged with what BigQuery put there.
    assert 'resource "google_bigquery_dataset_iam_policy" "this"' in bigquery
    assert "for_each = local.dataset_policy" in bigquery
    # No additive dataset grant of any kind exists beside it: mixing the two would let
    # one silently undo the other.
    for additive in (
        'resource "google_bigquery_dataset_iam_member"',
        'resource "google_bigquery_dataset_iam_binding"',
        'resource "google_bigquery_dataset_access"',
    ):
        assert additive not in bigquery, additive
    assert not re.search(r"^\s*access\s*\{", bigquery, re.M)  # nor an inline access block
    # The old per-member grants are forgotten, not destroyed.
    removed = re.search(r"removed \{(.*?)\n\}", bigquery, re.S)
    assert removed and "google_bigquery_dataset_iam_member.access" in removed.group(1)
    assert "destroy = false" in removed.group(1)
    # The only owner is the project's owners. The deploy identity, project editors and
    # project viewers hold nothing at dataset level.
    assert '{ "roles/bigquery.dataOwner" = ["projectOwner:${var.project_id}"] }' in bigquery
    for absent in ("ms-deploy", "projectEditor", "projectViewer", "projectWriters",
                   "projectReaders", "allUsers", "allAuthenticatedUsers"):  # fmt: skip
        assert absent not in bigquery, absent
    table = [row for row in grants(read(PROD / "bigquery.tf"), "dataset_grants") if len(row) == 3]
    assert len(table) == 14 and {row[2] for row in table} == {
        "roles/bigquery.dataEditor", "roles/bigquery.dataViewer"
    }  # fmt: skip
    # No runtime identity holds a project-level BigQuery data role.
    bootstrap = code(terraform(BOOTSTRAP))
    for broad in (
        "roles/bigquery.dataEditor",
        "roles/bigquery.dataViewer",
        "roles/bigquery.dataOwner",
    ):
        assert broad not in bootstrap, broad
    assert (PROD / "tests" / "dataset_access.tftest.hcl").is_file()


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
        "expected_targets",
        "sample_native_ids",
        "entity_events",
        "lifecycle_events",
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


def test_github_is_trusted_only_for_one_manual_workflow_by_one_user() -> None:
    bootstrap = read(BOOTSTRAP / "main.tf")
    condition = re.search(r"attribute_condition = join\(\" && \", \[(.*?)\]\)", bootstrap, re.S)
    assert condition
    clauses = re.findall(r'"(assertion\.[^"]+)"', condition.group(1))
    assert clauses == [
        "assertion.repository_id == '${var.github_repository_id}'",
        "assertion.repository_owner_id == '${var.github_owner_id}'",
        "assertion.repository_visibility == 'private'",
        "assertion.ref == 'refs/heads/main'",
        "assertion.workflow_ref == "
        "'${var.github_repository}/.github/workflows/deploy.yml@refs/heads/main'",
        "assertion.event_name == 'workflow_dispatch'",
        "assertion.actor_id == '${var.github_actor_id}'",
    ]
    assert "||" not in condition.group(1)  # every clause must hold; there is no alternative
    mapping = re.search(r"attribute_mapping = \{(.*?)\}", bootstrap, re.S)
    assert mapping
    assert dict(re.findall(r'"([\w.]+)"\s*=\s*"([\w.]+)"', mapping.group(1))) == {
        "google.subject": "assertion.sub",
        "attribute.repository_id": "assertion.repository_id",
        "attribute.repository_owner_id": "assertion.repository_owner_id",
        "attribute.repository_visibility": "assertion.repository_visibility",
        "attribute.ref": "assertion.ref",
        "attribute.workflow_ref": "assertion.workflow_ref",
        "attribute.event_name": "assertion.event_name",
        "attribute.actor_id": "assertion.actor_id",
    }
    text = code(bootstrap)
    assert 'issuer_uri = "https://token.actions.githubusercontent.com"' in text
    assert text.count("roles/iam.workloadIdentityUser") == 1
    assert "attribute.repository_id/${var.github_repository_id}" in text  # bound by numeric id
    # No GitHub environment or reviewer is part of the trust boundary.
    assert "environment" not in text
    variables = read(BOOTSTRAP / "variables.tf")
    assert "github_environment" not in variables
    assert variables.count('can(regex("^[0-9]+$"') == 3  # repo, owner and actor ids are numeric
    for name in ("github_repository", "github_actor_id"):
        declaration = re.search(rf'variable "{name}" \{{(.*?)\n\}}', variables, re.S)
        assert declaration and "default" not in declaration.group(1), name
    # The registry exists before any image does, and the deploy identity only pushes to it.
    assert 'resource "google_artifact_registry_repository" "images"' in text
    assert "roles/artifactregistry.writer" in text and "roles/artifactregistry.admin" not in text
    assert "google_artifact_registry_repository" not in code(terraform(PROD))
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
    jobs = deploy[deploy.index("\njobs:\n") :]
    names = re.findall(r"^  ([a-z-]+):\n", jobs, re.M)
    assert names == ["verify", "identity-check", "plan", "apply"]
    bodies = dict(zip(names, re.split(r"^  [a-z-]+:\n", jobs, flags=re.M)[1:], strict=True))
    # Deploy reuses the whole CI workflow as its first job; it has no weaker copy of it.
    assert "uses: ./.github/workflows/ci.yml" in code(bodies["verify"])
    assert "uv run pytest" not in code(deploy)
    assert "google-github-actions/auth" not in bodies["verify"]
    assert "id-token" not in bodies["verify"]
    for name in ("identity-check", "plan", "apply"):
        body = code(bodies[name])
        # Nothing that authenticates can start unless verification passed...
        assert "needs: verify" in body, name
        # ...or for anything but a manual run on main, of the named operation...
        assert "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'" in body
        assert f"inputs.operation == '{name}'" in body
        # ...or before the bootstrap has provided identifiers.
        assert "vars.GCP_WORKLOAD_IDENTITY_PROVIDER != ''" in body
        assert "id-token: write" in body and "google-github-actions/auth" in body
        assert "environment:" not in body  # no GitHub environment is relied on
    assert "if: always()" not in deploy and "continue-on-error" not in deploy
    assert deploy.count("id-token: write") == 3  # exactly the three jobs above
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in ci


def test_a_push_to_main_can_never_reach_cloud_authentication() -> None:
    deploy = code(workflow("deploy.yml"))
    triggers = deploy[deploy.index("\non:\n") : deploy.index("\npermissions:")]
    assert "push:" in triggers and "workflow_dispatch:" in triggers  # pushes still verify
    assert "pull_request" not in triggers and "schedule" not in triggers
    assert "default: identity-check" in triggers  # the default manual run changes nothing
    # Every `if` that leads to authentication demands workflow_dispatch; none accepts push.
    conditions = re.findall(r"    if: >-\n((?:      .+\n)+)", deploy)
    assert len(conditions) == 3
    for condition in conditions:
        assert "github.event_name == 'workflow_dispatch'" in condition
        assert "||" not in condition and "push" not in condition
    # And Google would refuse a push's token anyway.
    assert "assertion.event_name == 'workflow_dispatch'" in read(BOOTSTRAP / "main.tf")


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


def test_the_image_scan_gate_is_strict_and_builds_are_never_from_a_stale_cache() -> None:
    for name in ("ci.yml", "deploy.yml"):
        text = code(workflow(name))
        assert "docker build --pull --no-cache " in text, name
        assert text.count("docker build") == 1
        scan = text[text.index("trivy-action") :][:400]
        assert "severity: CRITICAL,HIGH" in scan and 'exit-code: "1"' in scan
        assert "trivyignores" not in text and ".trivyignore" not in text
        assert "skip-" not in scan and 'exit-code: "0"' not in text
    assert not (REPO / ".trivyignore").exists()


def test_no_workflow_holds_a_key_or_a_hard_coded_credential() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = code(read(path))
        assert "credentials_json" not in text and "service_account_key" not in text
        assert "BEGIN PRIVATE KEY" not in text
        # The credentials path is only ever READ, to prove the credential is federated.
        assert not re.search(r"GOOGLE_APPLICATION_CREDENTIALS\s*[:=]", text)
        assert text.count("GOOGLE_APPLICATION_CREDENTIALS") == (
            1 if path.name == "deploy.yml" else 0
        )
        assert "secrets." not in text  # nothing here needs a stored secret at all
        assert not re.search(r"\b[0-9A-F]{32}\b", text)
        assert not re.search(
            r"(?i)(api[_-]?key|password|token)\s*[:=]\s*[\"']?[A-Za-z0-9/+]{16,}", text
        )
        assert not re.search(r"[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com", text)
        assert not re.search(r"projects/\d+/locations", text)  # no real provider path


def test_deployment_is_two_manual_runs_and_applies_only_a_plan_named_by_hash() -> None:
    deploy = workflow("deploy.yml")
    text = code(deploy)
    assert "workload_identity_provider: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}" in text
    assert "service_account: ${{ vars.GCP_DEPLOY_SERVICE_ACCOUNT }}" in text
    assert "cancel-in-progress: false" in text
    jobs = text[text.index("\njobs:\n") :]
    plan = jobs[jobs.index("  plan:\n") : jobs.index("  apply:\n")]
    apply = jobs[jobs.index("  apply:\n") :]
    # Plan: built, scanned, THEN authenticated and pushed; the pushed digest is planned.
    assert plan.index("trivy-action") < plan.index("google-github-actions/auth")
    assert plan.index("google-github-actions/auth") < plan.index("docker push")
    assert "RepoDigests" in plan and "image=${{ steps.push.outputs.image }}" in plan
    assert "terraform plan" in plan and "terraform apply" not in plan  # it changes nothing
    assert "sha256sum tfplan" in plan and "GITHUB_STEP_SUMMARY" in plan
    assert "DESTRUCTIVE PLAN" in plan and "upload-artifact" in plan
    # Apply: a separate manual run, given the plan's run id AND its hash, never re-planning.
    assert "inputs.plan_run_id != '' && inputs.plan_sha256 != ''" in apply
    assert "run-id: ${{ inputs.plan_run_id }}" in apply
    assert 'test "${actual}" = "${{ inputs.plan_sha256 }}"' in apply
    assert apply.index("sha256sum tfplan") < apply.index("google-github-actions/auth")
    assert "terraform plan" not in apply and "terraform apply -input=false tfplan" in apply
    assert "allow_destroy" in apply and apply.index("allow_destroy") < apply.index(
        "terraform apply"
    )
    assert "docker" not in apply  # the image is whatever the reviewed plan names
    # The identity check is read-only: it neither builds, plans nor applies.
    check = jobs[jobs.index("  identity-check:\n") : jobs.index("  plan:\n")]
    assert "terraform" not in check and "docker" not in check and "gcloud auth list" in check
    assert 'test "${kind}" = "external_account"' in check
    assert "terraform destroy" not in text and "-auto-approve" not in text


# --- the image ---------------------------------------------------------------------------


def test_the_image_is_slim_locked_unprivileged_and_carries_no_credentials() -> None:
    dockerfile = read(REPO / "Dockerfile")
    assert dockerfile.count("FROM python:3.12-slim-bookworm") == 2
    # Both stages use the official base by tag AND immutable digest, and the same one.
    bases = re.findall(
        r"^FROM (python:3\.12-slim-bookworm@sha256:[0-9a-f]{64})\b", dockerfile, re.M
    )
    assert len(bases) == 2 and len(set(bases)) == 1
    assert not re.search(r"^FROM (?!.*@sha256:)", dockerfile, re.M)  # no mutable base
    # Every other image the build pulls from is pinned by digest as well.
    helpers = re.findall(r"--from=(\S+)", dockerfile)
    external = [helper for helper in helpers if "/" in helper]
    assert external == [
        "ghcr.io/astral-sh/uv:0.5@sha256:"
        "7bff3c3776ec467fc1437960f2c469d8beb30f536a6465a3350c647ccd260ec2"
    ]
    assert {helper for helper in helpers if "/" not in helper} == {"build"}  # our own stage
    # The security fix is targeted: two named packages, upgrade only, no blanket upgrade,
    # no pinned Debian revision, and it runs as root before the user is dropped.
    patch = re.search(r"RUN apt-get update \\\n(.*?)rm -rf /var/lib/apt/lists/\*", dockerfile, re.S)
    assert patch and "--no-install-recommends --only-upgrade" in patch.group(1)
    assert re.findall(r"^\s+([a-z0-9.+-]+) \\$", patch.group(1), re.M) == [
        "libpcre2-8-0",
        "perl-base",
    ]
    assert "apt-get upgrade" not in code(dockerfile) and "dist-upgrade" not in dockerfile
    assert "=" not in patch.group(1)  # no hard-coded package version
    assert dockerfile.index("--only-upgrade") < dockerfile.index("USER 10001:10001")
    assert "uv sync --locked --no-dev" in dockerfile and "--no-editable" in dockerfile
    assert "USER 10001:10001" in dockerfile and 'ENTRYPOINT ["metaspike"]' in dockerfile
    instructions = code(dockerfile)
    assert "COPY . " not in instructions and "ADD " not in instructions
    assert not re.search(r"(?i)\b(ARG|ENV)\s+\w*(KEY|SECRET|TOKEN|PASSWORD)", instructions)
    assert ".env" not in instructions
    # The application's own tests and fixtures never enter the image. dbt's data
    # tests (warehouse/tests, SQL only) do: the warehouse job runs them.
    copied = re.findall(r"^COPY\s+(?:--\S+\s+)*(.+)$", instructions, re.M)
    assert not [line for line in copied if re.search(r"(^|\s)tests(/|\s|$)", line)]
    assert "/opt/dbt" in instructions and "--no-dev --no-install-project" in instructions
    ignore = read(REPO / ".dockerignore").splitlines()
    assert ignore[1] == "*" or "*" in ignore[:3]  # deny by default
    allowed = [line[1:] for line in ignore if line.startswith("!")]
    assert allowed == ["pyproject.toml", "uv.lock", "README.md", "src/", "warehouse/"]
    for never in ("warehouse/.venv/", "warehouse/target/", "warehouse/logs/"):
        assert never in ignore, never
    for never in (".env", ".metaspike/", "tests/", "infra/", ".git/", "*.tfvars"):
        assert never in ignore, never


@pytest.mark.parametrize("name", ["phase4-runbook.md"])
def test_the_runbook_says_what_an_operator_must_know(name: str) -> None:
    runbook = read(REPO / "docs" / name)
    for statement in (
        "Nothing is collecting",
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
