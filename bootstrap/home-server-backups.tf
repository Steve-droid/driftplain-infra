# E21/HM2: independent of platform/ destruction, but in Steve's existing AWS account.
# This only provisions the backup destination. HM3/HM5 own encrypted export/restore,
# scheduled uploads, retention activation and dedicated short-lived home-server identity.
data "aws_partition" "current" {}

locals {
  home_server_backup_bucket_arn = "arn:${data.aws_partition.current.partition}:s3:::${var.home_server_backup_bucket_name}"
}

resource "aws_s3_bucket" "home_server_backups" {
  bucket        = var.home_server_backup_bucket_name
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "home_server_backups" {
  bucket = aws_s3_bucket.home_server_backups.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "home_server_backups" {
  bucket                  = aws_s3_bucket.home_server_backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "home_server_backups" {
  bucket = aws_s3_bucket.home_server_backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "home_server_backups" {
  bucket = aws_s3_bucket.home_server_backups.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_policy" "home_server_backups" {
  bucket = aws_s3_bucket.home_server_backups.id
  # No access grants here: uploads/restores require a separately authorized IAM identity.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [local.home_server_backup_bucket_arn, "${local.home_server_backup_bucket_arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "DenyPlatformTeardownAccess"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [local.home_server_backup_bucket_arn, "${local.home_server_backup_bucket_arn}/*"]
        Condition = { ArnEquals = { "aws:PrincipalArn" = aws_iam_role.platform_teardown.arn } }
      }
    ]
  })
}

# E21/HM5 reviewed retention (the HM3 restore from S3 succeeded on 2026-09-15). Scheduled exports
# land under postgres/hourly/ and postgres/daily/; recovery/ (issuer, sealing keys, credential
# bundles, acceptance markers) carries NO expiry rule and keeps every version. S3 evaluates
# lifecycle once a day at midnight UTC, so an "expiration" of N days keeps an object N–N+1 days;
# expiring a current version only writes a delete marker on a versioned bucket, so noncurrent
# versions expire on the same schedule and orphaned delete markers are removed.
# Versioning and prevent_destroy are not immutable retention or protection from account admins.
resource "aws_s3_bucket_lifecycle_configuration" "home_server_backups" {
  bucket = aws_s3_bucket.home_server_backups.id

  rule {
    id     = "postgres-hourly"
    status = "Enabled"
    filter {
      prefix = "postgres/hourly/"
    }
    expiration {
      days = var.home_server_backup_hourly_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = var.home_server_backup_hourly_retention_days
    }
  }

  rule {
    id     = "postgres-daily"
    status = "Enabled"
    filter {
      prefix = "postgres/daily/"
    }
    expiration {
      days = var.home_server_backup_daily_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = var.home_server_backup_daily_retention_days
    }
  }

  rule {
    id     = "housekeeping"
    status = "Enabled"
    filter {}
    # Only markers whose versions are all gone; never a live object or a retained version.
    expiration {
      expired_object_delete_marker = true
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.home_server_backups]
}
