# MetaSpike bootstrap: applied by the owner, by hand, rarely.
#
# It creates what CI needs before it can deploy anything: enabled APIs, the GitHub
# trust, the deploy identity, the runtime identities with their few project-level
# roles, and the budget. Everything else is in infra/prod and is applied through CI.
#
# Nothing here is public and no secret value appears anywhere.

locals {
  apis = [
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
  ]

  # One identity per job. None is shared and none is broad.
  runtime_identities = {
    "ms-scheduler"     = "Cloud Scheduler: may start the dispatcher and nothing else"
    "ms-dispatch"      = "Decides what is due and starts approved jobs; holds no source secret"
    "ms-steam-players" = "Samples Steam concurrent players"
    "ms-wikimedia"     = "Collects Wikimedia pageviews; needs no secret"
    "ms-catalogue"     = "Collects IGDB and Steam catalogue metadata"
    "ms-warehouse"     = "Loads landed files into BigQuery and runs dbt"
    "ms-features"      = "Runs the Python feature engine and writes baseline state"
    "ms-parity"        = "Rebuilds baseline state from history and compares"
  }

  # Identities that run BigQuery jobs. Data access is granted per dataset in infra/prod.
  bigquery_job_runners = ["ms-warehouse", "ms-features", "ms-parity"]

  # Project-level roles the deploy identity needs to manage the resources in infra/prod.
  # There is no Owner, no Editor, and no role that can read a secret's value. Acting as
  # a service account is NOT here: it is granted per service account, further down.
  #
  # BigQuery and Cloud Storage are not here either. Their predefined admin roles carry
  # the data itself (rows, objects, query jobs), which deploying never needs, so each
  # is replaced by a custom role below that holds configuration and IAM only.
  deploy_roles = [
    "roles/cloudscheduler.admin",
    "roles/logging.configWriter",
    "roles/monitoring.editor",
    "roles/run.admin",
  ]

  # What the pinned provider calls for a dataset, a table and a dataset policy: read,
  # create, update, and the access list. No row can be read or written with these, and
  # no job can be submitted. Nothing can be deleted: tables are deletion-protected and
  # removing a dataset or a table is the owner's decision, made with the owner's login.
  # rowAccessPolicies.list is a read the provider makes while planning a schema change.
  deploy_bigquery_permissions = [
    "bigquery.datasets.create",
    "bigquery.datasets.get",
    "bigquery.datasets.getIamPolicy",
    "bigquery.datasets.setIamPolicy",
    "bigquery.datasets.update",
    "bigquery.rowAccessPolicies.list",
    "bigquery.tables.create",
    "bigquery.tables.get",
    "bigquery.tables.update",
  ]

  # What the pinned provider calls for a bucket and its IAM members. No object can be
  # read, listed, written or deleted with these, and no bucket can be deleted. The
  # Terraform state is reached through its own grant on the state bucket alone.
  deploy_storage_permissions = [
    "storage.buckets.create",
    "storage.buckets.get",
    "storage.buckets.getIamPolicy",
    "storage.buckets.setIamPolicy",
    "storage.buckets.update",
  ]
}

resource "google_project_service" "enabled" {
  for_each = toset(local.apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --- GitHub Actions trust: OIDC only, no service-account key --------------------------

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  description               = "Short-lived GitHub OIDC tokens. No long-lived key exists."

  depends_on = [google_project_service.enabled]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"                  = "assertion.sub"
    "attribute.repository_id"         = "assertion.repository_id"
    "attribute.repository_owner_id"   = "assertion.repository_owner_id"
    "attribute.repository_visibility" = "assertion.repository_visibility"
    "attribute.ref"                   = "assertion.ref"
    "attribute.workflow_ref"          = "assertion.workflow_ref"
    "attribute.event_name"            = "assertion.event_name"
    "attribute.actor_id"              = "assertion.actor_id"
  }

  # A token is accepted only if EVERY line holds. The boundary is made of GitHub's
  # immutable numeric ids, which cannot be re-registered the way a name can:
  #   this repository, owned by this account, while it is private;
  #   the main branch; this one workflow file, as it exists on main;
  #   started by hand (never by a push); by this one user.
  # There is no GitHub environment or reviewer in this design. A push to main can run
  # verification, but the token GitHub issues for it fails this condition.
  attribute_condition = join(" && ", [
    "assertion.repository_id == '${var.github_repository_id}'",
    "assertion.repository_owner_id == '${var.github_owner_id}'",
    "assertion.repository_visibility == 'private'",
    "assertion.ref == 'refs/heads/main'",
    "assertion.workflow_ref == '${var.github_repository}/.github/workflows/deploy.yml@refs/heads/main'",
    "assertion.event_name == 'workflow_dispatch'",
    "assertion.actor_id == '${var.github_actor_id}'",
  ])

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deploy" {
  project      = var.project_id
  account_id   = "ms-deploy"
  display_name = "MetaSpike deploy (GitHub Actions via workload identity)"

  depends_on = [google_project_service.enabled]
}

resource "google_service_account_iam_member" "deploy_from_github" {
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.github_repository_id}"
}

resource "google_project_iam_member" "deploy" {
  for_each = toset(local.deploy_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deploy.email}"

  # Project-level IAM is read and written through an API this root itself enables.
  depends_on = [google_project_service.enabled]
}

# Manage datasets, tables and buckets as infrastructure, without the data in them.
#
# Security property to keep in view, as with the secret containers below: these roles
# can set IAM on a dataset or a bucket, so the identity could grant itself the data
# access it does not hold. That is a recorded IAM change, not a quiet read, and the
# control on it remains the federation condition.
resource "google_project_iam_custom_role" "deploy_bigquery" {
  project     = var.project_id
  role_id     = "msDeployBigQueryConfig"
  title       = "MetaSpike deploy: BigQuery configuration"
  description = "Datasets, tables and dataset access as infrastructure. No rows, no jobs, no deletion."
  permissions = local.deploy_bigquery_permissions

  depends_on = [google_project_service.enabled]
}

resource "google_project_iam_member" "deploy_bigquery" {
  project = var.project_id
  role    = google_project_iam_custom_role.deploy_bigquery.id
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_project_iam_custom_role" "deploy_storage" {
  project     = var.project_id
  role_id     = "msDeployBucketConfig"
  title       = "MetaSpike deploy: bucket configuration"
  description = "Bucket settings and bucket IAM as infrastructure. No objects, no bucket deletion."
  permissions = local.deploy_storage_permissions

  depends_on = [google_project_service.enabled]
}

resource "google_project_iam_member" "deploy_storage" {
  project = var.project_id
  role    = google_project_iam_custom_role.deploy_storage.id
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

# Manage secret CONTAINERS and who may read them, without being able to read a value.
# The built-in secretmanager.admin role includes versions.access, so it is not used.
#
# Security property to keep in view: an identity that can set IAM on a secret could,
# once the secret has a version, grant itself or anything else access to it. ms-deploy
# is therefore a privileged identity even though it cannot read a value directly, and
# the control on it is the federation condition (one workflow, started by hand, by one
# user). To be reviewed before Part C, when the first secret versions are added.
resource "google_project_iam_custom_role" "secret_container_admin" {
  project     = var.project_id
  role_id     = "msSecretContainerAdmin"
  title       = "MetaSpike secret container admin"
  description = "Create and bind secrets. Deliberately cannot access a secret version."
  permissions = [
    "secretmanager.secrets.create",
    "secretmanager.secrets.delete",
    "secretmanager.secrets.get",
    "secretmanager.secrets.getIamPolicy",
    "secretmanager.secrets.list",
    "secretmanager.secrets.setIamPolicy",
    "secretmanager.secrets.update",
    "secretmanager.versions.get",
    "secretmanager.versions.list",
  ]

  depends_on = [google_project_service.enabled]
}

resource "google_project_iam_member" "deploy_secret_containers" {
  project = var.project_id
  role    = google_project_iam_custom_role.secret_container_admin.id
  member  = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_storage_bucket_iam_member" "deploy_state" {
  bucket = var.state_bucket_name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.deploy.email}"
}

# --- Image registry -------------------------------------------------------------------
# Here, not in infra/prod: the production root needs an image digest before it can
# plan, and an image cannot be pushed to a registry that does not exist yet.

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = "metaspike"
  format        = "DOCKER"
  description   = "MetaSpike application image. Private. Jobs are deployed by digest."

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s"
    }
  }

  labels = {
    system = "metaspike"
  }

  depends_on = [google_project_service.enabled]
}

# The deploy identity may push to this one repository. It administers no registry.
resource "google_artifact_registry_repository_iam_member" "deploy_pushes" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deploy.email}"
}

# --- Runtime identities ---------------------------------------------------------------
# Created here so that infra/prod never needs permission to change project-level IAM.

resource "google_service_account" "runtime" {
  for_each = local.runtime_identities

  project      = var.project_id
  account_id   = each.key
  display_name = each.value

  depends_on = [google_project_service.enabled]
}

# To create a Cloud Run job that runs as an identity, or a scheduler job that calls as
# one, the deployer must be allowed to act as that identity. That is granted here on
# each of the eight runtime service accounts individually: seven are a job's identity
# and ms-scheduler is the scheduler job's caller. It is not granted on the project, so
# it does not extend to ms-deploy itself, to Google's default service accounts, or to
# any service account created later.
resource "google_service_account_iam_member" "deploy_acts_as_runtime" {
  for_each = local.runtime_identities

  service_account_id = google_service_account.runtime[each.key].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_project_iam_member" "runtime_logs" {
  for_each = { for name, _ in local.runtime_identities : name => name if name != "ms-scheduler" }

  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.runtime[each.key].email}"

  depends_on = [google_project_service.enabled]
}

resource "google_project_iam_member" "bigquery_jobs" {
  for_each = toset(local.bigquery_job_runners)

  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.runtime[each.key].email}"

  depends_on = [google_project_service.enabled]
}

# --- Budget: a warning, from day one --------------------------------------------------

resource "google_monitoring_notification_channel" "owner_email" {
  project      = var.project_id
  display_name = "MetaSpike owner"
  type         = "email"

  labels = {
    email_address = var.alert_email
  }

  depends_on = [google_project_service.enabled]
}

# Read only after the APIs are on: looking a project up needs one of them.
data "google_project" "this" {
  project_id = var.project_id

  depends_on = [google_project_service.enabled]
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account_id
  display_name    = "MetaSpike Production monthly"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget_usd)
    }
  }

  # 25 / 50 / 75 / 100 percent of actual spend, and a forecast of going over.
  dynamic "threshold_rules" {
    for_each = [0.25, 0.5, 0.75, 1.0]
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.owner_email.id]
    disable_default_iam_recipients   = false
  }

  depends_on = [google_project_service.enabled]
}
