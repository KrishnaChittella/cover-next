# What is watched. Isolated API errors are counted, not alerted on; alerts are for
# conditions that need a person. Every alert goes to the owner's notification channel.

locals {
  # Counters extracted from the jobs' structured logs. Each job writes one JSON line
  # per event with an `event` field.
  log_counters = {
    "ms_steam_refused_403" = {
      description = "Steam answered 401 or 403. Collection halts until cleared by hand."
      filter      = "jsonPayload.event=\"source_refused\" AND jsonPayload.source=\"steam_current_players\" AND jsonPayload.error_code=\"unauthorized\""
    }
    "ms_source_rate_limited" = {
      description = "A source answered 429."
      filter      = "jsonPayload.event=\"source_refused\" AND jsonPayload.error_code=\"rate_limited\""
    }
    "ms_slot_not_observed" = {
      description = "A sampling slot ended without a valid observation."
      filter      = "jsonPayload.event=\"slot_resolved\" AND jsonPayload.state!=\"observed\""
    }
    "ms_baseline_parity_mismatch" = {
      description = "A rebuilt baseline state differed from the persisted one."
      filter      = "jsonPayload.event=\"parity_mismatch\""
    }
    "ms_warehouse_failed" = {
      description = "A load or dbt run failed."
      filter      = "jsonPayload.event=\"warehouse_failed\""
    }
  }
}

resource "google_logging_metric" "counter" {
  for_each = local.log_counters

  project     = var.project_id
  name        = each.key
  description = each.value.description
  filter      = "resource.type=\"cloud_run_job\" AND ${each.value.filter}"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

# Health that does not depend on any payload: Cloud Run's own execution counts exist
# even when our code never starts.
# Three missed hourly ticks. Enabled only while the scheduler runs: while it is paused
# there is nothing to be silent about.
resource "google_monitoring_alert_policy" "dispatcher_silent" {
  project      = var.project_id
  display_name = "MetaSpike: dispatcher has not succeeded for 3 hours"
  combiner     = "OR"
  enabled      = !var.scheduler_paused

  conditions {
    display_name = "no successful ms-dispatch execution"
    condition_absent {
      filter   = "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"ms-dispatch\" AND metric.type=\"run.googleapis.com/job/completed_execution_count\" AND metric.labels.result=\"succeeded\""
      duration = "10800s"
      aggregations {
        alignment_period   = "3600s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = [var.notification_channel]
}

resource "google_monitoring_alert_policy" "job_failures" {
  project      = var.project_id
  display_name = "MetaSpike: repeated job failures"
  combiner     = "OR"

  conditions {
    display_name = "two or more failed executions of one job in an hour"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_job\" AND metric.type=\"run.googleapis.com/job/completed_execution_count\" AND metric.labels.result=\"failed\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "0s"
      aggregations {
        alignment_period     = "3600s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.labels.job_name"]
      }
    }
  }

  notification_channels = [var.notification_channel]
}

resource "google_monitoring_alert_policy" "steam_refused" {
  project      = var.project_id
  display_name = "MetaSpike: Steam refused the key"
  combiner     = "OR"

  conditions {
    display_name = "any 401/403 from Steam"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_job\" AND metric.type=\"logging.googleapis.com/user/${google_logging_metric.counter["ms_steam_refused_403"].name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "900s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = [var.notification_channel]
}

resource "google_monitoring_alert_policy" "parity_mismatch" {
  project      = var.project_id
  display_name = "MetaSpike: baseline state parity mismatch"
  combiner     = "OR"

  conditions {
    display_name = "any rebuilt state differs from the persisted state"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_job\" AND metric.type=\"logging.googleapis.com/user/${google_logging_metric.counter["ms_baseline_parity_mismatch"].name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "3600s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = [var.notification_channel]
}

# The warehouse runs a few times a day and a failure stops history from advancing.
resource "google_monitoring_alert_policy" "warehouse_failed" {
  project      = var.project_id
  display_name = "MetaSpike: warehouse load or build failed"
  combiner     = "OR"

  conditions {
    display_name = "any warehouse failure in an hour"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_job\" AND metric.type=\"logging.googleapis.com/user/${google_logging_metric.counter["ms_warehouse_failed"].name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "3600s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = [var.notification_channel]
}

# One rate limit is handled by the circuit breaker and logged. Three in an hour, over
# every job together, means the limits are wrong.
resource "google_monitoring_alert_policy" "source_rate_limited" {
  project      = var.project_id
  display_name = "MetaSpike: a source keeps rate limiting"
  combiner     = "OR"

  conditions {
    display_name = "three or more rate limits in an hour"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_job\" AND metric.type=\"logging.googleapis.com/user/${google_logging_metric.counter["ms_source_rate_limited"].name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 2
      duration        = "0s"
      aggregations {
        alignment_period     = "3600s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  notification_channels = [var.notification_channel]
}

# A cost guard. The planned day is about 26 executions; the dispatcher's own ceiling
# is 120. More than 60 in a day, over every job together, is not the plan.
resource "google_monitoring_alert_policy" "execution_volume" {
  project      = var.project_id
  display_name = "MetaSpike: unusually many job executions in a day"
  combiner     = "OR"

  conditions {
    display_name = "more than 60 completed executions in 24 hours"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_job\" AND metric.type=\"run.googleapis.com/job/completed_execution_count\""
      comparison      = "COMPARISON_GT"
      threshold_value = 60
      duration        = "0s"
      aggregations {
        alignment_period     = "86400s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  notification_channels = [var.notification_channel]
}

# Everything stored in the landing bucket, live and soft-deleted, in every class. The
# figure is measured about once a day, so this is a slow guard and not a fast one.
resource "google_monitoring_alert_policy" "landing_storage" {
  project      = var.project_id
  display_name = "MetaSpike: landing bucket above 5 GB"
  combiner     = "OR"

  conditions {
    display_name = "landing bucket holds more than 5 GB"
    condition_threshold {
      filter          = "resource.type=\"gcs_bucket\" AND resource.labels.bucket_name=\"${google_storage_bucket.data["landing"].name}\" AND metric.type=\"storage.googleapis.com/storage/v2/total_bytes\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5000000000
      duration        = "0s"
      aggregations {
        alignment_period     = "3600s"
        per_series_aligner   = "ALIGN_MEAN"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.labels.bucket_name"]
      }
    }
  }

  notification_channels = [var.notification_channel]
}

# Not built: "no successful Wikimedia collection in 36 hours". Cloud Monitoring cannot
# say it without a metric of our own. An absence condition is too short for a job that
# succeeds once a day, and an alignment window is at most 25 hours. It is recorded as
# a limitation in docs/phase4-c2-plan.md, not approximated here.

