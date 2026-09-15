variable "aws_region" {
  type = string
  validation {
    condition     = var.aws_region == "ap-south-1"
    error_message = "Home-server identity is scoped to ap-south-1."
  }
}
variable "home_server_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.home_server_account_id))
    error_message = "Use an exact AWS account ID."
  }
}
variable "home_server_identity_enabled" {
  type        = bool
  description = "Create the reviewed identity resources independently of session enablement. Never use false to retire an existing identity."
}
variable "home_server_sessions_enabled" {
  type        = bool
  description = "Enable authentication only after certificate custody and revocation readiness are verified."
  validation {
    condition     = !var.home_server_sessions_enabled || var.home_server_identity_enabled
    error_message = "Sessions require the identity resources."
  }
}
variable "home_server_ca_certificate_pem" {
  type        = string
  description = "PUBLIC CA certificate only; no CA key, CSR or workload private material."
  validation {
    condition = (
      (!var.home_server_identity_enabled && var.home_server_ca_certificate_pem == "") ||
      can(regex("^-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\r\n]+\n-----END CERTIFICATE-----[\r\n]*$", var.home_server_ca_certificate_pem))
    )
    error_message = "Enabling resources requires exactly one public PEM certificate."
  }
}
variable "home_server_backup_bucket_name" {
  type = string
  validation {
    condition     = var.home_server_backup_bucket_name == "modelmatch-home-server-backups-${var.home_server_account_id}"
    error_message = "Only the dedicated home-server backup bucket may be targeted."
  }
}
variable "home_server_ingestion_bucket_name" {
  type = string
  validation {
    condition     = var.home_server_ingestion_bucket_name == "modelmatch-ingestion-sources-${var.home_server_account_id}"
    error_message = "Only the existing ingestion-sources bucket may be targeted."
  }
}
variable "home_server_bedrock_profile_ids" {
  type = list(string)
  validation {
    condition = toset(var.home_server_bedrock_profile_ids) == toset([
      "apac.amazon.nova-lite-v1:0", "global.amazon.nova-2-lite-v1:0"
    ])
    error_message = "Preserve the two reviewed Nova inference profiles."
  }
}
variable "home_server_bedrock_model_ids" {
  type = list(string)
  validation {
    condition = toset(var.home_server_bedrock_model_ids) == toset([
      "amazon.nova-lite-v1:0", "amazon.nova-2-lite-v1:0"
    ])
    error_message = "Preserve the two reviewed Nova models."
  }
}
variable "home_server_teardown_role_name" {
  type = string
  validation {
    condition     = var.home_server_teardown_role_name == "modelmatch-platform-teardown-codebuild"
    error_message = "Target the existing platform teardown role only."
  }
}
