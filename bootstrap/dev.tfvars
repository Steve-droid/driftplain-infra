# Concrete NON-SECRET values for the bootstrap (persistent) stack.
# Passed EXPLICITLY: `terraform -chdir=bootstrap plan|apply -var-file=dev.tfvars`
# (we do not rely on auto-loaded terraform.tfvars / *.auto.tfvars — Roey's rule, predictability).
# Committed on purpose: nothing here is a secret. Secrets reach the cluster via Secrets Manager
# (ESO + IRSA), never through tfvars.

aws_region = "ap-south-1"

# --- State backend (P1) ---
state_bucket_name = "modelmatch-tfstate-957261948820"

# --- Budget + alerting (P2) ---
budget_limit_amount = "10"                      # USD/month GROSS (credits not netted out); alerts only — Budgets never caps spend. 80%/100% ACTUAL + 100% FORECASTED. HM8 (September 22, 2026): retained services only, ~$2–3/month
alert_email         = "stevelevit230@gmail.com" # config, not a secret

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
