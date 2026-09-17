# Driftplain and Modicum public DNS

**September 12, 2026 — both domains registered at Porkbun, delegated to Route 53 and verified over trusted HTTPS.**
App: `https://modicum.cloud`; API: `https://api.modicum.cloud`.

> **E21/HM5 (September 17, 2026):** authoritative DNS for both domains is prepared to move to
> Cloudflare with AWS still the origin — see [`../cloudflare/README.md`](../cloudflare/README.md).
> `scripts/route53-cloudflare-sync.py` exports these zones read-only, renders their Cloudflare
> twins and diffs them; this root keeps serving until the separately approved delegation change.
The failed AWS registration remains a separate open billing-support case. Do not retry
registration, transfer the domain or wait for Support before connecting DNS.

This fourth Terraform root has its own S3 state key, `dns/terraform.tfstate`. It owns a
persistent public zone and two A alias records pointing to the **existing** Kubernetes-owned
public ingress NLB. The load balancer is a data source, never managed by this root. There is no
new load balancer, TLS termination change, ExternalDNS controller, or static AWS credential.
Route 53 is global; the target NLB is in `ap-south-1`. Inputs have no defaults; every plan/apply/test
uses `-var-file=dev.tfvars`. Credentials come from `AWS_PROFILE=saa` on the laptop.

## P38r additional domain — delegated September 12, 2026

Modicum DNS is live. `additional_domains` adds **driftplain.dev** plus **api.driftplain.dev**
inside this same persistent state, leaving the original zone and aliases untouched. The new
registration, delegation, trusted TLS and the runtime cutover are complete.
The fresh plan is **3 add / 0 change / 0 destroy**. Use the same explicit `-var-file=dev.tfvars`.
For the deployed zone, read `terraform -chdir=dns output -json additional_domains`
and delegate driftplain.dev to its actual four nameservers. Do not change Modicum's delegation.

`records_enabled=false` removes **all four** app/API aliases, retains both zones, and does not query
a missing NLB. Rebuild discovery refreshes the shared NLB target for both domains. Removing a zone
from the map is blocked by prevent_destroy; retirement is a separate explicit decision.

The original P38m steps below are retained as the first-domain runbook/history. The new rollout
uses `global.additionalHosts.driftplain.enabled=true` to stage certificates, followed by
`global.runtimeHostSet=driftplain` only after TLS and Google origins are verified. Rollback the
runtime selector to an empty string, keeping additional hosts enabled for issued URLs/certificates.

## Create the zone, then delegate at Porkbun

Registration is complete at Porkbun. It does not create a Route 53 zone. Read-only checks on
2026-09-09 found no matching AWS zone and no `dns/` state object. Existing delegation is to
Porkbun's four nameservers. Recheck just before applying; if a zone or state has appeared,
inspect ownership and reconcile it instead of creating a duplicate zone. Import is only for
an actual existing zone that this stack is meant to own, never an assumed registration zone.

```sh
export AWS_PROFILE=saa
aws sts get-caller-identity --query Account --output text
aws route53 list-hosted-zones-by-name --dns-name modicum.cloud \
  --query "HostedZones[?Name=='modicum.cloud.'].{Id:Id,Name:Name,Private:Config.PrivateZone}"
aws s3api list-objects-v2 --bucket modelmatch-tfstate-957261948820 \
  --prefix dns/ --query 'Contents[].Key'
terraform -chdir=dns init -input=false -lockfile=readonly
terraform -chdir=dns plan -var-file=dev.tfvars -out=dns.tfplan
```

Expected first plan: **3 additions / 0 changes / 0 destroys** — one public zone and two A
aliases only. Check the AWS account and Service-to-NLB discovery before reviewing the plan.
Steve's required slice review precedes commits/release. After review, apply the exact saved
plan (`terraform -chdir=dns apply dns.tfplan`; the saved plan already contains the explicit
var-file inputs), then read `terraform -chdir=dns output name_servers`. Saved plans are local
and ignored by Git. If inputs or live state change, regenerate and inspect the plan.

At Porkbun, in Safari, open modicum.cloud's **authoritative nameserver** settings and replace
all registrar nameservers with the four values returned by this actual Terraform zone.
Do not enter NS records in the old Porkbun DNS zone as a substitute for registrar delegation.
Complete contact-email verification if the registrar indicates it is outstanding; Steve handles
private account details. Inspect DNSSEC/DS before delegation; an old provider's DS record must
not remain when moving to an unsigned zone. No contact details, login links or credentials belong
in source or logs. Keep the domain and AWS billing-support case separate.

Verify the new zone directly and the parent delegation/public resolvers during propagation:

```sh
# Replace ROUTE53_NS with one of this zone's actual nameservers.
dig @ROUTE53_NS modicum.cloud A +norecurse
dig @ROUTE53_NS api.modicum.cloud A +norecurse
dig +trace modicum.cloud NS
dig @1.1.1.1 modicum.cloud A
dig @8.8.8.8 api.modicum.cloud A
```

Public zone hosting and Porkbun domain renewal have separate recurring charges; registrar
renewal settings remain the user's choice. The zone persists through platform teardown.

## Refresh the target after a platform rebuild

```sh
AWS_PROFILE=saa python3 scripts/discover-ingress-dns.py \
  --region ap-south-1 --account-id 957261948820 \
  --context arn:aws:eks:ap-south-1:957261948820:cluster/modelmatch
```

The script only reads the selected Kubernetes ingress Service and AWS load balancers. It requires
one matching public NLB and prints its ARN, DNS name, and canonical zone ID. Review/update
`dns/dev.tfvars` with that ARN, then review a fresh plan before apply. Do not select an arbitrary
LB from the account or pin an IP. The initial ARN was read from AWS and verified against the
Kubernetes Service during P38m. Public names stay stable across LB replacement; their target
must be refreshed, so a rebuild is not automatically zero-downtime.

## Stage HTTPS, then switch URLs

1. After slice review, apply the DNS-only plan and delegate Porkbun to the new zone.
2. Publish the GitOps chart change with `global.appHost=modicum.cloud`,
   `apiHost=api.modicum.cloud`, `useCustomHosts=false`, and `retainSslipHosts=true`.
   This creates two new F5 master/minion pairs and independent TLS secrets. Existing ingress
   objects and API URLs remain intact. cert-manager continues using Let's Encrypt HTTP-01.
3. Verify DNS targets/resolution and that `modelmatch-app-tls-branded` and
   `modelmatch-api-tls-branded` certificates are Ready, then check HTTPS **without `-k`**:

   ```sh
   dig modicum.cloud A
   dig api.modicum.cloud A
   AWS_PROFILE=saa kubectl --context arn:aws:eks:ap-south-1:957261948820:cluster/modelmatch -n app get ingress,certificate
   curl --fail --silent --show-error https://modicum.cloud/ > /dev/null
   curl --fail --silent --show-error https://api.modicum.cloud/readyz
   ```

4. Switch `global.useCustomHosts=true` in a separate reviewed GitOps commit. ArgoCD rolls the
   FE/BE ConfigMap consumers; the frontend API URL and backend `PUBLIC_BASE_URL` now use the
   branded API. CORS allows the branded and original app origins. Verify login, both setup flows,
   new snippets, dashboard reads and CORS without rotating credentials. Verify old API URLs
   directly too. Do not invoke chat/ingestion models or add synthetic CI runs for DNS proof.
   DNS requires no app image rebuild, migration, seed, or postgres Application sync.
5. Record ArgoCD revision, certificate Ready state, DNS targets, and app checks before advertising
   the URL or taking P39 captures. A new origin has separate browser storage: sign in again.

New CI snippets use `api.modicum.cloud`; existing pasted snippets still call the old sslip.io API,
which remains served directly, without a POST redirect. Keep both hosts until users migrate.
`recompute-host.sh` refuses to rewrite the legacy IP while branded hosts are configured.

## Rollback and final teardown

Rollback the runtime switch to `useCustomHosts=false`, keeping both ingress sets and DNS intact.
This restores previous public API configuration without deleting certificates or changing data.
Do not immediately remove branded ingress/DNS if any clients have already adopted those names.

Before P45 platform teardown, set `records_enabled=false`, review and apply the two-record deletion
plan. This does not query the NLB, so it works after an emergency kill-switch teardown too. The
zone has `prevent_destroy`; the domain and zone survive platform teardown and continue their own
registration/hosting billing. After an automatic budget teardown, reconcile these aliases manually.
Final zone deletion or cancellation of domain renewal is a separate explicit decision. Never remove
the zone from Terraform state as a substitute for deciding its lifecycle.

## Offline checks

```sh
terraform -chdir=dns fmt -check
terraform -chdir=dns validate
terraform -chdir=dns test -var-file=dev.tfvars
```

Terraform tests mock the AWS provider: aliases/target wiring, disabling records after teardown,
and rejecting out-of-zone or duplicate hostnames. They create no AWS resources.

Google Search Console supplies `additional_domains["driftplain.dev"].verification_txt`.
This is a public DNS ownership token, retained independently of the NLB aliases so ownership
verification survives `records_enabled=false` and platform teardown. Existing Google HTML
verification at the application remains available. Add/change only the actual provider-issued
proof, then review an additive plan before apply; do not generate a token or rotate credentials.

During propagation, cached resolvers continued asking Porkbun's old nameservers. Its apex ALIAS
and an exact `api.driftplain.dev` CNAME now also point to the existing NLB DNS name (TTL 600),
allowing HTTP-01 validation and old caches to reach the same ingress. Route 53 delegation was
never reverted; no IP was pinned. The original unused wildcard parking record remains at
Porkbun. Route 53 remains authoritative and Terraform owns both zones, aliases and Google's
public TXT proof. These registrar compatibility records do not replace the Terraform records.
