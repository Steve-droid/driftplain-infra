# Modicum remains live; Driftplain was purchased and delegated on September 12, 2026.
# Non-secret values. Pass explicitly with -var-file=dev.tfvars.
aws_region      = "ap-south-1"
aws_account_id  = "957261948820"
domain_name     = "modicum.cloud"
app_hostname    = "modicum.cloud"
api_hostname    = "api.modicum.cloud"
# false since September 21, 2026: AWS compute retired, the NLB no longer exists (AWS-COMPUTE-RETIREMENT.md).
# Zones and the TXT record stay; HM7 routes the runtime hostnames through Cloudflare instead.
records_enabled = false
additional_domains = {
  "driftplain.dev" = {
    app_hostname = "driftplain.dev"
    api_hostname = "api.driftplain.dev"
    # Public ownership proof supplied by Google Search Console on September 12, 2026.
    verification_txt = "google-site-verification=lq9EA-ghdeh0oRTy81xK-8rUU2gtptfVIjwLcwUy5ZA"
  }
}
# Historical ARN (NLB deleted September 21, 2026); unused while records_enabled = false.
# Re-run scripts/discover-ingress-dns.py only after an explicitly scoped platform rebuild.
ingress_nlb_arn = "arn:aws:elasticloadbalancing:ap-south-1:957261948820:loadbalancer/net/ad943b23d921d4914b93737030722b00/99ffa2a12cf35e54"
