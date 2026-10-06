output "buckets" {
  value = { for name, bucket in google_storage_bucket.data : name => bucket.name }
}

output "datasets" {
  value = keys(google_bigquery_dataset.this)
}

output "jobs" {
  value = keys(google_cloud_run_v2_job.this)
}

output "collection_running" {
  description = "False while the single scheduler job is paused."
  value       = !var.scheduler_paused
}
