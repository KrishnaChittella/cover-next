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

output "notification_channel" {
  value = google_monitoring_notification_channel.owner_email.id
}
