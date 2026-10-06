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

  # Roles the deploy identity needs to manage the resources in infra/prod. There is no
  # Owner, no Editor, and no role that can read a secret's value.
  deploy_roles = [
    "roles/artifactregistry.admin",
    "roles/bigquery.admin",
    "roles/cloudscheduler.admin",
    "roles/iam.serviceAccountUser",
    "roles/logging.configWriter",
    "roles/monitoring.editor",
    "roles/run.admin",
    "roles/storage.admin",
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
    "google.subject"                = "assertion.sub"
    "attribute.repository_id"       = "assertion.repository_id"
    "attribute.repository_owner_id" = "assertion.repository_owner_id"
    "attribute.ref"                 = "assertion.ref"
    "attribute.environment"         = "assertion.environment"
  }

  # Numeric ids cannot be re-registered by someone else the way a name can. Only the
  # main branch, running in the approval-gated environment, is accepted at all.
  attribute_condition = join(" && ", [
    "assertion.repository_id == '${var.github_repository_id}'",
    "assertion.repository_owner_id == '${var.github_owner_id}'",
    "assertion.ref == 'refs/heads/main'",
    "assertion.environment == '${var.github_environment}'",
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
}

# Manage secret CONTAINERS and who may read them, without being able to read a value.
# The built-in secretmanager.admin role includes versions.access, so it is not used.
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

# --- Runtime identities ---------------------------------------------------------------
# Created here so that infra/prod never needs permission to change project-level IAM.

resource "google_service_account" "runtime" {
  for_each = local.runtime_identities

  project      = var.project_id
  account_id   = each.key
  display_name = each.value

  depends_on = [google_project_service.enabled]
}

resource "google_project_iam_member" "runtime_logs" {
  for_each = { for name, _ in local.runtime_identities : name => name if name != "ms-scheduler" }

  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

resource "google_project_iam_member" "bigquery_jobs" {
  for_each = toset(local.bigquery_job_runners)

  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.runtime[each.key].email}"
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

data "google_project" "this" {
  project_id = var.project_id
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
