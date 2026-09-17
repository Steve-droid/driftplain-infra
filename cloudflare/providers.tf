# The Cloudflare API token is never in HCL or tfvars: the provider reads CLOUDFLARE_API_TOKEN
# from the operator's shell (a scoped token: Zone:Edit, DNS:Edit, Zone Settings:Edit,
# Cloudflare Tunnel:Edit, Cache Rules:Edit on this account only). The account ID travels the
# same way as TF_VAR_cloudflare_account_id, so the committed dev.tfvars stays account-free.
provider "cloudflare" {}
