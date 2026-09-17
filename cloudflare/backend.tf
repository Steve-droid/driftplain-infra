terraform {
  backend "s3" {
    bucket       = "modelmatch-tfstate-957261948820"
    key          = "cloudflare/terraform.tfstate"
    region       = "ap-south-1"
    encrypt      = true
    use_lockfile = true
  }
}
