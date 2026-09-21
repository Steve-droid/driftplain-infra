# E21/HM5+HM7 — Cloudflare authoritative DNS for both domains plus the named tunnel and every
# public hostname served by the home cluster: the runtime pair (driftplain.dev,
# api.driftplain.dev — cut over from AWS in HM7 after the September 21 compute retirement) and
# the staging pair (staging.driftplain.dev, api-staging.driftplain.dev — HM5).
#
# Sequence (each arrow was a separate reviewed step; delegation and apply are explicit approvals):
#   Route 53 export/diff → this root creates the zones with identical DNS-only twins → plan review
#   → Steve delegated driftplain.dev at Porkbun (September 18; modicum.cloud stays undelegated)
#   → the in-cluster connector (driftplain-gitops charts/home-server-cloudflared) joined the
#   tunnel → staging hosts exercised → HM7: the runtime pair became tunnel hosts (the dangling
#   NLB CNAME twins disappeared from the Route 53 render). modicum.cloud / api.modicum.cloud are
#   NOT routed (Steve, September 18); HM8 decides that zone.
#
# Origin TLS is verified end to end: the connector dials the F5 ingress over HTTPS, checks the
# certificate against the mounted home-server-ca pool with the private branded SNI, and never
# sets noTLSVerify. No Cloudflare Access sits in front of the API or OAuth callbacks. The API
# hosts are served uncached so chat streaming and auth headers pass through untouched.

locals {
  zone_records = merge([for zone, records in var.zone_records : {
    for idx, record in records : "${zone}/${record.type}/${record.name}/${idx}" => merge(record, { zone = zone })
  }]...)

  # Address-bearing twins (A/AAAA/CNAME) a tunnel host may not collide with; a TXT proof at the
  # apex coexists with the flattened apex CNAME.
  mirrored_address_names = { for zone, records in var.zone_records : zone =>
    toset([for r in records : r.name if contains(["A", "AAAA", "CNAME"], r.type)])
  }
  tunnel_hosts = var.tunnel_enabled ? var.tunnel_hosts : {}

  # Zones that need a "bypass cache" rule: any tunnel host with cache = false.
  uncached_hosts = { for zone in var.zones : zone =>
    sort([for role, host in local.tunnel_hosts : host.hostname if host.zone == zone && !host.cache])
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

# ── DNS-only twins of the retained Route 53 records (ownership proofs; no runtime address) ───
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
      error_message = "Mirrored records stay DNS-only (grey cloud): only tunnel hosts are proxied."
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
      [for role in sort(keys(local.tunnel_hosts)) : {
        hostname = local.tunnel_hosts[role].hostname
        service  = var.home_ingress_origin
        origin_request = {
          ca_pool            = var.home_origin_ca_pool_path
          origin_server_name = local.tunnel_hosts[role].origin_server_name
          http_host_header   = local.tunnel_hosts[role].origin_server_name
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

# HM7 renamed the resource and the staging keys, and the re-rendered twin file re-indexed the
# Google TXT proof (the NLB twins before it disappeared); the moved blocks keep the September 18
# records instead of recreating them.
moved {
  from = cloudflare_dns_record.mirrored["driftplain.dev/TXT/driftplain.dev/2"]
  to   = cloudflare_dns_record.mirrored["driftplain.dev/TXT/driftplain.dev/0"]
}

moved {
  from = cloudflare_dns_record.staging["app"]
  to   = cloudflare_dns_record.tunnel["staging_app"]
}

moved {
  from = cloudflare_dns_record.staging["api"]
  to   = cloudflare_dns_record.tunnel["staging_api"]
}

resource "cloudflare_dns_record" "tunnel" {
  for_each = local.tunnel_hosts
  zone_id  = cloudflare_zone.product[each.value.zone].id
  name     = each.value.hostname
  type     = "CNAME"
  content  = "${cloudflare_zero_trust_tunnel_cloudflared.home_server[0].id}.cfargotunnel.com"
  ttl      = 1 # automatic; proxied records ignore the TTL
  proxied  = true
  comment  = each.value.comment
  lifecycle {
    precondition {
      condition     = each.value.hostname == each.value.zone || endswith(each.value.hostname, ".${each.value.zone}")
      error_message = "A tunnel host must be its zone's apex (flattened CNAME) or a subdomain of it."
    }
    precondition {
      condition     = !contains(local.mirrored_address_names[each.value.zone], each.value.hostname)
      error_message = "A tunnel host must not collide with a mirrored A/AAAA/CNAME twin (re-render records.tfvars.json first)."
    }
    precondition {
      condition     = endswith(each.value.origin_server_name, ".home-server.driftplain.dev")
      error_message = "The origin SNI must be one of the private home-server ingress names."
    }
  }
}

# ── API hosts are never cached: chat streams and auth headers pass through unchanged ─────────
resource "cloudflare_ruleset" "bypass_cache" {
  for_each = { for zone, hosts in local.uncached_hosts : zone => hosts if length(hosts) > 0 }
  zone_id  = cloudflare_zone.product[each.key].id
  # The name is immutable in provider v5 (a rename replaces the ruleset); it predates HM7.
  name  = "home-server staging: API uncached"
  kind  = "zone"
  phase = "http_request_cache_settings"
  rules = [{
    action      = "set_cache_settings"
    description = "Bypass the cache for the API hosts (streaming, auth, OAuth callbacks)"
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
