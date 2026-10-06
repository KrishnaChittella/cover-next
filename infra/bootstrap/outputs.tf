output "workload_identity_provider" {
  description = "Value for the GitHub Actions auth step."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deploy_service_account" {
  value = google_service_account.deploy.email
}

output "runtime_service_accounts" {
  value = { for name, account in google_service_account.runtime : name => account.email }
}

output "image_repository" {
  description = "Where the deploy workflow pushes the application image."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "notification_channel" {
  value = google_monitoring_notification_channel.owner_email.id
}
