# Cloudflare authoritative DNS + the home-server tunnel (E21/HM5, HM7)

**HM7 (September 22, 2026): the runtime pair `driftplain.dev` / `api.driftplain.dev` joins the
tunnel.** AWS compute was retired on September 21 ([decision record](../home-server/AWS-COMPUTE-RETIREMENT.md)),
so the four NLB CNAME twins point at a deleted load balancer and are dropped; the tunnel now
carries four hostnames (runtime + staging) to the same two private ingress names. The apply is
a separate approval (plan below); modicum.cloud stays un-routed and undelegated. Cutover evidence
and runbook: [`../home-server/HM7-CUTOVER.md`](../home-server/HM7-CUTOVER.md).

**Applied September 18, 2026 (18 resources). `driftplain.dev` delegated and active September 18; `modicum.cloud` stays undelegated by decision (September 18).** Zones
`driftplain.dev` (`c9ee1f87408620ad3191e7a97ee8fa0d`) and `modicum.cloud`
(`b0d05c69806c8d36f85a1e89b118ca08`) answer on `fatima.ns.cloudflare.com` / `seth.ns.cloudflare.com`;
tunnel `driftplain-home-server` (`a27459ba-d201-4437-a963-f63ff3d49796`) has served
`staging.driftplain.dev` and `api-staging.driftplain.dev` through the gitops connector (v0.28.0)
since then. Route 53 (`../dns/`) keeps the retained zones (aliases disabled) until the HM8 review.
Evidence: [`../home-server/hm5-cloudflare-evidence.json`](../home-server/hm5-cloudflare-evidence.json).
The scoped API token lives in `~/.config/driftplain/cloudflare.env` (mode 0600, sourced by the
operator, never printed or committed).

This fifth Terraform root has its own state key, `cloudflare/terraform.tfstate`. It owns:

- **both zones** (`driftplain.dev`, `modicum.cloud`) as full-setup zones with **DNS-only twins**
  of every non-provider Route 53 record. Since HM7 that is only the Google ownership TXT proof:
  the NLB aliases were dropped with the load balancer, and a twin that carried an address
  (A/AAAA/CNAME) may not collide with a tunnel hostname (precondition). Apex NS/SOA are
  Cloudflare's own. Both Route 53 zones are `NOT_SIGNING` (no DNSSEC, no DS at the parent).
- the zone posture: `ssl=strict`, `min_tls_version=1.2`, `always_use_https=on`.
- the **named tunnel** `driftplain-home-server` (remotely managed config) whose ingress rules are
  the `tunnel_hosts` of `dev.tfvars`: runtime `driftplain.dev` / `api.driftplain.dev` (HM7) and
  staging `staging.driftplain.dev` / `api-staging.driftplain.dev` (HM5), app hosts →
  `app.home-server.driftplain.dev`, API hosts → `api.home-server.driftplain.dev`, all dialing the
  F5 nginx-ingress ClusterIP Service over HTTPS with `caPool=/etc/cloudflared/ca/home-server-ca.crt`
  and the private SNI/Host header (the SNI must end in `.home-server.driftplain.dev`),
  `noTLSVerify=false`, catch-all `http_status:404`. The API hosts share one cache-bypass rule
  (its Cloudflare name keeps the original "staging" wording because the name is immutable).
  No Cloudflare Access anywhere.
- the proxied tunnel CNAMEs to `<tunnel>.cfargotunnel.com` (the apex is a flattened CNAME) and
  the `tunnel_token` output the operator seals for the GitOps connector
  (`driftplain-gitops charts/home-server-cloudflared`). `tunnel_urls` lists the routed URLs by role.

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

The S3 backend needs `AWS_PROFILE=saa`. First plan (September 18): 2 zones, 5 mirrored records,
6 zone settings, 1 tunnel + 1 config, 2 staging CNAMEs, 1 cache ruleset — 19 additions. HM7 plan
(September 22): **2 to add** (the runtime CNAMEs `tunnel["app"]`, `tunnel["api"]`), **3 to change**
(TXT comment, bypass expression, tunnel ingress), **4 to destroy** (the dangling NLB twins); the
staging records and the TXT proof move to their new addresses without replacement (`moved` blocks).
Zones carry `prevent_destroy`. Mocked tests (no account, no network):

```sh
terraform -chdir=cloudflare test -var-file=dev.tfvars -var-file=records.tfvars.json
```

## Sequence after Steve's approvals

1. Done September 18: `terraform apply cloudflare.tfplan`; `terraform output name_servers` gives the
   two nameservers above.
2. Done September 18: the Cloudflare zones answer identically to Route 53 for every twin
   (`dig @fatima.ns.cloudflare.com driftplain.dev A`, `api.driftplain.dev A`, `driftplain.dev TXT`,
   `modicum.cloud A`, `api.modicum.cloud A`; the apex CNAME flattens to the NLB addresses).
3. Done September 18 for **driftplain.dev** (Steve changed the Porkbun nameservers; Registry DNSSEC
   holds no DS records). The `.dev` parent answered with the Cloudflare pair within a minute, the
   zone went `active` after an `activation_check`, and trusted HTTPS on `https://driftplain.dev` and
   `https://api.driftplain.dev` still terminates at the AWS NLB. Public resolvers follow their cached
   NS TTL (8.8.8.8 and 9.9.9.9 switched within ten minutes; 1.1.1.1 still held the Route 53 set).
   modicum.cloud will not be delegated (Steve, September 18: the domain is not needed; the zone
   stays applied and unused). Route 53 zones stay until HM8 retirement review.
4. Done September 18 (gitops v0.28.0). Seal the connector token and enable the GitOps child:
   `terraform -chdir=cloudflare output -raw tunnel_token | home-server-sealing-keys.py seal-value --namespace cloudflared --name home-server-cloudflared-token --key token --cert <controller.pem> --out <file>`
   (strict scope; the value travels on stdin only), copy `encryptedData.token` into
   `sealed.encryptedToken`, set `enabled: true` in the chart values, merge.
5. Done September 18 (gitops v0.29.0 added the `staging` host set). HTML, `/healthz`, `/readyz`,
   login and a chat exchange all answer 200 through the edge with `cf-cache-status: DYNAMIC`; the
   connector restart rolled in 16 s with the edge answering throughout; CORS preflight from
   `https://staging.driftplain.dev` is allowed. Results in
   [`../home-server/hm5-staging-evidence.json`](../home-server/hm5-staging-evidence.json). Google
   sign-in on staging stays a separate authorization.
6. HM7 (September 22, after the final export was restored and validated at home): approval →
   `terraform apply cloudflare.tfplan` (runtime CNAMEs resolve to the tunnel; the 404 catch-all
   answers until the gitops merge) → merge the gitops `runtimeHostSet: driftplain` bump (ingress
   rules for the runtime pair, `API_BASE_URL`/`PUBLIC_BASE_URL`, CORS, four edge probes) → validate
   from outside the LAN. Order matters: applying gitops first would point staging's `config.js`
   at an unresolvable `api.driftplain.dev` and fail the edge probes. Results in
   [`../home-server/HM7-CUTOVER.md`](../home-server/HM7-CUTOVER.md).

Quick-tunnel witness (no account needed) proving the connector image, the mounted CA pool and the
verified-TLS path: [`../home-server/hm5-quick-tunnel-witness.yaml`](../home-server/hm5-quick-tunnel-witness.yaml)
and its evidence in `../home-server/hm5-quick-tunnel-evidence.json`.
