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

# --- Source secret versions ---------------------------------------------------------
# A job is given a reference to a source secret only when the EXACT version to use is
# named here. Unset (the default) means the job has no reference to that secret at
# all, which is the only valid state while the secret container has no version: Cloud
# Run will not create a job that points at a version that does not exist.
#
# What goes here is a Secret Manager version NUMBER. It is metadata, not the secret;
# no secret value is ever a Terraform input. "latest" is refused, so a deployment
# always states which version it runs with and never moves silently.

variable "steam_web_api_key_version" {
  description = "Numeric version of steam-web-api-key to give the Steam jobs. Unset: no reference."
  type        = string
  default     = null

  validation {
    condition     = var.steam_web_api_key_version == null || can(regex("^[1-9][0-9]*$", var.steam_web_api_key_version))
    error_message = "Use an explicit positive version number such as \"1\". \"latest\" and aliases are not accepted."
  }
}

variable "igdb_client_id_version" {
  description = "Numeric version of igdb-client-id to give the catalogue job. Unset: no reference."
  type        = string
  default     = null

  validation {
    condition     = var.igdb_client_id_version == null || can(regex("^[1-9][0-9]*$", var.igdb_client_id_version))
    error_message = "Use an explicit positive version number such as \"1\". \"latest\" and aliases are not accepted."
  }
}

variable "igdb_client_secret_version" {
  description = "Numeric version of igdb-client-secret to give the catalogue job. Unset: no reference."
  type        = string
  default     = null

  validation {
    condition     = var.igdb_client_secret_version == null || can(regex("^[1-9][0-9]*$", var.igdb_client_secret_version))
    error_message = "Use an explicit positive version number such as \"1\". \"latest\" and aliases are not accepted."
  }
}
