variable "cloudflare_account_id" {
  description = "Cloudflare account that owns both zones and the tunnel (TF_VAR_cloudflare_account_id; not committed)."
  type        = string
  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.cloudflare_account_id))
    error_message = "Pass the 32-hex Cloudflare account ID via TF_VAR_cloudflare_account_id."
  }
}

variable "zones" {
  description = "Registered domains whose authoritative DNS moves to Cloudflare (registration stays at Porkbun)."
  type        = set(string)
  validation {
    condition = alltrue([for z in var.zones :
      can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}$", z))
    ])
    error_message = "Zones are lowercase DNS domains without scheme, path or trailing dot."
  }
}

variable "zone_records" {
  description = "Per zone, the DNS-only twins of the Route 53 records (rendered by dns/scripts/route53-cloudflare-sync.py into records.tfvars.json; AWS stays the origin)."
  type = map(list(object({
    name    = string
    type    = string
    content = string
    ttl     = number
    proxied = bool
    comment = optional(string)
  })))
}

variable "tunnel_enabled" {
  description = "Create the named tunnel, its ingress rules and the proxied staging hostnames."
  type        = bool
}

variable "tunnel_name" {
  description = "Name of the remotely managed cloudflared tunnel served by the home cluster."
  type        = string
}

variable "staging_hosts" {
  description = "Public staging hostnames routed through the tunnel to the home ingress; the runtime hostnames are never listed here before HM7."
  type = map(object({
    zone               = string
    hostname           = string
    origin_server_name = string
    cache              = bool
  }))
}

variable "home_ingress_origin" {
  description = "In-cluster origin the connector dials: the F5 nginx-ingress ClusterIP Service over HTTPS."
  type        = string
  validation {
    condition     = startswith(var.home_ingress_origin, "https://")
    error_message = "The connector must reach the home ingress over TLS (https://...), never plain HTTP."
  }
}

variable "home_origin_ca_pool_path" {
  description = "Path inside the connector pod where the GitOps chart mounts the home-server-ca certificate (PEM)."
  type        = string
}
