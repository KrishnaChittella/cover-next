# Regional us-central1 datasets. NOT the US multi-region: a region and a multi-region
# are different locations, and only a regional dataset keeps loads from the regional
# buckets free of transfer charges and keeps the data in one place.
#
# Part A declares the datasets and the append-only fact tables whose shape is already
# fixed by accepted contracts. dbt models (staging, marts) belong to Part B.

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

resource "google_bigquery_dataset_iam_member" "access" {
  for_each = {
    for grant in local.dataset_grants : "${grant[0]}:${grant[1]}:${grant[2]}" => grant
  }

  project    = var.project_id
  dataset_id = google_bigquery_dataset.this[each.value[1]].dataset_id
  role       = each.value[2]
  member     = local.member[each.value[0]]
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
