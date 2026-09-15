mock_provider "aws" {}

override_resource {
  target          = aws_rolesanywhere_trust_anchor.home_server[0]
  override_during = plan
  values          = { arn = "arn:aws:rolesanywhere:ap-south-1:957261948820:trust-anchor/11111111-1111-1111-1111-111111111111" }
}
override_resource {
  target          = aws_iam_role.home_server["backup"]
  override_during = plan
  values          = { arn = "arn:aws:iam::957261948820:role/modelmatch-home-server-backup" }
}
override_resource {
  target          = aws_iam_role.home_server["bedrock"]
  override_during = plan
  values          = { arn = "arn:aws:iam::957261948820:role/modelmatch-home-server-bedrock" }
}

run "draft_creates_nothing" {
  command = plan
  variables {
    home_server_identity_enabled   = false
    home_server_sessions_enabled   = false
    home_server_ca_certificate_pem = ""
  }
  assert {
    condition = (length(aws_iam_role.home_server) == 0 &&
      length(aws_rolesanywhere_profile.home_server) == 0 &&
      length(aws_rolesanywhere_trust_anchor.home_server) == 0 &&
    length(aws_iam_role_policy.home_server_teardown_protection) == 0)
    error_message = "The review draft must not create resources."
  }
}

run "separate_trust_and_permissions" {
  command = plan
  variables {
    home_server_identity_enabled = true
    # Public-shaped placeholder only, NOT a usable issuer. X.509 validation is a
    # separate pre-provisioning gate; this is an offline Terraform wiring test.
    home_server_ca_certificate_pem = "-----BEGIN CERTIFICATE-----\nVEVTVA==\n-----END CERTIFICATE-----"
  }
  assert {
    condition = alltrue([for name, role in aws_iam_role.home_server :
      jsondecode(role.assume_role_policy).Statement == [{
        Effect    = "Allow"
        Principal = { Service = "rolesanywhere.amazonaws.com" }
        Action    = ["sts:AssumeRole", "sts:TagSession", "sts:SetSourceIdentity"]
        Condition = {
          ArnEquals = { "aws:SourceArn" = aws_rolesanywhere_trust_anchor.home_server[0].arn }
          StringEquals = {
            "aws:SourceAccount"               = "957261948820"
            "aws:PrincipalTag/x509Subject/CN" = "driftplain-home-server-${name}"
            "aws:PrincipalTag/x509Issuer/CN"  = "driftplain-home-server-issuer-v1"
          }
        }
      }]
    ])
    error_message = "Trust must bind exact account, anchor, issuer and distinct leaf subjects."
  }
  assert {
    condition = jsondecode(aws_iam_role_policy.home_server["backup"].policy).Statement == [{
      Sid    = "UploadEncryptedBackups"
      Effect = "Allow"
      Action = ["s3:PutObject"]
      Resource = [
        "arn:aws:s3:::modelmatch-home-server-backups-957261948820/postgres/hourly/*",
        "arn:aws:s3:::modelmatch-home-server-backups-957261948820/postgres/daily/*",
        "arn:aws:s3:::modelmatch-home-server-backups-957261948820/recovery/*"
      ]
    }]
    error_message = "Uploader must have only prefix-bound PutObject: no read/delete/admin/Bedrock."
  }
  assert {
    condition = (
      length(jsondecode(aws_iam_role_policy.home_server["bedrock"].policy).Statement) == 3 &&
      alltrue([for s in slice(jsondecode(aws_iam_role_policy.home_server["bedrock"].policy).Statement, 0, 2) : s.Action == ["bedrock:InvokeModel"] && s.Effect == "Allow"]) &&
      jsondecode(aws_iam_role_policy.home_server["bedrock"].policy).Statement[2] == {
        Sid = "IngestionSourceObjects", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"],
        Resource = "arn:aws:s3:::modelmatch-ingestion-sources-957261948820/sources/*"
      } &&
      toset(jsondecode(aws_iam_role_policy.home_server["bedrock"].policy).Statement[0].Resource) == toset([
        "arn:aws:bedrock:ap-south-1:957261948820:inference-profile/apac.amazon.nova-lite-v1:0",
        "arn:aws:bedrock:ap-south-1:957261948820:inference-profile/global.amazon.nova-2-lite-v1:0"
      ]) &&
      toset(jsondecode(aws_iam_role_policy.home_server["bedrock"].policy).Statement[1].Resource) == toset([
        "arn:aws:bedrock:*::foundation-model/amazon.nova-lite-v1:0",
        "arn:aws:bedrock:*::foundation-model/amazon.nova-2-lite-v1:0"
      ]) &&
      jsondecode(aws_iam_role_policy.home_server["bedrock"].policy).Statement[1].Condition.StringEquals["bedrock:InferenceProfileArn"] == jsondecode(aws_iam_role_policy.home_server["bedrock"].policy).Statement[0].Resource
    )
    error_message = "Bedrock must retain exact Nova/profile scope, deny bare-model invocation by omission, and hold only the object-level ingestion prefix grant."
  }
  assert {
    condition = (!aws_rolesanywhere_trust_anchor.home_server[0].enabled &&
      alltrue([for name, profile in aws_rolesanywhere_profile.home_server :
        !profile.enabled && profile.duration_seconds == 3600 &&
        profile.role_arns == toset([aws_iam_role.home_server[name].arn]) &&
        profile.session_policy == aws_iam_role_policy.home_server[name].policy &&
        aws_iam_role.home_server[name].max_session_duration == 3600
    ]))
    error_message = "Authentication stays disabled; each profile permits one role with the same permission ceiling and one-hour sessions."
  }
  assert {
    condition = (
      jsondecode(aws_iam_role_policy.home_server_teardown_protection[0].policy).Statement[0].Action == ["rolesanywhere:*"] &&
      alltrue([for s in jsondecode(aws_iam_role_policy.home_server_teardown_protection[0].policy).Statement : s.Effect == "Deny"]) &&
      jsondecode(aws_iam_role_policy.home_server_teardown_protection[0].policy).Statement[2].Resource == "arn:aws:s3:::modelmatch-tfstate-957261948820/home-server/identity/*"
    )
    error_message = "Platform teardown must not manage Roles Anywhere or its persistent state."
  }
}

run "reject_missing_ca" {
  command = plan
  variables {
    home_server_identity_enabled   = true
    home_server_ca_certificate_pem = ""
  }
  expect_failures = [var.home_server_ca_certificate_pem]
}
run "reject_private_key_input" {
  command = plan
  variables { home_server_ca_certificate_pem = "-----BEGIN PRIVATE KEY-----\nTEST\n-----END PRIVATE KEY-----" }
  expect_failures = [var.home_server_ca_certificate_pem]
}
run "reject_other_bucket" {
  command = plan
  variables { home_server_backup_bucket_name = "modelmatch-tfstate-957261948820" }
  expect_failures = [var.home_server_backup_bucket_name]
}
run "reject_model_wildcard" {
  command = plan
  variables { home_server_bedrock_model_ids = ["*"] }
  expect_failures = [var.home_server_bedrock_model_ids]
}
run "reject_sessions_without_identity" {
  command = plan
  variables {
    home_server_identity_enabled = false
    home_server_sessions_enabled = true
  }
  expect_failures = [var.home_server_sessions_enabled]
}
