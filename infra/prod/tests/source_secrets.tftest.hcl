# Source secret references on the Cloud Run jobs. Runs entirely offline: the provider
# is mocked, nothing is planned against a real project and nothing is applied.
#
#   terraform init -backend=false && terraform test

mock_provider "google" {}

variables {
  project_id           = "test-project"
  image                = "us-central1-docker.pkg.dev/test-project/metaspike/metaspike@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  notification_channel = "projects/test-project/notificationChannels/1"
}

run "default_has_no_source_secret_reference_anywhere" {
  command = plan

  assert {
    condition = alltrue([
      for name, job in google_cloud_run_v2_job.this :
      length([for e in job.template[0].template[0].containers[0].env : e if length(e.value_source) > 0]) == 0
    ])
    error_message = "With no secret version supplied, no job may reference any secret."
  }

  assert {
    condition     = length(google_cloud_run_v2_job.this) == 7
    error_message = "All seven jobs are still planned."
  }

  assert {
    condition     = join(",", sort(keys(google_secret_manager_secret.this))) == "igdb-client-id,igdb-client-secret,steam-web-api-key"
    error_message = "The three secret containers are still planned."
  }

  assert {
    condition = join(" ", sort(keys(google_secret_manager_secret_iam_member.reader))) == join(" ", [
      "ms-catalogue:igdb-client-id",
      "ms-catalogue:igdb-client-secret",
      "ms-catalogue:steam-web-api-key",
      "ms-steam-players:steam-web-api-key",
    ])
    error_message = "The reviewed reader bindings remain, ready for when versions exist."
  }

  assert {
    condition     = google_cloud_scheduler_job.tick.paused == true
    error_message = "The scheduler is paused by default."
  }
}

run "steam_version_reaches_only_the_jobs_that_use_steam" {
  command = plan

  variables {
    steam_web_api_key_version = "3"
  }

  assert {
    condition = join(",", [
      for e in google_cloud_run_v2_job.this["ms-steam-players"].template[0].template[0].containers[0].env :
      "${e.name}=${e.value_source[0].secret_key_ref[0].secret}@${e.value_source[0].secret_key_ref[0].version}"
      if length(e.value_source) > 0
    ]) == "METASPIKE_STEAM_WEB_API_KEY=steam-web-api-key@3"
    error_message = "ms-steam-players gets the Steam key at exactly the version given."
  }

  assert {
    condition = join(",", [
      for e in google_cloud_run_v2_job.this["ms-catalogue"].template[0].template[0].containers[0].env :
      e.name if length(e.value_source) > 0
    ]) == "METASPIKE_STEAM_WEB_API_KEY"
    error_message = "ms-catalogue gets the Steam key and, with no IGDB versions given, nothing else."
  }

  assert {
    condition = alltrue([
      for name in ["ms-dispatch", "ms-wikimedia", "ms-warehouse", "ms-features", "ms-parity"] :
      length([for e in google_cloud_run_v2_job.this[name].template[0].template[0].containers[0].env : e if length(e.value_source) > 0]) == 0
    ])
    error_message = "No other job ever references a secret."
  }
}

run "catalogue_gets_only_the_individually_configured_references" {
  command = plan

  variables {
    igdb_client_id_version = "12"
  }

  assert {
    condition = join(",", [
      for e in google_cloud_run_v2_job.this["ms-catalogue"].template[0].template[0].containers[0].env :
      "${e.name}=${e.value_source[0].secret_key_ref[0].secret}@${e.value_source[0].secret_key_ref[0].version}"
      if length(e.value_source) > 0
    ]) == "METASPIKE_IGDB_CLIENT_ID=igdb-client-id@12"
    error_message = "Only the one configured IGDB reference appears."
  }

  assert {
    condition = length([
      for e in google_cloud_run_v2_job.this["ms-steam-players"].template[0].template[0].containers[0].env : e if length(e.value_source) > 0
    ]) == 0
    error_message = "An IGDB version gives the Steam job nothing."
  }
}

run "all_three_versions_configured" {
  command = plan

  variables {
    steam_web_api_key_version  = "1"
    igdb_client_id_version     = "2"
    igdb_client_secret_version = "40"
  }

  assert {
    condition = join(",", sort([
      for e in google_cloud_run_v2_job.this["ms-catalogue"].template[0].template[0].containers[0].env :
      "${e.value_source[0].secret_key_ref[0].secret}@${e.value_source[0].secret_key_ref[0].version}"
      if length(e.value_source) > 0
    ])) == "igdb-client-id@2,igdb-client-secret@40,steam-web-api-key@1"
    error_message = "Each reference carries its own explicit version."
  }
}

run "latest_is_refused_for_steam" {
  command = plan

  variables {
    steam_web_api_key_version = "latest"
  }

  expect_failures = [var.steam_web_api_key_version]
}

run "latest_is_refused_for_igdb_client_id" {
  command = plan

  variables {
    igdb_client_id_version = "latest"
  }

  expect_failures = [var.igdb_client_id_version]
}

run "a_value_or_a_non_positive_number_is_refused" {
  command = plan

  variables {
    steam_web_api_key_version  = "0"
    igdb_client_id_version     = "0123456789ABCDEF0123456789ABCDEF"
    igdb_client_secret_version = "-1"
  }

  expect_failures = [
    var.steam_web_api_key_version,
    var.igdb_client_id_version,
    var.igdb_client_secret_version,
  ]
}
