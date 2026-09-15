locals {
  home_server_subjects = {
    backup  = "driftplain-home-server-backup"
    bedrock = "driftplain-home-server-bedrock"
  }
  home_server_identities = var.home_server_identity_enabled ? local.home_server_subjects : {}
  # Mirror the exact reviewed platform/irsa.tf model/profile scope. E21/HM4 adds the
  # same ingestion-source object grant IRSA role A has (GetObject/PutObject on the
  # content-hash prefix only; no ListBucket, no delete) so the durable S3 blob store
  # works at home under the Bedrock identity the backend pod already carries.
  home_server_profile_arns = [
    for id in var.home_server_bedrock_profile_ids :
    "arn:aws:bedrock:${var.aws_region}:${var.home_server_account_id}:inference-profile/${id}"
  ]
  home_server_model_arns = [
    for id in var.home_server_bedrock_model_ids :
    "arn:aws:bedrock:*::foundation-model/${id}"
  ]
  home_server_permissions = {
    backup = {
      Version = "2012-10-17"
      Statement = [{
        Sid    = "UploadEncryptedBackups"
        Effect = "Allow"
        Action = ["s3:PutObject"]
        Resource = [for prefix in ["postgres/hourly", "postgres/daily", "recovery"] :
          "arn:aws:s3:::${var.home_server_backup_bucket_name}/${prefix}/*"
        ]
      }]
    }
    bedrock = {
      Version = "2012-10-17"
      Statement = [
        {
          Sid      = "InvokeNovaProfiles"
          Effect   = "Allow"
          Action   = ["bedrock:InvokeModel"]
          Resource = local.home_server_profile_arns
        },
        {
          Sid      = "InvokeNovaModelsViaProfile"
          Effect   = "Allow"
          Action   = ["bedrock:InvokeModel"]
          Resource = local.home_server_model_arns
          Condition = { StringEquals = {
            "bedrock:InferenceProfileArn" = local.home_server_profile_arns
          } }
        },
        {
          Sid      = "IngestionSourceObjects"
          Effect   = "Allow"
          Action   = ["s3:GetObject", "s3:PutObject"]
          Resource = "arn:aws:s3:::${var.home_server_ingestion_bucket_name}/sources/*"
        }
      ]
    }
  }
}

resource "aws_rolesanywhere_trust_anchor" "home_server" {
  count   = var.home_server_identity_enabled ? 1 : 0
  name    = "modelmatch-home-server-issuer-v1"
  enabled = var.home_server_sessions_enabled
  source {
    source_type = "CERTIFICATE_BUNDLE"
    source_data {
      x509_certificate_data = var.home_server_ca_certificate_pem
    }
  }
  lifecycle { prevent_destroy = true }
  depends_on = [aws_iam_role_policy.home_server_teardown_protection]
}

resource "aws_iam_role" "home_server" {
  for_each             = local.home_server_identities
  name                 = "modelmatch-home-server-${each.key}"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "rolesanywhere.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession", "sts:SetSourceIdentity"]
      Condition = {
        ArnEquals = {
          "aws:SourceArn" = aws_rolesanywhere_trust_anchor.home_server[0].arn
        }
        StringEquals = {
          "aws:SourceAccount"               = var.home_server_account_id
          "aws:PrincipalTag/x509Subject/CN" = each.value
          "aws:PrincipalTag/x509Issuer/CN"  = "driftplain-home-server-issuer-v1"
        }
      }
    }]
  })
  lifecycle { prevent_destroy = true }
}

# Additive policy on the existing teardown role; bootstrap still owns its existing
# guardrails. Explicit Deny wins over that role's AdministratorAccess attachment.
resource "aws_iam_role_policy" "home_server_teardown_protection" {
  count = var.home_server_identity_enabled ? 1 : 0
  name  = "home-server-identity-protection"
  role  = var.home_server_teardown_role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DenyRolesAnywhereAdministration"
        Effect   = "Deny"
        Action   = ["rolesanywhere:*"]
        Resource = "*"
      },
      {
        Sid      = "DenyHomeServerRoleAccess"
        Effect   = "Deny"
        Action   = ["iam:*", "sts:AssumeRole"]
        Resource = [for name in keys(local.home_server_subjects) : "arn:aws:iam::${var.home_server_account_id}:role/modelmatch-home-server-${name}"]
      },
      {
        Sid      = "DenyHomeServerIdentityStateAccess"
        Effect   = "Deny"
        Action   = ["s3:*"]
        Resource = "arn:aws:s3:::modelmatch-tfstate-${var.home_server_account_id}/home-server/identity/*"
      }
    ]
  })
  lifecycle { prevent_destroy = true }
}

resource "aws_iam_role_policy" "home_server" {
  for_each = local.home_server_identities
  name     = "modelmatch-home-server-${each.key}"
  role     = aws_iam_role.home_server[each.key].id
  policy   = jsonencode(local.home_server_permissions[each.key])
}

resource "aws_rolesanywhere_profile" "home_server" {
  for_each                    = local.home_server_identities
  name                        = "modelmatch-home-server-${each.key}"
  enabled                     = var.home_server_sessions_enabled
  duration_seconds            = 3600
  require_instance_properties = false
  role_arns                   = [aws_iam_role.home_server[each.key].arn]
  # An additional session ceiling, even if the role gains another policy later.
  session_policy = jsonencode(local.home_server_permissions[each.key])
  lifecycle { prevent_destroy = true }
}
