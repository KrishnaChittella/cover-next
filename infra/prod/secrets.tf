# Secret CONTAINERS only. No secret version, and therefore no secret value, is ever
# created by Terraform: a value in a resource would be written to Terraform state.
# The owner adds each value once, from standard input (see docs/phase4-runbook.md).
#
# The short-lived IGDB OAuth access token is NOT here. It is minted at runtime from the
# client id and secret and cached in the tokens bucket by the one identity that uses it.

locals {
  secrets = {
    "steam-web-api-key"  = ["ms-steam-players", "ms-catalogue"]
    "igdb-client-id"     = ["ms-catalogue"]
    "igdb-client-secret" = ["ms-catalogue"]
  }

  secret_readers = flatten([
    for secret, identities in local.secrets : [
      for identity in identities : { secret = secret, identity = identity }
    ]
  ])
}

resource "google_secret_manager_secret" "this" {
  for_each = local.secrets

  project   = var.project_id
  secret_id = each.key

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  labels = {
    system = "metaspike"
  }
}

# The dispatcher, the Wikimedia collector, the warehouse, features and parity jobs, the
# scheduler and the deploy identity appear nowhere below: none of them can read a secret.
resource "google_secret_manager_secret_iam_member" "reader" {
  for_each = { for grant in local.secret_readers : "${grant.identity}:${grant.secret}" => grant }

  project   = var.project_id
  secret_id = google_secret_manager_secret.this[each.value.secret].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.member[each.value.identity]
}
