# Minimal config to pre-download the AWS provider plugin.
# Used by `make demo-live` to cache the provider on first run.
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 4.0"
    }
  }
}

provider "aws" {
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  region                      = "us-east-1"
}
