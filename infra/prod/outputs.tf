output "buckets" {
  value = { for name, bucket in google_storage_bucket.data : name => bucket.name }
}

output "datasets" {
  value = keys(google_bigquery_dataset.this)
}

output "jobs" {
  value = keys(google_cloud_run_v2_job.this)
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "collection_running" {
  description = "False while the single scheduler job is paused."
  value       = !var.scheduler_paused
}
