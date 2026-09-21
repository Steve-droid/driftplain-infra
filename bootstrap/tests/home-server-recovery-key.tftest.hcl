mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_partition" { defaults = { partition = "aws" } }
}
mock_provider "aws" { alias = "us_east_1" }
override_resource {
  override_during = plan
  target          = aws_secretsmanager_secret.home_server_recovery_key
  values          = { arn = "arn:aws:secretsmanager:ap-south-1:957261948820:secret:modelmatch/home-server/recovery-key-v1-ABC123" }
}

run "protected_operator_recovery_metadata" {
  command = plan
  assert {
    condition = (
      aws_secretsmanager_secret.home_server_recovery_key.recovery_window_in_days == 30 &&
      aws_secretsmanager_secret_policy.home_server_recovery_key.block_public_policy
    )
    error_message = "Recovery custody requires a deletion window and public-policy guard."
  }
  assert {
    condition = (
      jsondecode(aws_secretsmanager_secret_policy.home_server_recovery_key.policy).Statement[0].Effect == "Deny" &&
      jsondecode(aws_secretsmanager_secret_policy.home_server_recovery_key.policy).Statement[0].Condition.ArnNotEquals["aws:PrincipalArn"] == var.home_server_recovery_operator_arn &&
      length(jsondecode(aws_secretsmanager_secret_policy.home_server_recovery_key.policy).Statement) == 1
    )
    error_message = "Only the operator may read the key; no other principal is granted or named (HM8)."
  }
}
run "reject_runtime_secret_reuse" {
  command = plan
  variables { home_server_recovery_key_secret_name = "modelmatch/app" }
  expect_failures = [var.home_server_recovery_key_secret_name]
}
run "reject_wildcard_operator" {
  command = plan
  variables { home_server_recovery_operator_arn = "*" }
  expect_failures = [var.home_server_recovery_operator_arn]
}
