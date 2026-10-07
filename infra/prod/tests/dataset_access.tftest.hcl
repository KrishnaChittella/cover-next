# BigQuery dataset access is authoritative and holds only the reviewed entries. Runs
# entirely offline: the provider is mocked and nothing is planned against a project.
#
#   terraform init -backend=false && terraform test

mock_provider "google" {}

variables {
  project_id           = "test-project"
  image                = "us-central1-docker.pkg.dev/test-project/metaspike/metaspike@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  notification_channel = "projects/test-project/notificationChannels/1"
}

run "every_dataset_has_one_whole_policy" {
  command = plan

  assert {
    condition     = join(",", sort(keys(google_bigquery_dataset_iam_policy.this))) == "ms_catalogue,ms_features,ms_marts,ms_ops,ms_raw,ms_staging"
    error_message = "Each of the six datasets has exactly one authoritative policy."
  }

  assert {
    condition = alltrue([
      for name, policy in google_bigquery_dataset_iam_policy.this :
      contains(
        [for b in jsondecode(policy.policy_data).bindings : b.role if contains(b.members, "projectOwner:test-project")],
        "roles/bigquery.dataOwner"
      )
    ])
    error_message = "Project owners keep ownership of every dataset."
  }

  assert {
    condition = alltrue(flatten([
      for name, policy in google_bigquery_dataset_iam_policy.this : [
        for b in jsondecode(policy.policy_data).bindings : [
          for m in b.members :
          m == "projectOwner:test-project" || can(regex("^serviceAccount:ms-(warehouse|features|parity)@test-project\\.iam\\.gserviceaccount\\.com$", m))
        ]
      ]
    ]))
    error_message = "Only project owners and the three warehouse-facing runtime identities may appear. No deploy identity, no project editors or viewers, no default entry."
  }

  assert {
    condition = alltrue(flatten([
      for name, policy in google_bigquery_dataset_iam_policy.this : [
        for b in jsondecode(policy.policy_data).bindings :
        b.role == "roles/bigquery.dataOwner" ? length(b.members) == 1 : true
      ]
    ]))
    error_message = "Nothing but the project owners owns a dataset: no creator entry, no service account."
  }

  assert {
    condition = alltrue(flatten([
      for name, policy in google_bigquery_dataset_iam_policy.this : [
        for b in jsondecode(policy.policy_data).bindings :
        contains(["roles/bigquery.dataOwner", "roles/bigquery.dataEditor", "roles/bigquery.dataViewer"], b.role)
      ]
    ]))
    error_message = "Only the three dataset data roles are used."
  }

  assert {
    condition = sort([
      for b in jsondecode(google_bigquery_dataset_iam_policy.this["ms_features"].policy_data).bindings :
      join("|", concat([b.role], b.members))
      ]) == sort([
      "roles/bigquery.dataEditor|serviceAccount:ms-features@test-project.iam.gserviceaccount.com",
      "roles/bigquery.dataOwner|projectOwner:test-project",
      "roles/bigquery.dataViewer|serviceAccount:ms-parity@test-project.iam.gserviceaccount.com",
    ])
    error_message = "ms_features: features writes, parity reads, project owners own. Nothing else."
  }

  assert {
    condition     = length(google_bigquery_table.fact) == 12
    error_message = "Eight original tables plus the four added in Part C1."
  }
}
