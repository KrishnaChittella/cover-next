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

output "dispatch_schedule" {
  description = "The cron expression the scheduler carries, in Etc/UTC. It has no year."
  value       = local.dispatch_schedule
}

output "soak_scheduled_ticks" {
  description = "Finite soak only: how many hourly ticks the window holds. Null otherwise."
  value       = local.soak_scheduled_ticks
}

output "daily_execution_alert_threshold" {
  description = "More completed executions than this in 24 hours raises the volume alert."
  value       = local.daily_execution_threshold
}
