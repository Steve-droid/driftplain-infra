# Terraform + provider version constraints for the persistent bootstrap stack.
# required_version >= 1.10 is what makes `use_lockfile` (S3-native state locking,
# no DynamoDB table) available in the S3 backend — see backend.tf.
terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
