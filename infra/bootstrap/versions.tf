terraform {
  required_version = ">= 1.9.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.0"
    }
  }

  # The state bucket is created by hand once (see docs/phase4-runbook.md); Terraform
  # cannot create the bucket that holds its own first state. The bucket name is passed
  # at init time: terraform init -backend-config="bucket=<state bucket>"
  backend "gcs" {
    prefix = "bootstrap"
  }
}

provider "google" {
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}
