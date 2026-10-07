# Regional us-central1 datasets. NOT the US multi-region: a region and a multi-region
# are different locations, and only a regional dataset keeps loads from the regional
# buckets free of transfer charges and keeps the data in one place.
#
# Terraform declares the datasets and the append-only tables the loader fills. dbt
# creates its own models in ms_staging and ms_marts.
#
# Dataset access is AUTHORITATIVE: each dataset's whole policy is stated below and
# anything else is removed on apply. BigQuery adds the creating identity as OWNER and
# three project-wide default entries to every new dataset; none of those is part of
# the reviewed design, and the policy resource is what keeps them from coming back.

locals {
  datasets = {
    ms_raw       = "Append-only loads from landed files. Authoritative history."
    ms_catalogue = "Catalogue events: bindings, decisions, quarantine, lifecycle."
    ms_staging   = "dbt staging models (Part B)."
    ms_marts     = "dbt marts: resolved slots, coverage, health (Part B)."
    ms_features  = "Feature engine output. Internal; nothing here is publishable."
    ms_ops       = "Run log, source health, quota usage, parity results."
  }

  # identity, dataset, role
  dataset_grants = [
    ["ms-warehouse", "ms_raw", "roles/bigquery.dataEditor"],
    ["ms-warehouse", "ms_catalogue", "roles/bigquery.dataEditor"],
    ["ms-warehouse", "ms_staging", "roles/bigquery.dataEditor"],
    ["ms-warehouse", "ms_marts", "roles/bigquery.dataEditor"],
    ["ms-warehouse", "ms_ops", "roles/bigquery.dataEditor"],
    ["ms-features", "ms_raw", "roles/bigquery.dataViewer"],
    ["ms-features", "ms_marts", "roles/bigquery.dataViewer"],
    ["ms-features", "ms_catalogue", "roles/bigquery.dataViewer"],
    ["ms-features", "ms_features", "roles/bigquery.dataEditor"],
    ["ms-parity", "ms_raw", "roles/bigquery.dataViewer"],
    ["ms-parity", "ms_marts", "roles/bigquery.dataViewer"],
    ["ms-parity", "ms_catalogue", "roles/bigquery.dataViewer"],
    ["ms-parity", "ms_features", "roles/bigquery.dataViewer"],
    ["ms-parity", "ms_ops", "roles/bigquery.dataEditor"],
  ]

  # table => dataset, partition column, cluster columns, schema file
  fact_tables = {
    sample_attempts = {
      dataset   = "ms_raw"
      partition = "slot_start"
      cluster   = ["source", "metric", "source_native_id", "slot_start"]
    }
    period_observations = {
      dataset   = "ms_raw"
      partition = "period_start"
      cluster   = ["source", "metric", "source_identifier"]
    }
    source_records = {
      dataset   = "ms_raw"
      partition = "fetched_at"
      cluster   = ["source", "source_id"]
    }
    binding_events = {
      dataset   = "ms_catalogue"
      partition = "occurred_at"
      cluster   = ["source", "source_id"]
    }
    resolution_decisions = {
      dataset   = "ms_catalogue"
      partition = "decided_at"
      cluster   = ["record_source", "record_source_id"]
    }
    quarantine_events = {
      dataset   = "ms_catalogue"
      partition = "occurred_at"
      cluster   = ["record_source", "record_source_id"]
    }
    bucket_evaluations = {
      dataset   = "ms_features"
      partition = "bucket_start"
      cluster   = ["rule_version", "source", "subject"]
    }
    collection_runs = {
      dataset   = "ms_ops"
      partition = "started_at"
      cluster   = ["job", "source"]
    }
    # What the dispatcher intended to be collected, slot by slot. An unsampled slot is
    # reconstructed from this on read; it is never stored as a row anywhere.
    expected_targets = {
      dataset   = "ms_ops"
      partition = "slot_start"
      cluster   = ["source", "metric", "source_native_id"]
    }
    # One row per source-native id that has ever had an attempt. Written by the loader.
    sample_native_ids = {
      dataset   = "ms_raw"
      partition = "first_slot_start"
      cluster   = ["source", "metric"]
    }
    # Derived catalogue history about canonical entities. Not source data.
    entity_events = {
      dataset   = "ms_catalogue"
      partition = "occurred_at"
      cluster   = ["canonical_game_id"]
    }
    lifecycle_events = {
      dataset   = "ms_catalogue"
      partition = "occurred_at"
      cluster   = ["canonical_game_id"]
    }
  }

  # Every role held on each dataset, and by whom. This is the whole policy.
  #   - `projectOwners` is BigQuery's own special group for a dataset: whoever owns
  #     the project owns the dataset. It keeps every dataset recoverable and
  #     manageable by a person, and BigQuery requires a dataset to have an owner. It
  #     is written exactly so: the provider maps the bare word to the dataset's
  #     special group and refuses the IAM-style "projectOwner:<project>".
  #   - The deploy identity is NOT listed: it manages tables through its reviewed
  #     project-level role, and needs no dataset-level ownership on top of it.
  #   - `projectWriters` and `projectReaders` are NOT listed.
  dataset_policy = {
    for dataset in keys(local.datasets) : dataset => merge(
      { "roles/bigquery.dataOwner" = ["projectOwners"] },
      {
        for role in distinct([for g in local.dataset_grants : g[2] if g[1] == dataset]) :
        role => sort([
          for g in local.dataset_grants : local.member[g[0]] if g[1] == dataset && g[2] == role
        ])
      },
    )
  }
}

resource "google_bigquery_dataset" "this" {
  for_each = local.datasets

  project     = var.project_id
  dataset_id  = each.key
  location    = var.region
  description = each.value

  delete_contents_on_destroy = false

  labels = {
    system = "metaspike"
  }
}

resource "google_bigquery_dataset_iam_policy" "this" {
  for_each = local.dataset_policy

  project    = var.project_id
  dataset_id = google_bigquery_dataset.this[each.key].dataset_id

  # The policy document, written out in full. Nothing is merged with what exists.
  policy_data = jsonencode({
    bindings = [
      for role in sort(keys(each.value)) : {
        role    = role
        members = each.value[role]
      }
    ]
  })
}

# The per-member grants this policy replaces. They are forgotten, not destroyed:
# destroying one would remove its binding from a dataset whose policy has just been
# written, in whatever order Terraform happened to run the two.
removed {
  from = google_bigquery_dataset_iam_member.access

  lifecycle {
    destroy = false
  }
}

resource "google_bigquery_table" "fact" {
  for_each = local.fact_tables

  project    = var.project_id
  dataset_id = google_bigquery_dataset.this[each.value.dataset].dataset_id
  table_id   = each.key

  deletion_protection = true
  schema              = file("${path.module}/schemas/${each.key}.json")
  clustering          = each.value.cluster

  # A query that does not name a time range is refused instead of scanning everything.
  require_partition_filter = true

  time_partitioning {
    type  = "DAY"
    field = each.value.partition
  }

  labels = {
    system = "metaspike"
  }
}
