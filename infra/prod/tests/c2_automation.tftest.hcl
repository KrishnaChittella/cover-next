# What Part C2-B deploys, and what it must leave alone. Runs entirely offline: the
# provider is mocked, nothing is planned against a real project and nothing is applied.
#
#   terraform init -backend=false && terraform test

mock_provider "google" {}

variables {
  project_id           = "test-project"
  image                = "us-central1-docker.pkg.dev/test-project/metaspike/metaspike@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  notification_channel = "projects/test-project/notificationChannels/1"
}

run "the_scheduler_is_hourly_and_paused_by_default" {
  command = plan

  assert {
    condition     = google_cloud_scheduler_job.tick.schedule == "0 * * * *"
    error_message = "The one trigger fires on the hour."
  }

  assert {
    condition     = google_cloud_scheduler_job.tick.paused == true
    error_message = "With no instruction otherwise the scheduler is planned paused."
  }

  assert {
    condition     = google_cloud_scheduler_job.tick.name == "ms-dispatch-15m"
    error_message = "The trigger keeps its name: a rename would destroy and recreate it."
  }

  assert {
    condition     = google_monitoring_alert_policy.dispatcher_silent.enabled == false
    error_message = "The silence alert is off while the scheduler is paused."
  }
}

run "only_the_wikimedia_job_is_under_work_orders" {
  command = plan

  assert {
    condition = [
      for e in google_cloud_run_v2_job.this["ms-wikimedia"].template[0].template[0].containers[0].env :
      e.value if e.name == "METASPIKE_WORK_ORDERS"
    ] == ["required"]
    error_message = "The Wikimedia collector acts only on a work order."
  }

  assert {
    condition = alltrue([
      for name, job in google_cloud_run_v2_job.this :
      length([for e in job.template[0].template[0].containers[0].env : e if e.name == "METASPIKE_WORK_ORDERS"]) == (name == "ms-wikimedia" ? 1 : 0)
    ])
    error_message = "No other job carries the work-order setting."
  }

  assert {
    condition = alltrue([
      for name, job in google_cloud_run_v2_job.this :
      length([for e in job.template[0].template[0].containers[0].env : e if length(e.value_source) > 0]) == 0
    ])
    error_message = "No job references a secret."
  }

  assert {
    condition     = length(google_cloud_run_v2_job.this) == 7
    error_message = "The same seven jobs, and no new one."
  }
}

run "the_work_order_grant_is_read_only_and_one_prefix" {
  command = plan

  assert {
    condition     = length(google_storage_bucket_iam_member.objects) == 27
    error_message = "One object grant is added to the 26 that existed."
  }

  assert {
    condition = length([
      for key, grant in google_storage_bucket_iam_member.objects : key
      if length(grant.condition) > 0 && strcontains(key, "dispatch/orders/")
    ]) == 1
    error_message = "Exactly one grant reaches the work-order area."
  }

  assert {
    condition = alltrue([
      for key, grant in google_storage_bucket_iam_member.objects :
      grant.role == "roles/storage.objectViewer" && grant.member == "serviceAccount:ms-wikimedia@test-project.iam.gserviceaccount.com" && endswith(grant.bucket, "-ops")
      if strcontains(key, "dispatch/orders/")
    ])
    error_message = "It is read-only, for the Wikimedia collector, on the operations bucket."
  }

  assert {
    condition = alltrue([
      for key, grant in google_storage_bucket_iam_member.objects :
      strcontains(grant.condition[0].expression, "/objects/dispatch/orders/wikimedia_pageviews/")
      if strcontains(key, "dispatch/orders/")
    ])
    error_message = "The condition names the Wikimedia order prefix and nothing wider."
  }
}

run "the_alert_policies_are_the_reviewed_eight" {
  command = plan

  assert {
    condition     = google_monitoring_alert_policy.warehouse_failed.conditions[0].condition_threshold[0].threshold_value == 0
    error_message = "Any warehouse failure in an hour alerts."
  }

  assert {
    condition     = google_monitoring_alert_policy.source_rate_limited.conditions[0].condition_threshold[0].threshold_value == 2
    error_message = "Three or more rate limits in an hour alert."
  }

  assert {
    condition     = google_monitoring_alert_policy.execution_volume.conditions[0].condition_threshold[0].aggregations[0].alignment_period == "86400s"
    error_message = "Execution volume is counted over a day."
  }

  assert {
    condition     = google_monitoring_alert_policy.landing_storage.conditions[0].condition_threshold[0].threshold_value == 5000000000
    error_message = "The landing bucket alerts above 5 GB."
  }

  assert {
    condition     = google_monitoring_alert_policy.dispatcher_silent.conditions[0].condition_absent[0].duration == "10800s"
    error_message = "Dispatcher silence is three hourly ticks."
  }

  assert {
    condition = alltrue([
      for policy in [
        google_monitoring_alert_policy.dispatcher_silent,
        google_monitoring_alert_policy.job_failures,
        google_monitoring_alert_policy.steam_refused,
        google_monitoring_alert_policy.parity_mismatch,
        google_monitoring_alert_policy.warehouse_failed,
        google_monitoring_alert_policy.source_rate_limited,
        google_monitoring_alert_policy.execution_volume,
        google_monitoring_alert_policy.landing_storage,
      ] : policy.notification_channels == tolist(["projects/test-project/notificationChannels/1"])
    ])
    error_message = "Every policy notifies the one proven channel."
  }
}

run "unpausing_is_explicit_and_turns_the_silence_alert_on" {
  command = plan

  variables {
    scheduler_paused = false
  }

  assert {
    condition     = google_cloud_scheduler_job.tick.paused == false && google_monitoring_alert_policy.dispatcher_silent.enabled == true
    error_message = "Only an explicit false unpauses, and the silence alert follows it."
  }
}
