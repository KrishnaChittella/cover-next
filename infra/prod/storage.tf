# Private regional buckets. Uniform access, public access prevention, no ACLs.
#
# No bucket has a retention policy or a lock. Steam Data may have to be deleted if
# Valve requires it or when licensed use ends, so nothing may make deletion impossible.
# Runtime identities cannot delete landed data because they are never granted a role
# that includes deletion on it, which is immutability without a lock.

locals {
  buckets = {
    # Normalised, append-only batches from every collector. The replayable log.
    landing = { soft_delete_seconds = 604800, nearline_after_days = null }
    # Raw responses, only for sources whose policy allows raw storage.
    raw = { soft_delete_seconds = 604800, nearline_after_days = 30 }
    # Baseline state checkpoints and catalogue snapshots. A rebuildable cache.
    state = { soft_delete_seconds = 0, nearline_after_days = null }
    # Leases, quota ledgers, heartbeats and health. Small and overwritten.
    ops = { soft_delete_seconds = 0, nearline_after_days = null }
    # The renewable IGDB OAuth token cache. One identity only.
    tokens = { soft_delete_seconds = 0, nearline_after_days = null }
  }
}

resource "google_storage_bucket" "data" {
  for_each = local.buckets

  name     = local.bucket[each.key]
  project  = var.project_id
  location = var.region

  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  soft_delete_policy {
    retention_duration_seconds = each.value.soft_delete_seconds
  }

  dynamic "lifecycle_rule" {
    for_each = each.value.nearline_after_days == null ? [] : [each.value.nearline_after_days]
    content {
      condition {
        age = lifecycle_rule.value
      }
      action {
        type          = "SetStorageClass"
        storage_class = "NEARLINE"
      }
    }
  }

  labels = {
    system = "metaspike"
    data   = each.key
  }
}

# --- Who may touch which objects ------------------------------------------------------
# Grants are per bucket and, with an IAM condition, per object prefix. A condition on
# resource.name constrains object reads and writes; it cannot constrain listing, which
# is why collectors are given create (not list) on landing.

locals {
  # identity, bucket, role, object prefix ("" means the whole bucket)
  object_grants = [
    # Dispatcher: reads operational state, writes only its own.
    ["ms-dispatch", "ops", "roles/storage.objectViewer", ""],
    ["ms-dispatch", "ops", "roles/storage.objectUser", "dispatch/"],

    # Steam players: its own landing and ops paths, the shared Steam quota ledger,
    # and read-only catalogue state to find its targets.
    ["ms-steam-players", "landing", "roles/storage.objectCreator", "steam_current_players/"],
    ["ms-steam-players", "ops", "roles/storage.objectUser", "steam_current_players/"],
    ["ms-steam-players", "ops", "roles/storage.objectUser", "quota/steam_web_api/"],
    ["ms-steam-players", "state", "roles/storage.objectViewer", "catalogue/"],

    # Wikimedia: raw storage is allowed for this source.
    ["ms-wikimedia", "landing", "roles/storage.objectCreator", "wikimedia_pageviews/"],
    ["ms-wikimedia", "raw", "roles/storage.objectCreator", "wikimedia_pageviews/"],
    ["ms-wikimedia", "ops", "roles/storage.objectUser", "wikimedia_pageviews/"],
    ["ms-wikimedia", "state", "roles/storage.objectViewer", "catalogue/"],

    # Catalogue: IGDB and Steam app list, the token cache, and catalogue state.
    ["ms-catalogue", "landing", "roles/storage.objectCreator", "igdb/"],
    ["ms-catalogue", "landing", "roles/storage.objectCreator", "steam_catalogue/"],
    ["ms-catalogue", "raw", "roles/storage.objectCreator", "igdb/"],
    ["ms-catalogue", "ops", "roles/storage.objectUser", "catalogue/"],
    ["ms-catalogue", "ops", "roles/storage.objectUser", "quota/steam_web_api/"],
    ["ms-catalogue", "state", "roles/storage.objectUser", "catalogue/"],
    ["ms-catalogue", "tokens", "roles/storage.objectUser", ""],

    # Warehouse: reads what was landed, never writes there.
    ["ms-warehouse", "landing", "roles/storage.objectViewer", ""],
    ["ms-warehouse", "raw", "roles/storage.objectViewer", ""],
    ["ms-warehouse", "ops", "roles/storage.objectUser", "warehouse/"],

    # Features: owns baseline state.
    ["ms-features", "state", "roles/storage.objectUser", "baseline/"],
    ["ms-features", "ops", "roles/storage.objectUser", "features/"],

    # Parity: reads state, never writes it.
    ["ms-parity", "state", "roles/storage.objectViewer", "baseline/"],
    ["ms-parity", "ops", "roles/storage.objectUser", "parity/"],
  ]
}

resource "google_storage_bucket_iam_member" "objects" {
  for_each = {
    for grant in local.object_grants :
    "${grant[0]}:${grant[1]}:${grant[2]}:${grant[3]}" => grant
  }

  bucket = google_storage_bucket.data[each.value[1]].name
  role   = each.value[2]
  member = local.member[each.value[0]]

  dynamic "condition" {
    for_each = each.value[3] == "" ? [] : [each.value[3]]
    content {
      title       = "prefix-${replace(trimsuffix(condition.value, "/"), "/", "-")}"
      description = "Only objects under ${condition.value}"
      expression  = "resource.name.startsWith(\"projects/_/buckets/${local.bucket[each.value[1]]}/objects/${condition.value}\")"
    }
  }
}
