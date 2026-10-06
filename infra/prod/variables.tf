variable "project_id" {
  description = "Globally unique GCP project id for MetaSpike Production."
  type        = string
}

variable "region" {
  description = "Region for every regional resource, and the only place data is stored."
  type        = string
  default     = "us-central1"

  validation {
    condition     = var.region == "us-central1"
    error_message = "Persistent MetaSpike data is confined to us-central1 (United States)."
  }
}

variable "image" {
  description = "The application image, pinned by digest."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.image))
    error_message = "Deploy by digest (…@sha256:<64 hex>), never by a movable tag."
  }
}

variable "notification_channel" {
  description = "Monitoring notification channel id, from the bootstrap root's output."
  type        = string
}

variable "scheduler_paused" {
  description = "The one-action stop. While true, nothing is collected."
  type        = bool
  default     = true
}
