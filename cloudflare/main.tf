# E21/HM5 — Cloudflare authoritative DNS for both domains (AWS stays the origin) plus the named
# tunnel and the public STAGING hostnames served by the home cluster.
#
# Sequence (each arrow is a separate reviewed step; delegation and apply are explicit approvals):
#   Route 53 export/diff → this root creates the zones with identical DNS-only records → plan review
#   → Steve delegates driftplain.dev at Porkbun (modicum.cloud later) → verification → the in-cluster
#   connector (driftplain-gitops charts/home-server-cloudflared) joins the tunnel → staging hosts
#   are exercised. The runtime hostnames (driftplain.dev, api.driftplain.dev, modicum.cloud …)
#   remain CNAME twins of the Route 53 aliases until HM7.
#
# Origin TLS is verified end to end: the connector dials the F5 ingress over HTTPS, checks the
# certificate against the mounted home-server-ca pool with the private staging SNI, and never
# sets noTLSVerify. No Cloudflare Access sits in front of the API or OAuth callbacks. The API
# staging host is served uncached so chat streaming and auth headers pass through untouched.

locals {
  zone_records = merge([for zone, records in var.zone_records : {
    for idx, record in records : "${zone}/${record.type}/${record.name}/${idx}" => merge(record, { zone = zone })
  }]...)

  mirrored_names = { for zone, records in var.zone_records : zone => toset([for r in records : r.name]) }
  staging_hosts  = var.tunnel_enabled ? var.staging_hosts : {}

  # Zones that need a "bypass cache" rule: any staging host with cache = false.
  uncached_hosts = { for zone in var.zones : zone =>
    sort([for role, host in local.staging_hosts : host.hostname if host.zone == zone && !host.cache])
  }
}

resource "cloudflare_zone" "product" {
  for_each = var.zones
  name     = each.key
  type     = "full"
  account = {
    id = var.cloudflare_account_id
  }
  lifecycle {
    prevent_destroy = true
  }
}

# ── DNS-only twins of the Route 53 records (AWS origin until HM7) ─────────────────────────────
resource "cloudflare_dns_record" "mirrored" {
  for_each = local.zone_records
  zone_id  = cloudflare_zone.product[each.value.zone].id
  name     = each.value.name
  type     = each.value.type
  content  = each.value.content
  ttl      = each.value.ttl
  proxied  = each.value.proxied
  comment  = each.value.comment
  lifecycle {
    precondition {
      condition     = !each.value.proxied
      error_message = "Mirrored records stay DNS-only (grey cloud): AWS is the origin until HM7."
    }
    precondition {
      condition     = each.value.name == each.value.zone || endswith(each.value.name, ".${each.value.zone}")
      error_message = "A mirrored record must live inside its own zone."
    }
    precondition {
      condition     = !contains(["NS", "SOA"], each.value.type) || each.value.name != each.value.zone
      error_message = "Apex NS/SOA are Cloudflare-owned; never mirror them."
    }
  }
}

# ── Zone-wide TLS posture (edge → origin verified; AWS ingress already serves trusted certs) ─
locals {
  zone_settings = {
    ssl              = "strict"
    min_tls_version  = "1.2"
    always_use_https = "on"
  }
  zone_setting_pairs = merge([for zone in var.zones : {
    for setting, value in local.zone_settings : "${zone}/${setting}" => { zone = zone, setting = setting, value = value }
  }]...)
}

resource "cloudflare_zone_setting" "posture" {
  for_each   = local.zone_setting_pairs
  zone_id    = cloudflare_zone.product[each.value.zone].id
  setting_id = each.value.setting
  value      = each.value.value
}

# ── The named tunnel (remotely managed) and its ingress rules ─────────────────────────────────
resource "cloudflare_zero_trust_tunnel_cloudflared" "home_server" {
  count      = var.tunnel_enabled ? 1 : 0
  account_id = var.cloudflare_account_id
  name       = var.tunnel_name
  config_src = "cloudflare"
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "home_server" {
  count      = var.tunnel_enabled ? 1 : 0
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.home_server[0].id
  config = {
    ingress = concat(
      [for role in sort(keys(local.staging_hosts)) : {
        hostname = local.staging_hosts[role].hostname
        service  = var.home_ingress_origin
        origin_request = {
          ca_pool            = var.home_origin_ca_pool_path
          origin_server_name = local.staging_hosts[role].origin_server_name
          http_host_header   = local.staging_hosts[role].origin_server_name
          no_tls_verify      = false
          connect_timeout    = 10
          tls_timeout        = 10
          http2_origin       = false
        }
      }],
      [{ service = "http_status:404" }],
    )
  }
  lifecycle {
    precondition {
      condition     = startswith(var.home_ingress_origin, "https://")
      error_message = "The connector must dial the home ingress over TLS."
    }
  }
}

resource "cloudflare_dns_record" "staging" {
  for_each = local.staging_hosts
  zone_id  = cloudflare_zone.product[each.value.zone].id
  name     = each.value.hostname
  type     = "CNAME"
  content  = "${cloudflare_zero_trust_tunnel_cloudflared.home_server[0].id}.cfargotunnel.com"
  ttl      = 1 # automatic; proxied records ignore the TTL
  proxied  = true
  comment  = "HM5 staging host served by the home cluster through the named tunnel"
  lifecycle {
    precondition {
      condition     = endswith(each.value.hostname, ".${each.value.zone}")
      error_message = "A staging host must be a subdomain of its zone."
    }
    precondition {
      condition     = !contains(local.mirrored_names[each.value.zone], each.value.hostname)
      error_message = "Staging hosts must not collide with the mirrored runtime records (public cutover is HM7)."
    }
    precondition {
      condition     = endswith(each.value.origin_server_name, ".home-server.driftplain.dev")
      error_message = "The origin SNI must be one of the private home-server ingress names."
    }
  }
}

# ── API staging is never cached: chat streams and auth headers pass through unchanged ────────
resource "cloudflare_ruleset" "bypass_cache" {
  for_each = { for zone, hosts in local.uncached_hosts : zone => hosts if length(hosts) > 0 }
  zone_id  = cloudflare_zone.product[each.key].id
  name     = "home-server staging: API uncached"
  kind     = "zone"
  phase    = "http_request_cache_settings"
  rules = [{
    action      = "set_cache_settings"
    description = "Bypass the cache for the API staging host (streaming, auth, OAuth callbacks)"
    enabled     = true
    expression  = "(http.host in {${join(" ", [for h in each.value : "\"${h}\""])}})"
    action_parameters = {
      cache = false
    }
  }]
}

# The connector token is read here only so the operator can seal it for the cluster; it is
# sensitive state, never printed by the runbook except into kubeseal's stdin.
data "cloudflare_zero_trust_tunnel_cloudflared_token" "home_server" {
  count      = var.tunnel_enabled ? 1 : 0
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.home_server[0].id
}
