# Inputs for the bootstrap (persistent) stack — declarations only. DEFAULTLESS by rule (Roey): no
# `default`s live here. Concrete NON-SECRET values are supplied explicitly via `-var-file=dev.tfvars`
# (we do NOT rely on auto-loaded terraform.tfvars / *.auto.tfvars). Secrets never go in tfvars.

variable "aws_region" {
  description = "AWS region for all resources in this stack."
  type        = string
}

variable "state_bucket_name" {
  description = "Globally-unique S3 bucket holding Terraform remote state for both stacks."
  type        = string
}

variable "budget_limit_amount" {
  description = "Monthly AWS budget alert threshold in USD (string per the AWS Budgets API). Alerting only — AWS Budgets does not stop or cap spend."
  type        = string
}

variable "alert_email" {
  description = "Email address subscribed to the budget-alert SNS topic. Config, not a secret."
  type        = string
}

variable "ingestion_bucket_name" {
  description = "S3 bucket for catalog-ingestion source docs (S5b). APP CONTRACT — must match the default of `s3_bucket` in modelmatch-backend/app/config.py; the P7 IRSA role-A policy scopes to its ARN."
  type        = string
}

variable "home_server_backup_bucket_name" {
  description = "Dedicated persistent S3 bucket for encrypted Driftplain home-server backups; distinct from state and ingestion."
  type        = string

  validation {
    condition = (
      can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.home_server_backup_bucket_name)) &&
      var.home_server_backup_bucket_name != var.state_bucket_name &&
      var.home_server_backup_bucket_name != var.ingestion_bucket_name
    )
    error_message = "Use a distinct 3–63 character lowercase bucket name with letters, digits or hyphens."
  }
}

variable "home_server_backup_hourly_retention_days" {
  description = "Days a postgres/hourly/ export (and its noncurrent versions) is kept before lifecycle expiry."
  type        = number
  validation {
    condition     = var.home_server_backup_hourly_retention_days >= 1 && var.home_server_backup_hourly_retention_days <= 7 && floor(var.home_server_backup_hourly_retention_days) == var.home_server_backup_hourly_retention_days
    error_message = "Hourly retention must be a whole number of days between 1 and 7."
  }
}

variable "home_server_backup_daily_retention_days" {
  description = "Days a postgres/daily/ export (and its noncurrent versions) is kept before lifecycle expiry."
  type        = number
  validation {
    condition     = var.home_server_backup_daily_retention_days >= 7 && var.home_server_backup_daily_retention_days <= 365 && floor(var.home_server_backup_daily_retention_days) == var.home_server_backup_daily_retention_days && var.home_server_backup_daily_retention_days > var.home_server_backup_hourly_retention_days
    error_message = "Daily retention must be a whole number of days between 7 and 365 and longer than hourly retention."
  }
}

variable "home_server_recovery_key_secret_name" {
  description = "Dedicated operator-only recovery-key secret name; metadata only in Terraform."
  type        = string
  validation {
    condition     = can(regex("^modelmatch/home-server/recovery-key-v[1-9][0-9]*$", var.home_server_recovery_key_secret_name))
    error_message = "Use a versioned modelmatch/home-server/recovery-key-vN name, separate from runtime app secrets."
  }
}

variable "home_server_recovery_operator_arn" {
  description = "IAM principal allowed to read the recovery key; never a home-server workload role."
  type        = string
  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:(user|role)/.+$", var.home_server_recovery_operator_arn))
    error_message = "Use an explicit IAM user or role ARN, not a wildcard or STS session ARN."
  }
}
