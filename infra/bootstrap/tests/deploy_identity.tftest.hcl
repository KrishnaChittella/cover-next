# What the deploy identity may hold at project level. Runs entirely offline: the
# provider is mocked, nothing is planned against a real project and nothing is applied.
#
#   terraform init -backend=false && terraform test

mock_provider "google" {}

variables {
  project_id           = "test-project"
  billing_account_id   = "000000-000000-000000"
  state_bucket_name    = "test-project-tfstate"
  github_owner_id      = "1"
  github_repository_id = "2"
  github_repository    = "owner/name"
  github_actor_id      = "1"
  alert_email          = "nobody@example.invalid"
}

run "the_deploy_identity_holds_no_data_role" {
  command = plan

  assert {
    condition = join(" ", sort(keys(google_project_iam_member.deploy))) == join(" ", [
      "roles/cloudscheduler.admin",
      "roles/logging.configWriter",
      "roles/monitoring.editor",
      "roles/run.admin",
    ])
    error_message = "The predefined project roles of ms-deploy are exactly the four reviewed ones."
  }

  assert {
    condition     = !contains(keys(google_project_iam_member.deploy), "roles/bigquery.admin")
    error_message = "ms-deploy must not hold roles/bigquery.admin: it carries rows and query jobs."
  }

  assert {
    condition     = !contains(keys(google_project_iam_member.deploy), "roles/storage.admin")
    error_message = "ms-deploy must not hold roles/storage.admin: it carries every object."
  }

  assert {
    condition = alltrue([
      for role in keys(google_project_iam_member.deploy) :
      !contains(["roles/owner", "roles/editor", "roles/resourcemanager.projectIamAdmin", "roles/iam.serviceAccountTokenCreator", "roles/iam.serviceAccountKeyAdmin", "roles/iam.serviceAccountUser", "roles/secretmanager.admin", "roles/secretmanager.secretAccessor", "roles/bigquery.dataOwner", "roles/bigquery.dataEditor", "roles/bigquery.dataViewer", "roles/bigquery.jobUser", "roles/bigquery.user", "roles/storage.objectAdmin", "roles/storage.objectUser", "roles/storage.objectViewer"], role)
    ])
    error_message = "ms-deploy must not hold a basic, impersonation, secret or data role on the project."
  }

  assert {
    condition     = google_project_iam_custom_role.deploy_bigquery.role_id == "msDeployBigQueryConfig" && google_project_iam_custom_role.deploy_storage.role_id == "msDeployBucketConfig"
    error_message = "The two replacement roles are the named custom roles."
  }
}

run "the_bigquery_role_is_configuration_only" {
  command = plan

  assert {
    condition = join(" ", sort(tolist(google_project_iam_custom_role.deploy_bigquery.permissions))) == join(" ", [
      "bigquery.datasets.create",
      "bigquery.datasets.get",
      "bigquery.datasets.getIamPolicy",
      "bigquery.datasets.setIamPolicy",
      "bigquery.datasets.update",
      "bigquery.rowAccessPolicies.list",
      "bigquery.tables.create",
      "bigquery.tables.get",
      "bigquery.tables.update",
    ])
    error_message = "The BigQuery deploy role holds exactly the reviewed permissions."
  }

  assert {
    condition = alltrue([
      for p in google_project_iam_custom_role.deploy_bigquery.permissions :
      !contains(["bigquery.tables.getData", "bigquery.tables.updateData", "bigquery.jobs.create", "bigquery.tables.export", "bigquery.tables.createSnapshot", "bigquery.readsessions.create", "bigquery.tables.delete", "bigquery.datasets.delete"], p)
    ])
    error_message = "The BigQuery deploy role must not read or write rows, run jobs, export, or delete."
  }

  assert {
    condition = alltrue([
      for p in google_project_iam_custom_role.deploy_bigquery.permissions :
      startswith(p, "bigquery.") && !endswith(p, "Data") && !strcontains(p, ".jobs.")
    ])
    error_message = "Nothing outside BigQuery configuration belongs in this role."
  }
}

run "the_storage_role_is_configuration_only" {
  command = plan

  assert {
    condition = join(" ", sort(tolist(google_project_iam_custom_role.deploy_storage.permissions))) == join(" ", [
      "storage.buckets.create",
      "storage.buckets.get",
      "storage.buckets.getIamPolicy",
      "storage.buckets.setIamPolicy",
      "storage.buckets.update",
    ])
    error_message = "The storage deploy role holds exactly the reviewed permissions."
  }

  assert {
    condition = alltrue([
      for p in google_project_iam_custom_role.deploy_storage.permissions :
      startswith(p, "storage.buckets.") && p != "storage.buckets.delete"
    ])
    error_message = "The storage deploy role must not touch objects or delete a bucket."
  }

  assert {
    condition     = google_storage_bucket_iam_member.deploy_state.role == "roles/storage.objectUser" && google_storage_bucket_iam_member.deploy_state.bucket == "test-project-tfstate"
    error_message = "Object access exists on the Terraform state bucket only."
  }
}

run "the_secret_role_still_cannot_read_or_add_a_value" {
  command = plan

  assert {
    condition = alltrue([
      for p in google_project_iam_custom_role.secret_container_admin.permissions :
      !contains(["secretmanager.versions.access", "secretmanager.versions.add"], p)
    ])
    error_message = "ms-deploy must never read or add a secret version."
  }
}
