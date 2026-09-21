# Inputs for our own ECR module. The repository list is passed in from the stack
# (bootstrap/) so the module stays reusable; everything else has a sane default that
# matches how the live repos were created during the smoke (P5 is an IMPORT slice, so
# the defaults are deliberately chosen to produce a no-change import).

variable "repository_names" {
  description = "ECR repository names to manage (one repo per name). Passed from the stack."
  type        = list(string)
}

variable "image_tag_mutability" {
  description = "MUTABLE or IMMUTABLE. Kept MUTABLE to match the live repos (clean import); IMMUTABLE is a future hardening once CI tagging is stable."
  type        = string
  default     = "MUTABLE"
}

variable "scan_on_push" {
  description = "Run a basic CVE scan on every push. Off to match live; enabling it later is a deliberate in-place edit."
  type        = bool
  default     = false
}

variable "encryption_type" {
  description = "Server-side encryption for image layers. AES256 matches the live repos' default."
  type        = string
  default     = "AES256"
}

variable "untagged_expire_days" {
  description = "Lifecycle: expire untagged images older than this many days (sweeps PR/build cruft)."
  type        = number
  default     = 14
}

variable "keep_last_tagged" {
  description = "Lifecycle: keep only the most recent N tagged (semver) images; older tagged images expire."
  type        = number
  default     = 10
}

variable "tagged_pattern_list" {
  description = "Tag patterns the keep-last-N rule applies to. [\"*\"] matches all tagged images (our tags are bare semver like 1.0.0, no prefix)."
  type        = list(string)
  default     = ["*"]
}

variable "force_delete" {
  description = "Delete the repository even when it still holds images (HM8 retirement: the history lives on GHCR)."
  type        = bool
}
