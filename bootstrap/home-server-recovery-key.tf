# HM2 custody: Terraform owns metadata/protection only, NEVER the private key value.
# Populate through home-server/recovery-key.py after the reviewed apply.
data "aws_partition" "home_server_recovery" {}

locals {
  # Six-character suffix wildcard permits a complete guardrail plan before creation.
  home_server_recovery_key_arn_pattern = "arn:${data.aws_partition.home_server_recovery.partition}:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.home_server_recovery_key_secret_name}-??????"
}

resource "aws_secretsmanager_secret" "home_server_recovery_key" {
  name                    = var.home_server_recovery_key_secret_name
  description             = "Driftplain backup decryption key v1; operator recovery only, never home-server runtime access."
  recovery_window_in_days = 30
  # Default AWS-managed aws/secretsmanager key; no customer-managed KMS key fee.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_secretsmanager_secret_policy" "home_server_recovery_key" {
  secret_arn          = aws_secretsmanager_secret.home_server_recovery_key.arn
  block_public_policy = true
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyRecoveryKeyReadOutsideOperator"
        Effect    = "Deny"
        Principal = "*"
        Action    = ["secretsmanager:GetSecretValue", "secretsmanager:BatchGetSecretValue"]
        Resource  = "*"
        Condition = { ArnNotEquals = { "aws:PrincipalArn" = var.home_server_recovery_operator_arn } }
      }
    ]
  })
}

# No secret_version resource or secret-value data source: plaintext must not enter state.
# Keep v1 while any retained backup needs it. A future key gets its own named secret/item.
