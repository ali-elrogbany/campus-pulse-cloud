terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region

  # Every resource gets these tags, which makes cost tracking and cleanup easy.
  default_tags {
    tags = {
      Project   = "CampusPulse"
      ManagedBy = "Terraform"
    }
  }
}
