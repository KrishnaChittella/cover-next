terraform {
  required_version = ">= 1.9.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.0"
    }
  }

  # Same hand-made state bucket as the bootstrap root, different prefix.
  # terraform init -backend-config="bucket=<state bucket>"
  backend "gcs" {
    prefix = "prod"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
