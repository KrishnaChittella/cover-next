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

# --- The schedule ---------------------------------------------------------------------
# Two modes, kept apart. In "finite_soak" (the default) the scheduler can only be
# unpaused with a window of days named below, so the hourly ticks end by themselves
# when the window does. "steady_state" is the unbounded hourly schedule; the deploy
# workflow never passes it, so reaching it takes a reviewed change to the workflow.
#
# Cloud Scheduler's cron has no year. A window of days in a month is therefore not a
# one-shot: it becomes eligible again on the same dates a year later. What bounds a
# soak to its year is the control record's `armed_until`, and the reconciliation after
# the soak, which pauses the scheduler and takes the window out again.

variable "schedule_mode" {
  description = "finite_soak: unpausing needs soak_window. steady_state: unbounded hourly."
  type        = string
  default     = "finite_soak"

  validation {
    condition     = contains(["finite_soak", "steady_state"], var.schedule_mode)
    error_message = "schedule_mode is finite_soak or steady_state."
  }
}

variable "soak_window" {
  description = "The UTC days of one calendar month on which the hourly tick fires. No year: see above."
  type = object({
    month     = number
    start_day = number
    end_day   = number
  })
  default = null

  validation {
    condition = var.soak_window == null || try(
      alltrue([
        for part in [var.soak_window.month, var.soak_window.start_day, var.soak_window.end_day] :
        floor(part) == part
      ]) && var.soak_window.month >= 1 && var.soak_window.month <= 12 && var.soak_window.start_day >= 1,
      false
    )
    error_message = "month is 1 to 12 and the days are whole numbers from 1."
  }

  validation {
    condition     = var.soak_window == null || try(var.soak_window.end_day >= var.soak_window.start_day, false)
    error_message = "The window ends before it starts. A window that wraps into another month is not supported."
  }

  # February is taken as 28 days in every year, so a window never depends on the year.
  validation {
    condition = var.soak_window == null || try(
      var.soak_window.end_day <= [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][var.soak_window.month - 1],
      false
    )
    error_message = "The window must lie inside one calendar month: end_day is past the month's last day."
  }
}

variable "soak_daily_execution_ceiling" {
  description = "Finite soak only: the most job executions any 24 hours may hold (24 ticks plus the bounded child jobs). Above it the volume alert fires."
  type        = number
  default     = null

  validation {
    condition = var.soak_daily_execution_ceiling == null || try(
      floor(var.soak_daily_execution_ceiling) == var.soak_daily_execution_ceiling
      && var.soak_daily_execution_ceiling >= 24 && var.soak_daily_execution_ceiling < 60,
      false
    )
    error_message = "A whole number from 24 (the hourly ticks alone) to 59 (below the steady-state guard of 60)."
  }
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
