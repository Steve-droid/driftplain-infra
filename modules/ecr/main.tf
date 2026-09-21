# Our own ECR module (no third-party/registry modules — Roey's hard rule). One repository
# and one lifecycle policy per name in var.repository_names.
#
# IMPORT NOTE (P5): the three repos already exist (created during the smoke, they hold the
# live :1.0.0 images). This config is matched to the live settings (MUTABLE / scanOnPush=false
# / AES256) so `terraform import` followed by `plan` shows NO destroy/replace — only the new
# lifecycle policies (which the repos never had) plus benign default_tags additions.

resource "aws_ecr_repository" "this" {
  for_each = toset(var.repository_names)

  name                 = each.value
  image_tag_mutability = var.image_tag_mutability
  # HM8 (September 22, 2026): the release history is on public GHCR (every ECR tag copied,
  # digests equal), so the repositories can be deleted with their images in one step.
  force_delete = var.force_delete

  image_scanning_configuration {
    scan_on_push = var.scan_on_push
  }

  encryption_configuration {
    encryption_type = var.encryption_type
  }
}

# Registry hygiene (lesson-04 FinOps): image storage is cheap per-GB but unbounded without a
# policy. Two rules, evaluated by ascending rulePriority:
#   1. expire untagged images older than N days  — sweeps PR/build leftovers
#   2. keep only the last N tagged images        — bounds release history (e.g. :1.0.0 ...)
resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images older than ${var.untagged_expire_days} days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = var.untagged_expire_days
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Keep only the last ${var.keep_last_tagged} tagged images"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = var.tagged_pattern_list
          countType      = "imageCountMoreThan"
          countNumber    = var.keep_last_tagged
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
