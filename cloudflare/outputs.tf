output "name_servers" {
  description = "Per zone, the Cloudflare nameservers to enter at Porkbun (delegation is a separate explicit approval)."
  value       = { for zone, z in cloudflare_zone.product : zone => z.name_servers }
}

output "zone_ids" {
  value = { for zone, z in cloudflare_zone.product : zone => z.id }
}

output "mirrored_record_count" {
  description = "How many DNS-only twins of the Route 53 records this root manages."
  value       = length(cloudflare_dns_record.mirrored)
}

output "tunnel_id" {
  value = var.tunnel_enabled ? cloudflare_zero_trust_tunnel_cloudflared.home_server[0].id : null
}

output "tunnel_urls" {
  description = "Per role, the public URL served by the home cluster through the tunnel."
  value       = { for role, host in cloudflare_dns_record.tunnel : role => "https://${host.name}" }
}

output "tunnel_token" {
  description = "Connector token for the GitOps SealedSecret (terraform output -raw tunnel_token | kubeseal …)."
  sensitive   = true
  value       = var.tunnel_enabled ? data.cloudflare_zero_trust_tunnel_cloudflared_token.home_server[0].token : null
}
