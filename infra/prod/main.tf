# MetaSpike Production: private collection infrastructure. Applied through CI only.
#
# Nothing here is public: every bucket enforces public access prevention, no IAM
# binding names allUsers or allAuthenticatedUsers, and Cloud Run jobs have no URL.
# No secret value appears here; only empty secret containers and who may read them.

locals {
  # Runtime identities are created by the bootstrap root. They are referenced by their
  # fixed names so this root needs no permission over project-level IAM.
  sa = {
    for name in [
      "ms-scheduler", "ms-dispatch", "ms-steam-players", "ms-wikimedia",
      "ms-catalogue", "ms-warehouse", "ms-features", "ms-parity",
    ] : name => "${name}@${var.project_id}.iam.gserviceaccount.com"
  }
  member = { for name, email in local.sa : name => "serviceAccount:${email}" }

  bucket = { for name in keys(local.buckets) : name => "${var.project_id}-${name}" }
}
