# Concrete NON-SECRET values for the bootstrap (persistent) stack.
# Passed EXPLICITLY: `terraform -chdir=bootstrap plan|apply -var-file=dev.tfvars`
# (we do not rely on auto-loaded terraform.tfvars / *.auto.tfvars — Roey's rule, predictability).
# Committed on purpose: nothing here is a secret. Secrets reach the cluster via Secrets Manager
# (ESO + IRSA), never through tfvars.

aws_region = "ap-south-1"

# --- State backend (P1) ---
state_bucket_name = "modelmatch-tfstate-957261948820"

# --- Budget + alerting (P2) ---
budget_limit_amount = "110"                     # USD/month GROSS (credits not netted out); alerts only — Budgets never caps spend. 80%/90% ACTUAL + 100% FORECASTED
alert_email         = "stevelevit230@gmail.com" # config, not a secret

# --- ECR repos (P5 adopted in the old account; P32 creates them fresh in 957261948820) ---
ecr_repository_names = ["modelmatch-backend", "modelmatch-frontend", "modelmatch-agent", "modelmatch-agent-security"]

# --- Ingestion source bucket (P6) — APP CONTRACT: the backend reads it from S3_BUCKET (gitops values).
# Renamed with the account suffix at P32 (2026-09-06): the bare name was still held by the closed
# bootcamp account (AWS keeps a closed account's resources ~90 days) → BucketAlreadyExists. ---
ingestion_bucket_name = "modelmatch-ingestion-sources-957261948820"

# E21: encrypted home-server backups, protected separately from state and ingestion data.
home_server_backup_bucket_name       = "modelmatch-home-server-backups-957261948820"
home_server_recovery_key_secret_name = "modelmatch/home-server/recovery-key-v1"
home_server_recovery_operator_arn    = "arn:aws:iam::957261948820:user/steve"
# HM5 reviewed retention: hourly one day, daily 30 days; recovery/ keeps every version. Apply = separate approval.
home_server_backup_hourly_retention_days = 1
home_server_backup_daily_retention_days  = 30

# --- P34b budget kill switch (2026-09-07) — Budgets 90% ACTUAL -> SNS -> Lambda -> CodeBuild teardown ---
killswitch_lambda_dry_run        = "1" # E21 migration safeguard: plan-only; live apply requires Steve's approval. Keep budget alerts and token caps.
killswitch_trigger_percent       = 90  # must match the 90% ACTUAL notification in budget.tf
killswitch_build_timeout_minutes = 45
killswitch_log_retention_days    = 90

codebuild_image              = "aws/codebuild/amazonlinux-x86_64-standard:5.0"
terraform_version            = "1.15.5"                                                           # same as the laptop (S3-native locking needs >= 1.10)
terraform_sha256_linux_amd64 = "702b2136af6728c8ff037f843dd2dbce2b7ad88786b7381d1d72aefa250f601c" # from releases.hashicorp.com SHA256SUMS, 2026-09-07
infra_repo_url               = "https://github.com/Steve-droid/driftplain-infra.git"
infra_repo_branch            = "main"
