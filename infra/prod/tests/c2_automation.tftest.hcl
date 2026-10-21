# What Part C2-B deploys, and what it must leave alone. Runs entirely offline: the
# provider is mocked, nothing is planned against a real project and nothing is applied.
#
#   terraform init -backend=false && terraform test

mock_provider "google" {}

# The adopted YouTube secret container is imported, and a mock provider cannot import.
override_resource {
  target = google_secret_manager_secret.this["youtube-data-api-key"]
}

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

run "the_alert_policies_are_the_reviewed_ten" {
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
        google_monitoring_alert_policy.scheduler_delivery_failed,
        google_monitoring_alert_policy.dispatcher_failed,
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

run "unpausing_is_refused_without_a_finite_window" {
  command = plan

  variables {
    scheduler_paused = false
  }

  expect_failures = [google_cloud_scheduler_job.tick]
}

run "a_finite_soak_fires_hourly_on_the_named_days_only" {
  command = plan

  variables {
    scheduler_paused             = false
    soak_window                  = { month = 10, start_day = 12, end_day = 18 }
    soak_daily_execution_ceiling = 27
  }

  assert {
    condition     = google_cloud_scheduler_job.tick.schedule == "0 * 12-18 10 *"
    error_message = "Hourly, on the window's days of the window's month, and nothing else."
  }

  assert {
    condition     = google_cloud_scheduler_job.tick.paused == false && google_cloud_scheduler_job.tick.time_zone == "Etc/UTC"
    error_message = "The window is in UTC and the scheduler is unpaused only with it."
  }

  assert {
    condition     = output.dispatch_schedule == "0 * 12-18 10 *" && output.soak_scheduled_ticks == 168
    error_message = "The plan shows the cron and the number of ticks it holds: 7 days of 24."
  }

  assert {
    condition     = google_monitoring_alert_policy.dispatcher_silent.enabled == false
    error_message = "The absence alert stays off for a finite soak: silence before and after is the plan."
  }

  assert {
    condition     = google_monitoring_alert_policy.execution_volume.conditions[0].condition_threshold[0].threshold_value == 27
    error_message = "The volume alert carries the soak's own daily ceiling."
  }

  assert {
    condition     = output.daily_execution_alert_threshold == 27
    error_message = "The plan shows the threshold."
  }
}

run "a_single_day_window_names_one_day" {
  command = plan

  variables {
    scheduler_paused             = false
    soak_window                  = { month = 2, start_day = 28, end_day = 28 }
    soak_daily_execution_ceiling = 24
  }

  assert {
    condition     = google_cloud_scheduler_job.tick.schedule == "0 * 28 2 *" && output.soak_scheduled_ticks == 24
    error_message = "One day is one day of 24 ticks."
  }
}

run "a_window_that_ends_before_it_starts_is_refused" {
  command = plan

  variables {
    scheduler_paused             = false
    soak_window                  = { month = 10, start_day = 18, end_day = 12 }
    soak_daily_execution_ceiling = 27
  }

  expect_failures = [var.soak_window]
}

run "a_window_that_runs_past_the_month_is_refused" {
  command = plan

  variables {
    scheduler_paused             = false
    soak_window                  = { month = 11, start_day = 28, end_day = 31 }
    soak_daily_execution_ceiling = 27
  }

  expect_failures = [var.soak_window]
}

run "february_is_28_days_in_every_year" {
  command = plan

  variables {
    scheduler_paused             = false
    soak_window                  = { month = 2, start_day = 23, end_day = 29 }
    soak_daily_execution_ceiling = 27
  }

  expect_failures = [var.soak_window]
}

run "a_month_that_does_not_exist_is_refused" {
  command = plan

  variables {
    scheduler_paused             = false
    soak_window                  = { month = 13, start_day = 1, end_day = 7 }
    soak_daily_execution_ceiling = 27
  }

  expect_failures = [var.soak_window]
}

run "a_finite_soak_needs_its_daily_ceiling" {
  command = plan

  variables {
    scheduler_paused = false
    soak_window      = { month = 10, start_day = 12, end_day = 18 }
  }

  expect_failures = [google_cloud_scheduler_job.tick]
}

run "a_daily_ceiling_below_the_ticks_alone_is_refused" {
  command = plan

  variables {
    scheduler_paused             = false
    soak_window                  = { month = 10, start_day = 12, end_day = 18 }
    soak_daily_execution_ceiling = 23
  }

  expect_failures = [var.soak_daily_execution_ceiling]
}

run "a_paused_scheduler_carries_no_window" {
  command = plan

  variables {
    scheduler_paused             = true
    soak_window                  = { month = 10, start_day = 12, end_day = 18 }
    soak_daily_execution_ceiling = 27
  }

  expect_failures = [google_cloud_scheduler_job.tick]
}

run "steady_state_is_separate_and_takes_no_window" {
  command = plan

  variables {
    scheduler_paused             = false
    schedule_mode                = "steady_state"
    soak_window                  = { month = 10, start_day = 12, end_day = 18 }
    soak_daily_execution_ceiling = 27
  }

  expect_failures = [google_cloud_scheduler_job.tick]
}

run "steady_state_is_the_unbounded_schedule_with_the_silence_alert" {
  command = plan

  variables {
    scheduler_paused = false
    schedule_mode    = "steady_state"
  }

  assert {
    condition     = google_cloud_scheduler_job.tick.schedule == "0 * * * *" && google_cloud_scheduler_job.tick.paused == false
    error_message = "Only an explicit steady_state unpauses the unbounded schedule."
  }

  assert {
    condition     = google_monitoring_alert_policy.dispatcher_silent.enabled == true
    error_message = "With no end to the schedule, silence is a fault and the alert is on."
  }

  assert {
    condition     = google_monitoring_alert_policy.execution_volume.conditions[0].condition_threshold[0].threshold_value == 60
    error_message = "Steady state keeps the guard of 60 a day."
  }

  assert {
    condition     = output.soak_scheduled_ticks == null
    error_message = "There is no tick count for a schedule without an end."
  }
}

run "the_failure_alerts_are_on_in_every_mode" {
  command = plan

  assert {
    condition = alltrue([
      google_monitoring_alert_policy.scheduler_delivery_failed.enabled != false,
      google_monitoring_alert_policy.dispatcher_failed.enabled != false,
      google_monitoring_alert_policy.job_failures.enabled != false,
      google_monitoring_alert_policy.warehouse_failed.enabled != false,
      google_monitoring_alert_policy.source_rate_limited.enabled != false,
    ])
    error_message = "No failure alert is switched off by the schedule mode or by a pause."
  }

  assert {
    condition     = google_monitoring_alert_policy.dispatcher_failed.conditions[0].condition_threshold[0].threshold_value == 0 && google_monitoring_alert_policy.scheduler_delivery_failed.conditions[0].condition_threshold[0].threshold_value == 0
    error_message = "One failed dispatcher, or one failed scheduler attempt, in an hour alerts."
  }

  assert {
    condition     = google_logging_metric.counter["ms_scheduler_attempt_failed"].filter == "resource.type=\"cloud_scheduler_job\" AND severity>=ERROR"
    error_message = "The scheduler counter reads the scheduler's own error log entries."
  }

  assert {
    condition     = google_logging_metric.counter["ms_warehouse_failed"].filter == "resource.type=\"cloud_run_job\" AND jsonPayload.event=\"warehouse_failed\""
    error_message = "The job counters still read job logs."
  }

  assert {
    condition     = google_monitoring_alert_policy.execution_volume.conditions[0].condition_threshold[0].threshold_value == 60 && google_cloud_scheduler_job.tick.schedule == "0 * * * *"
    error_message = "Paused, the schedule and the volume guard are the plain ones."
  }
}
