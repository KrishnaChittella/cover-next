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
}

# Jobs pull their image with the Cloud Run service agent; runtime identities need no
# registry access at all, so none is granted.
