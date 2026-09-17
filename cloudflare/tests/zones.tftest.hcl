# Provider is mocked: no account, token or network. `apply` against the mock resolves computed
# IDs so the record/tunnel wiring can be asserted; nothing real is created.
mock_provider "cloudflare" {
  # The provider validates ID shapes even against mocks: give the computed IDs realistic forms.
  mock_resource "cloudflare_zone" {
    defaults = {
      id           = "9f2c1b4d8e7a6f5c4d3b2a1908f7e6d5"
      name_servers = ["ada.ns.cloudflare.com", "bob.ns.cloudflare.com"]
    }
  }
  mock_resource "cloudflare_zero_trust_tunnel_cloudflared" {
    defaults = {
      id = "5f4e3d2c-1b0a-4987-8765-43210fedcba9"
    }
  }
  mock_data "cloudflare_zero_trust_tunnel_cloudflared_token" {
    defaults = {
      token = "mock-token-never-real"
    }
  }
}

variables {
  cloudflare_account_id = "0123456789abcdef0123456789abcdef"
}

run "both_zones_mirror_route53_dns_only" {
  command = apply
  assert {
    condition     = keys(cloudflare_zone.product) == ["driftplain.dev", "modicum.cloud"]
    error_message = "Both registered domains must get a Cloudflare zone."
  }
  assert {
    condition     = alltrue([for z in cloudflare_zone.product : z.type == "full"])
    error_message = "Zones are full-setup (authoritative) zones; partial CNAME setup is a paid plan."
  }
  assert {
    condition     = length(cloudflare_dns_record.mirrored) == 5
    error_message = "The rendered twins are four NLB aliases (CNAME) plus the Google TXT proof."
  }
  assert {
    condition     = alltrue([for r in cloudflare_dns_record.mirrored : !r.proxied])
    error_message = "Mirrored (runtime) records stay DNS-only: AWS is the origin until HM7."
  }
  assert {
    condition = alltrue([for r in cloudflare_dns_record.mirrored :
      r.type != "CNAME" || endswith(r.content, ".elb.ap-south-1.amazonaws.com")
    ])
    error_message = "Every mirrored CNAME must point at the existing AWS ingress NLB."
  }
  assert {
    condition     = length([for r in cloudflare_dns_record.mirrored : r if r.type == "TXT" && startswith(r.content, "\"google-site-verification=")]) == 1
    error_message = "The Google ownership proof must be carried over."
  }
  assert {
    condition     = !anytrue([for r in cloudflare_dns_record.mirrored : contains(["NS", "SOA"], r.type)])
    error_message = "Apex NS/SOA are Cloudflare-owned."
  }
  assert {
    condition     = length(cloudflare_zone_setting.posture) == 6 && alltrue([for s in cloudflare_zone_setting.posture : s.value == "strict" if s.setting_id == "ssl"])
    error_message = "Both zones get strict edge-to-origin TLS, TLS 1.2 minimum and HTTPS redirect."
  }
}

run "tunnel_routes_only_staging_hosts_with_verified_origin_tls" {
  command = apply
  assert {
    condition     = length(cloudflare_zero_trust_tunnel_cloudflared.home_server) == 1 && cloudflare_zero_trust_tunnel_cloudflared.home_server[0].config_src == "cloudflare"
    error_message = "One remotely managed tunnel."
  }
  assert {
    condition     = length(cloudflare_dns_record.staging) == 2 && alltrue([for r in cloudflare_dns_record.staging : r.proxied && r.type == "CNAME" && endswith(r.content, ".cfargotunnel.com")])
    error_message = "Staging hosts are proxied CNAMEs to the tunnel."
  }
  assert {
    condition     = toset([for r in cloudflare_dns_record.staging : r.name]) == toset(["staging.driftplain.dev", "api-staging.driftplain.dev"])
    error_message = "Only the two staging hostnames are routed; no runtime host before HM7."
  }
  assert {
    condition = alltrue([for rule in cloudflare_zero_trust_tunnel_cloudflared_config.home_server[0].config.ingress :
      rule.hostname == null || (
        startswith(rule.service, "https://nginx-ingress-controller.nginx-ingress.svc.cluster.local") &&
        rule.origin_request.no_tls_verify == false &&
        rule.origin_request.ca_pool == "/etc/cloudflared/ca/home-server-ca.crt" &&
        endswith(rule.origin_request.origin_server_name, ".home-server.driftplain.dev") &&
        rule.origin_request.http_host_header == rule.origin_request.origin_server_name
      )
    ])
    error_message = "Every hostname rule dials the home ingress over TLS, verifies against home-server-ca with the private SNI, and never sets noTLSVerify."
  }
  assert {
    condition     = cloudflare_zero_trust_tunnel_cloudflared_config.home_server[0].config.ingress[length(cloudflare_zero_trust_tunnel_cloudflared_config.home_server[0].config.ingress) - 1].service == "http_status:404"
    error_message = "The catch-all rule answers 404: cluster administration and unknown hosts are not exposed."
  }
  assert {
    condition     = !anytrue([for rule in cloudflare_zero_trust_tunnel_cloudflared_config.home_server[0].config.ingress : rule.origin_request != null && rule.origin_request.access != null])
    error_message = "No Cloudflare Access on any route (API/OAuth flows stay untouched)."
  }
  assert {
    condition     = keys(cloudflare_ruleset.bypass_cache) == ["driftplain.dev"] && cloudflare_ruleset.bypass_cache["driftplain.dev"].rules[0].action_parameters.cache == false && strcontains(cloudflare_ruleset.bypass_cache["driftplain.dev"].rules[0].expression, "\"api-staging.driftplain.dev\"")
    error_message = "The API staging host bypasses the cache."
  }
}

run "tunnel_disabled_leaves_only_the_dns_mirror" {
  command = apply
  variables {
    tunnel_enabled = false
  }
  assert {
    condition     = length(cloudflare_zero_trust_tunnel_cloudflared.home_server) == 0 && length(cloudflare_dns_record.staging) == 0 && length(cloudflare_ruleset.bypass_cache) == 0
    error_message = "Without the tunnel nothing is proxied; the zones and mirrored records remain."
  }
  assert {
    condition     = length(cloudflare_dns_record.mirrored) == 5 && length(cloudflare_zone.product) == 2
    error_message = "The DNS mirror does not depend on the tunnel."
  }
  assert {
    condition     = output.tunnel_token == null && output.tunnel_id == null
    error_message = "No token without a tunnel."
  }
}

run "reject_routing_a_runtime_host_before_hm7" {
  command = plan
  variables {
    staging_hosts = {
      api = {
        zone               = "driftplain.dev"
        hostname           = "api.driftplain.dev"
        origin_server_name = "api.home-server.driftplain.dev"
        cache              = false
      }
    }
  }
  expect_failures = [cloudflare_dns_record.staging]
}

run "reject_a_staging_host_outside_its_zone" {
  command = plan
  variables {
    staging_hosts = {
      app = {
        zone               = "driftplain.dev"
        hostname           = "staging.modicum.cloud"
        origin_server_name = "app.home-server.driftplain.dev"
        cache              = true
      }
    }
  }
  expect_failures = [cloudflare_dns_record.staging]
}

run "reject_a_plaintext_origin" {
  command = plan
  variables {
    home_ingress_origin = "http://nginx-ingress-controller.nginx-ingress.svc.cluster.local:80"
  }
  expect_failures = [var.home_ingress_origin]
}

run "reject_a_proxied_mirror" {
  command = plan
  variables {
    zone_records = {
      "driftplain.dev" = [{
        name    = "driftplain.dev"
        type    = "CNAME"
        content = "ingress.elb.ap-south-1.amazonaws.com"
        ttl     = 300
        proxied = true
      }]
      "modicum.cloud" = []
    }
  }
  expect_failures = [cloudflare_dns_record.mirrored]
}
