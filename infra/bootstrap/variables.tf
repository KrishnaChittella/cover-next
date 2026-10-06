# Nothing here has a real default. Project, billing and GitHub identifiers are supplied
# by the owner at apply time and are never committed.

variable "project_id" {
  description = "Globally unique GCP project id for MetaSpike Production."
  type        = string
}

variable "billing_account_id" {
  description = "Billing account the project is linked to. Used only for the budget."
  type        = string
}

variable "region" {
  description = "Region for every regional resource."
  type        = string
  default     = "us-central1"

  validation {
    condition     = var.region == "us-central1"
    error_message = "Persistent MetaSpike data is confined to us-central1 (United States)."
  }
}

variable "state_bucket_name" {
  description = "Name of the Terraform state bucket that was created by hand."
  type        = string
}

variable "github_owner_id" {
  description = "Immutable numeric id of the GitHub account that owns the repository."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "Use the numeric owner id, not the account name."
  }
}

variable "github_repository_id" {
  description = "Immutable numeric id of the private MetaSpike repository."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "Use the numeric repository id, not the repository name."
  }
}

variable "github_repository" {
  description = "owner/name of the repository. Used only to name the one workflow file that may deploy; the security boundary is the numeric ids."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "Use the form owner/name."
  }
}

variable "github_actor_id" {
  description = "Immutable numeric id of the one GitHub user allowed to start a deployment."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_actor_id))
    error_message = "Use the numeric user id, not the login."
  }
}

variable "alert_email" {
  description = "Where budget and operational alerts are sent."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Monthly budget. Alerts warn; they stop nothing."
  type        = number
  default     = 20
}
