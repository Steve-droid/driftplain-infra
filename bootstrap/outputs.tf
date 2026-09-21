# Cross-stack outputs: the platform stack (and later CI / gitops) read these via a
# `terraform_remote_state` data source pointed at this bootstrap state — ECR URLs/ARNs, the
# state-bucket name/ARN, the budget topic.
#
# NOTE: the platform `backend "s3"` block does NOT read these. A backend block accepts only
# literals (no variables, no remote_state), so bucket/key/region are hardcoded there and kept
# in sync across the two backend.tf files by hand (see modelmatch-infra/CLAUDE.md). These
# outputs feed the `terraform_remote_state` data source (which can take config) and `terraform
# output` for humans/scripts — not the backend block.
output "state_bucket_name" {
  description = "Name of the S3 bucket holding Terraform remote state."
  value       = aws_s3_bucket.tf_state.id
}

output "state_bucket_arn" {
  description = "ARN of the Terraform remote-state bucket."
  value       = aws_s3_bucket.tf_state.arn
}

output "aws_region" {
  description = "Region the state bucket lives in."
  value       = var.aws_region
}

# Exposed for future fan-out (Slack/Lambda subscribers) and so platform/ could notify it later.
output "budget_alerts_topic_arn" {
  description = "ARN of the SNS topic that receives AWS Budgets alerts (us-east-1)."
  value       = aws_sns_topic.budget_alerts.arn
}

# Ingestion bucket (P6). Read by platform/ via terraform_remote_state so the P7 IRSA role-A
# policy can scope s3 access to this exact ARN — the app contract, not duplicated as a literal.
output "ingestion_bucket_name" {
  description = "Name of the S3 bucket holding catalog-ingestion source docs (S5b)."
  value       = aws_s3_bucket.ingestion.id
}

output "ingestion_bucket_arn" {
  description = "ARN of the ingestion bucket — scoped into the P7 IRSA role-A policy."
  value       = aws_s3_bucket.ingestion.arn
}

output "home_server_backup_bucket_name" {
  description = "Dedicated private destination for encrypted home-server backups."
  value       = aws_s3_bucket.home_server_backups.id
}

output "home_server_backup_bucket_arn" {
  description = "Backup bucket ARN for the separately reviewed home-server uploader/restore roles."
  value       = aws_s3_bucket.home_server_backups.arn
}

output "home_server_recovery_key_secret_arn" {
  description = "Operator recovery-key secret metadata; private value is never read by Terraform."
  value       = aws_secretsmanager_secret.home_server_recovery_key.arn
}
