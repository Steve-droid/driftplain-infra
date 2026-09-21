# ECR repositories for the four ModelMatch images (FE / BE / agent / agent-security). These live in the
# PERSISTENT bootstrap stack, never the daily destroy — the :1.0.0 images are the demo
# artifacts and must survive every platform teardown.
#
# History: in the bootcamp account the repos predated Terraform, so P5 adopted them via
# `terraform import`. In the Phase 2 account (957261948820, P32, 2026-09-06) nothing pre-exists,
# so this same module CREATES them; the repos start empty and P33 pushes the images.
# P38d imported the manually created agent-security repository and lifecycle policy.
# HM2 (September 13, 2026) retained that declaration and aligned stack=bootstrap.
# The import is complete; do not repeat it. This also supersedes the source proposal in PR #11.

module "ecr" {
  source = "../modules/ecr"

  repository_names = var.ecr_repository_names
  force_delete     = var.ecr_force_delete
  # mutability / scan / encryption / lifecycle numbers use the module defaults
  # (MUTABLE, scanOnPush=false, AES256, untagged>14d, keep last 10 tagged).
}
