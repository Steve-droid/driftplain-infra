# Persistent identity lifecycle, independent of platform/ and bootstrap/.
# Local tests use init -backend=false; live plans use this backend with normal locking.
terraform {
  backend "s3" {
    bucket       = "modelmatch-tfstate-957261948820"
    key          = "home-server/identity/terraform.tfstate"
    region       = "ap-south-1"
    encrypt      = true
    use_lockfile = true
  }
}
