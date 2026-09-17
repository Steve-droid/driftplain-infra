# Cloudflare authoritative DNS + the home-server tunnel (E21/HM5)

**Prepared September 17, 2026 — not applied.** Applying needs Steve's Cloudflare account and a
scoped API token; delegating either domain at Porkbun is a separate explicit approval. Until then
Route 53 (`../dns/`) stays authoritative and AWS stays the origin for every runtime hostname.

This fifth Terraform root has its own state key, `cloudflare/terraform.tfstate`. It owns:

- **both zones** (`driftplain.dev`, `modicum.cloud`) as full-setup zones with **DNS-only twins**
  of every non-provider Route 53 record (the four NLB aliases become CNAMEs to the same NLB with
  Cloudflare's apex flattening; the Google ownership TXT proof is carried over; apex NS/SOA are
  Cloudflare's own). Both Route 53 zones are `NOT_SIGNING` (no DNSSEC, no DS at the parent), so
  nothing has to be unsigned before delegation.
- the zone posture: `ssl=strict`, `min_tls_version=1.2`, `always_use_https=on`.
- the **named tunnel** `driftplain-home-server` (remotely managed config) whose only ingress rules
  are the staging hosts `staging.driftplain.dev` → `app.home-server.driftplain.dev` and
  `api-staging.driftplain.dev` → `api.home-server.driftplain.dev`, both dialing the F5
  nginx-ingress ClusterIP Service over HTTPS with `caPool=/etc/cloudflared/ca/home-server-ca.crt`
  and the private SNI/Host header, `noTLSVerify=false`, catch-all `http_status:404`. The API host
  gets a cache-bypass rule. No Cloudflare Access anywhere.
- the proxied staging CNAMEs to `<tunnel>.cfargotunnel.com` and the `tunnel_token` output the
  operator seals for the GitOps connector (`driftplain-gitops charts/home-server-cloudflared`).

Inputs have no defaults; every command passes **two** var-files: `dev.tfvars` (scalars) and
`records.tfvars.json` (the rendered record twins). The account ID and token are environment only.

## Export, render, diff (read-only, run before every plan)

```sh
source ~/.local/share/driftplain/home-server-identity/operator.env
cd ~/bootcamp/portfolio/driftplain-infra
"$HOME_SERVER_PYTHON" dns/scripts/route53-cloudflare-sync.py export --out /tmp/r53-export.json
"$HOME_SERVER_PYTHON" dns/scripts/route53-cloudflare-sync.py render --export /tmp/r53-export.json --out cloudflare/records.tfvars.json
"$HOME_SERVER_PYTHON" dns/scripts/route53-cloudflare-sync.py diff   --export /tmp/r53-export.json --records cloudflare/records.tfvars.json
```

`export` also records the live delegation (`dig NS/DS @1.1.1.1`) and DNSSEC status per zone;
`diff` exits 1 when the committed twin file drifted from Route 53. Unit tests:
`"$HOME_SERVER_PYTHON" dns/scripts/test-route53-cloudflare-sync.py`.

## Plan (needs the account; still no apply)

```sh
export CLOUDFLARE_API_TOKEN=…            # scoped token from Steve's Cloudflare account, never committed
export TF_VAR_cloudflare_account_id=…    # 32-hex account ID
terraform -chdir=cloudflare init -input=false
terraform -chdir=cloudflare plan -var-file=dev.tfvars -var-file=records.tfvars.json -out=cloudflare.tfplan
```

Expected first plan: 2 zones, 5 mirrored records, 6 zone settings, 1 tunnel + 1 config,
2 staging CNAMEs, 1 cache ruleset — **19 additions, 0 changes, 0 destroys**. `tunnel_enabled=false`
drops the last five (13 additions) if the tunnel is to follow the delegation. Zones carry
`prevent_destroy`. Mocked tests (no account, no network):

```sh
terraform -chdir=cloudflare test -var-file=dev.tfvars -var-file=records.tfvars.json
```

## Sequence after Steve's approvals

1. `terraform apply cloudflare.tfplan` (explicit approval). Read `terraform output name_servers`.
2. Verify the Cloudflare zones answer identically to Route 53 for every twin before delegating
   (`dig @<cloudflare-ns> driftplain.dev A`, `api.driftplain.dev A`, `driftplain.dev TXT`).
3. Delegate **driftplain.dev first** at Porkbun to the two Cloudflare nameservers (explicit
   approval; Route 53 keeps serving until the registrar change propagates). Verify trusted HTTPS on
   `https://driftplain.dev` and `https://api.driftplain.dev` still terminates at the AWS NLB, then
   modicum.cloud later. Route 53 zones stay until HM8 retirement review.
4. Seal the connector token and enable the GitOps child:
   `terraform -chdir=cloudflare output -raw tunnel_token | home-server-sealing-keys.py seal … --namespace cloudflared --name home-server-cloudflared-token --key token`
   (strict scope), set `enabled: true` + `sealed.encryptedToken` in the chart values, merge.
5. Exercise the staging hosts (HTML, API health, chat streaming, restart of the connector pod, CORS
   and OAuth callback behaviour) — the runtime hostnames are untouched until HM7.

Quick-tunnel witness (no account needed) proving the connector image, the mounted CA pool and the
verified-TLS path: [`../home-server/hm5-quick-tunnel-witness.yaml`](../home-server/hm5-quick-tunnel-witness.yaml)
and its evidence in `../home-server/hm5-quick-tunnel-evidence.json`.
