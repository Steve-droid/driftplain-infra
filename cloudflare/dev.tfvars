# E21/HM5+HM7 — non-secret inputs for the Cloudflare root. Pass explicitly on every command
# together with the rendered record twins:
#   terraform -chdir=cloudflare <cmd> -var-file=dev.tfvars -var-file=records.tfvars.json
# The account ID comes from TF_VAR_cloudflare_account_id and the API token from
# CLOUDFLARE_API_TOKEN in the operator's shell; neither is committed.
zones = ["driftplain.dev", "modicum.cloud"]

# The named tunnel and every public hostname the home cluster serves through it. Since HM7
# (cutover to home after the September 21 AWS compute retirement) the runtime pair is routed
# here; the staging pair (HM5) stays for pre-release checks. modicum.cloud / api.modicum.cloud
# are deliberately absent (not delegated; HM8 decides the zone).
tunnel_enabled = true
tunnel_name    = "driftplain-home-server"
tunnel_hosts = {
  app = {
    zone               = "driftplain.dev"
    hostname           = "driftplain.dev"
    origin_server_name = "app.home-server.driftplain.dev"
    cache              = true
    comment            = "HM7 runtime app host served by the home cluster through the named tunnel"
  }
  api = {
    zone               = "driftplain.dev"
    hostname           = "api.driftplain.dev"
    origin_server_name = "api.home-server.driftplain.dev"
    cache              = false
    comment            = "HM7 runtime API host served by the home cluster through the named tunnel (uncached)"
  }
  staging_app = {
    zone               = "driftplain.dev"
    hostname           = "staging.driftplain.dev"
    origin_server_name = "app.home-server.driftplain.dev"
    cache              = true
    comment            = "HM5 staging host served by the home cluster through the named tunnel"
  }
  staging_api = {
    zone               = "driftplain.dev"
    hostname           = "api-staging.driftplain.dev"
    origin_server_name = "api.home-server.driftplain.dev"
    cache              = false
    comment            = "HM5 staging host served by the home cluster through the named tunnel"
  }
}

# The connector runs inside the home cluster and dials the F5 nginx-ingress ClusterIP Service
# (10.43.144.172) by its in-cluster name over TLS, verifying the certificate against the
# home-server-ca pool the GitOps chart mounts at this path.
home_ingress_origin      = "https://nginx-ingress-controller.nginx-ingress.svc.cluster.local:443"
home_origin_ca_pool_path = "/etc/cloudflared/ca/home-server-ca.crt"
