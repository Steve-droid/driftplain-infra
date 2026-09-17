# Mocked plans only. No real bucket, IAM role, SNS message or Lambda invocation.
mock_provider "aws" {
  # These unrelated existing-stack data sources must still produce valid JSON for
  # provider schema validation. Real backup/teardown policy JSON is checked in the
  # refreshed AWS plan and IAM simulation, not replaced by this mock's empty policy.
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
}
mock_provider "aws" { alias = "us_east_1" }
mock_provider "archive" {}

run "private_versioned_destination" {
  command = plan
  assert {
    condition = (
      aws_s3_bucket.home_server_backups.force_destroy == false &&
      aws_s3_bucket_versioning.home_server_backups.versioning_configuration[0].status == "Enabled" &&
      aws_s3_bucket_ownership_controls.home_server_backups.rule[0].object_ownership == "BucketOwnerEnforced" &&
      aws_s3_bucket_server_side_encryption_configuration.home_server_backups.rule[*].apply_server_side_encryption_by_default[0].sse_algorithm == tolist(["AES256"])
    )
    error_message = "Backups require versioning, bucket ownership and encryption without force-empty deletion."
  }
  assert {
    condition = (
      aws_s3_bucket_public_access_block.home_server_backups.block_public_acls &&
      aws_s3_bucket_public_access_block.home_server_backups.block_public_policy &&
      aws_s3_bucket_public_access_block.home_server_backups.ignore_public_acls &&
      aws_s3_bucket_public_access_block.home_server_backups.restrict_public_buckets
    )
    error_message = "Every public-access block must remain enabled."
  }
  assert {
    condition     = var.killswitch_lambda_dry_run == "1"
    error_message = "Preparing backups must not re-arm automatic source teardown."
  }
}

run "reject_state_bucket_reuse" {
  command = plan
  variables {
    home_server_backup_bucket_name = "modelmatch-tfstate-957261948820"
  }
  expect_failures = [var.home_server_backup_bucket_name]
}

run "reject_ingestion_bucket_reuse" {
  command = plan
  variables {
    home_server_backup_bucket_name = "modelmatch-ingestion-sources-957261948820"
  }
  expect_failures = [var.home_server_backup_bucket_name]
}

run "reject_invalid_bucket_name" {
  command = plan
  variables {
    home_server_backup_bucket_name = "INVALID backup name"
  }
  expect_failures = [var.home_server_backup_bucket_name]
}

run "reviewed_retention_rules" {
  command = plan
  assert {
    condition = (
      [for r in aws_s3_bucket_lifecycle_configuration.home_server_backups.rule : r.id] == ["postgres-hourly", "postgres-daily", "housekeeping"] &&
      alltrue([for r in aws_s3_bucket_lifecycle_configuration.home_server_backups.rule : r.status == "Enabled"])
    )
    error_message = "Exactly the three reviewed lifecycle rules, all enabled."
  }
  assert {
    condition = (
      aws_s3_bucket_lifecycle_configuration.home_server_backups.rule[0].filter[0].prefix == "postgres/hourly/" &&
      aws_s3_bucket_lifecycle_configuration.home_server_backups.rule[0].expiration[0].days == 1 &&
      aws_s3_bucket_lifecycle_configuration.home_server_backups.rule[0].noncurrent_version_expiration[0].noncurrent_days == 1 &&
      aws_s3_bucket_lifecycle_configuration.home_server_backups.rule[1].filter[0].prefix == "postgres/daily/" &&
      aws_s3_bucket_lifecycle_configuration.home_server_backups.rule[1].expiration[0].days == 30 &&
      aws_s3_bucket_lifecycle_configuration.home_server_backups.rule[1].noncurrent_version_expiration[0].noncurrent_days == 30
    )
    error_message = "Hourly exports keep one day and daily exports 30 days, current and noncurrent versions alike."
  }
  assert {
    # The empty filter's prefix and the unset expiration days are unknown until apply; the
    # known parts pin the contract: marker cleanup on, no object expiry days, abort at 1 day.
    condition = (
      aws_s3_bucket_lifecycle_configuration.home_server_backups.rule[2].expiration[0].expired_object_delete_marker == true &&
      aws_s3_bucket_lifecycle_configuration.home_server_backups.rule[2].abort_incomplete_multipart_upload[0].days_after_initiation == 1 &&
      length(aws_s3_bucket_lifecycle_configuration.home_server_backups.rule[2].noncurrent_version_expiration) == 0
    )
    error_message = "Housekeeping removes only orphaned delete markers and abandoned multipart uploads; it never expires objects."
  }
  assert {
    condition = (
      !startswith(aws_s3_bucket_lifecycle_configuration.home_server_backups.rule[0].filter[0].prefix, "recovery/") &&
      !startswith(aws_s3_bucket_lifecycle_configuration.home_server_backups.rule[1].filter[0].prefix, "recovery/")
    )
    error_message = "recovery/ must never carry an expiry rule."
  }
}

run "reject_hourly_longer_than_daily" {
  command = plan
  variables {
    home_server_backup_hourly_retention_days = 7
    home_server_backup_daily_retention_days  = 7
  }
  expect_failures = [var.home_server_backup_daily_retention_days]
}

run "reject_fractional_or_zero_retention" {
  command = plan
  variables {
    home_server_backup_hourly_retention_days = 0.5
  }
  expect_failures = [var.home_server_backup_hourly_retention_days]
}
