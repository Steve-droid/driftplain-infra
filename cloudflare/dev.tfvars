# E21/HM5 — non-secret inputs for the Cloudflare root. Pass explicitly on every command together
# with the rendered record twins:
#   terraform -chdir=cloudflare <cmd> -var-file=dev.tfvars -var-file=records.tfvars.json
# The account ID comes from TF_VAR_cloudflare_account_id and the API token from
# CLOUDFLARE_API_TOKEN in the operator's shell; neither is committed.
zones = ["driftplain.dev", "modicum.cloud"]

# The named tunnel and the public staging hosts (served by the home cluster). The runtime hosts
# driftplain.dev / api.driftplain.dev / modicum.cloud / api.modicum.cloud are NOT listed here:
# they stay DNS-only twins of the Route 53 aliases until HM7.
tunnel_enabled = true
tunnel_name    = "driftplain-home-server"
staging_hosts = {
  app = {
    zone               = "driftplain.dev"
    hostname           = "staging.driftplain.dev"
    origin_server_name = "app.home-server.driftplain.dev"
    cache              = true
  }
  api = {
    zone               = "driftplain.dev"
    hostname           = "api-staging.driftplain.dev"
    origin_server_name = "api.home-server.driftplain.dev"
    cache              = false
  }
}

# The connector runs inside the home cluster and dials the F5 nginx-ingress ClusterIP Service
# (10.43.144.172) by its in-cluster name over TLS, verifying the certificate against the
# home-server-ca pool the GitOps chart mounts at this path.
home_ingress_origin      = "https://nginx-ingress-controller.nginx-ingress.svc.cluster.local:443"
home_origin_ca_pool_path = "/etc/cloudflared/ca/home-server-ca.crt"
