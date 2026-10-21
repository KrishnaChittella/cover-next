# BigQuery dataset access is authoritative and holds only the reviewed entries. Runs
# entirely offline: the provider is mocked and nothing is planned against a project.
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

run "every_dataset_has_one_whole_policy" {
  command = plan

  assert {
    condition     = join(",", sort(keys(google_bigquery_dataset_iam_policy.this))) == "ms_catalogue,ms_features,ms_marts,ms_ops,ms_raw,ms_staging"
    error_message = "Each of the six datasets has exactly one authoritative policy."
  }

  # 1. Every dataset has projectOwners, and only projectOwners, as its owner.
  assert {
    condition = alltrue([
      for name, policy in google_bigquery_dataset_iam_policy.this :
      [for b in jsondecode(policy.policy_data).bindings : join(",", b.members) if b.role == "roles/bigquery.dataOwner"] == ["projectOwners"]
    ])
    error_message = "Every dataset is owned by the BigQuery special group projectOwners and by nothing else."
  }

  # 2. The IAM-style spelling is never used: the provider cannot map it to a dataset.
  assert {
    condition = alltrue(flatten([
      for name, policy in google_bigquery_dataset_iam_policy.this : [
        for b in jsondecode(policy.policy_data).bindings : [for m in b.members : !startswith(m, "projectOwner:")]
      ]
    ]))
    error_message = "No dataset policy may contain a member beginning 'projectOwner:'."
  }

  # 3 and 4. The default reader and writer groups are absent.
  assert {
    condition = alltrue(flatten([
      for name, policy in google_bigquery_dataset_iam_policy.this : [
        for b in jsondecode(policy.policy_data).bindings : [
          for m in b.members : !contains(["projectReaders", "projectWriters", "allUsers", "allAuthenticatedUsers"], m)
        ]
      ]
    ]))
    error_message = "projectReaders, projectWriters and public members are absent from every dataset."
  }

  # 5. The deploy identity holds nothing at dataset level.
  assert {
    condition = alltrue(flatten([
      for name, policy in google_bigquery_dataset_iam_policy.this : [
        for b in jsondecode(policy.policy_data).bindings : [for m in b.members : !strcontains(m, "ms-deploy")]
      ]
    ]))
    error_message = "ms-deploy is absent from dataset-level IAM."
  }

  assert {
    condition = alltrue(flatten([
      for name, policy in google_bigquery_dataset_iam_policy.this : [
        for b in jsondecode(policy.policy_data).bindings : [
          for m in b.members :
          m == "projectOwners" || can(regex("^serviceAccount:ms-(warehouse|features|parity)@test-project\\.iam\\.gserviceaccount\\.com$", m))
        ]
      ]
    ]))
    error_message = "Only projectOwners and the three warehouse-facing runtime identities may appear."
  }

  # 6. The 14 approved runtime grants, exactly: dataset | role | identity.
  assert {
    condition = sort(flatten([
      for name, policy in google_bigquery_dataset_iam_policy.this : [
        for b in jsondecode(policy.policy_data).bindings : [
          for m in b.members : "${name}|${b.role}|${m}" if m != "projectOwners"
        ]
      ]
      ])) == sort([
      "ms_catalogue|roles/bigquery.dataEditor|serviceAccount:ms-warehouse@test-project.iam.gserviceaccount.com",
      "ms_catalogue|roles/bigquery.dataViewer|serviceAccount:ms-features@test-project.iam.gserviceaccount.com",
      "ms_catalogue|roles/bigquery.dataViewer|serviceAccount:ms-parity@test-project.iam.gserviceaccount.com",
      "ms_features|roles/bigquery.dataEditor|serviceAccount:ms-features@test-project.iam.gserviceaccount.com",
      "ms_features|roles/bigquery.dataViewer|serviceAccount:ms-parity@test-project.iam.gserviceaccount.com",
      "ms_marts|roles/bigquery.dataEditor|serviceAccount:ms-warehouse@test-project.iam.gserviceaccount.com",
      "ms_marts|roles/bigquery.dataViewer|serviceAccount:ms-features@test-project.iam.gserviceaccount.com",
      "ms_marts|roles/bigquery.dataViewer|serviceAccount:ms-parity@test-project.iam.gserviceaccount.com",
      "ms_ops|roles/bigquery.dataEditor|serviceAccount:ms-parity@test-project.iam.gserviceaccount.com",
      "ms_ops|roles/bigquery.dataEditor|serviceAccount:ms-warehouse@test-project.iam.gserviceaccount.com",
      "ms_raw|roles/bigquery.dataEditor|serviceAccount:ms-warehouse@test-project.iam.gserviceaccount.com",
      "ms_raw|roles/bigquery.dataViewer|serviceAccount:ms-features@test-project.iam.gserviceaccount.com",
      "ms_raw|roles/bigquery.dataViewer|serviceAccount:ms-parity@test-project.iam.gserviceaccount.com",
      "ms_staging|roles/bigquery.dataEditor|serviceAccount:ms-warehouse@test-project.iam.gserviceaccount.com",
    ])
    error_message = "The runtime dataset grants are exactly the 14 that were approved: none added, none removed, none changed."
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
    condition     = length(google_bigquery_table.fact) == 12
    error_message = "Eight original tables plus the four added in Part C1."
  }
}
