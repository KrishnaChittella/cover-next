# One image, several jobs. Each job has its own command, its own identity and only the
# secrets that identity may read. Cloud Run jobs have no URL and are not reachable
# from the internet; they are started by the scheduler (dispatcher only) or by the
# dispatcher (everything else).
#
# The commands below exist in the application. The scheduler still starts paused:
# nothing runs unattended until that is deliberately released.

locals {
  jobs = {
    "ms-dispatch" = {
      args        = ["dispatch"]
      identity    = "ms-dispatch"
      memory      = "512Mi"
      timeout     = "300s"
      max_retries = 0
      secrets     = {}
    }
    "ms-steam-players" = {
      args        = ["collect-steam-players"]
      identity    = "ms-steam-players"
      memory      = "512Mi"
      timeout     = "840s"
      max_retries = 0
      secrets     = { METASPIKE_STEAM_WEB_API_KEY = "steam-web-api-key" }
    }
    "ms-wikimedia" = {
      args        = ["collect-wikimedia"]
      identity    = "ms-wikimedia"
      memory      = "512Mi"
      timeout     = "900s"
      max_retries = 1
      secrets     = {}
    }
    "ms-catalogue" = {
      args        = ["collect-catalogue"]
      identity    = "ms-catalogue"
      memory      = "2Gi"
      timeout     = "3600s"
      max_retries = 1
      secrets = {
        METASPIKE_STEAM_WEB_API_KEY  = "steam-web-api-key"
        METASPIKE_IGDB_CLIENT_ID     = "igdb-client-id"
        METASPIKE_IGDB_CLIENT_SECRET = "igdb-client-secret"
      }
    }
    "ms-warehouse" = {
      args        = ["warehouse"]
      identity    = "ms-warehouse"
      memory      = "1Gi"
      timeout     = "1800s"
      max_retries = 1
      secrets     = {}
    }
    "ms-features" = {
      args        = ["features"]
      identity    = "ms-features"
      memory      = "1Gi"
      timeout     = "1800s"
      max_retries = 1
      secrets     = {}
    }
    "ms-parity" = {
      args        = ["parity"]
      identity    = "ms-parity"
      memory      = "1Gi"
      timeout     = "3600s"
      max_retries = 0
      secrets     = {}
    }
  }

  # Which version of each source secret the jobs are given. Null means "none yet", and
  # then no job carries a reference to that secret.
  secret_version = {
    "steam-web-api-key"  = var.steam_web_api_key_version
    "igdb-client-id"     = var.igdb_client_id_version
    "igdb-client-secret" = var.igdb_client_secret_version
  }

  # The only jobs the dispatcher may start. It may start them; it cannot read what
  # they read.
  dispatched_jobs = [for name in keys(local.jobs) : name if name != "ms-dispatch"]
}

resource "google_cloud_run_v2_job" "this" {
  for_each = local.jobs

  project             = var.project_id
  location            = var.region
  name                = each.key
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account = local.sa[each.value.identity]
      timeout         = each.value.timeout
      max_retries     = each.value.max_retries

      containers {
        image = var.image
        args  = each.value.args

        resources {
          limits = {
            # Cloud Run jobs need at least one full vCPU.
            cpu    = "1"
            memory = each.value.memory
          }
        }

        env {
          name  = "METASPIKE_STAGE"
          value = "private_collection"
        }
        env {
          name  = "METASPIKE_PROJECT"
          value = var.project_id
        }
        env {
          name  = "METASPIKE_REGION"
          value = var.region
        }
        env {
          name  = "METASPIKE_IMAGE"
          value = var.image
        }

        # A reference to a secret, resolved by Cloud Run when the task starts. The value
        # is never in this configuration, the state, or the image. A reference exists
        # only for a secret whose exact version number has been supplied; until then the
        # job simply does not have it, and it never follows "latest".
        dynamic "env" {
          for_each = {
            for name, secret in each.value.secrets : name => secret
            if local.secret_version[secret] != null
          }
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.this[env.value].secret_id
                version = local.secret_version[env.value]
              }
            }
          }
        }
      }
    }
  }

  labels = {
    system = "metaspike"
  }

  depends_on = [google_secret_manager_secret_iam_member.reader]
}

# The scheduler may start the dispatcher, and only the dispatcher.
resource "google_cloud_run_v2_job_iam_member" "scheduler_runs_dispatch" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.this["ms-dispatch"].name
  role     = "roles/run.invoker"
  member   = local.member["ms-scheduler"]
}

# The dispatcher may start each approved downstream job. No job may start another.
resource "google_cloud_run_v2_job_iam_member" "dispatch_runs" {
  for_each = toset(local.dispatched_jobs)

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.this[each.value].name
  role     = "roles/run.invoker"
  member   = local.member["ms-dispatch"]
}

# --- The single trigger ---------------------------------------------------------------
# One scheduler job for the whole system. The dispatcher decides what is due; a daily
# source is not called 96 times a day because the dispatcher wakes 96 times a day.
# Pausing this one job stops all collection.

resource "google_cloud_scheduler_job" "tick" {
  project     = var.project_id
  region      = var.region
  name        = "ms-dispatch-15m"
  description = "Starts the dispatcher every 15 minutes. Pause to stop all collection."
  schedule    = "*/15 * * * *"
  time_zone   = "Etc/UTC"
  paused      = var.scheduler_paused

  attempt_deadline = "180s"

  # Delivery is at least once, so the dispatcher takes a per-slot lease and a second
  # delivery for the same slot exits without doing anything.
  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.this["ms-dispatch"].name}:run"

    oauth_token {
      service_account_email = local.sa["ms-scheduler"]
    }
  }
}
