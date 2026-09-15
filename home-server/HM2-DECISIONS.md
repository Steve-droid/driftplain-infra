# HM2 remaining operating decisions — September 15, 2026

**Prepared for Steve's decisions; recommendations are not approvals.** Source review,
image publication, DNS changes, leaf issuance, CRL writes, authentication and scheduling
remain separate gates. HM3 has not started. [Operating design](OPERATING-DESIGN.md) remains
the baseline; this package makes its unresolved choices concrete.

## Images: recommend public GHCR for all four images

Publish reviewed releases under Steve's existing GitHub ownership, retaining compatibility
names: `ghcr.io/steve-droid/modelmatch-frontend`, `modelmatch-backend`, `modelmatch-agent`,
and `modelmatch-agent-security`. All four packages must be explicitly public. Tags aid
discovery; home manifests and generated CI snippets use immutable `@sha256:` references.
The [September 14 inventory](hm2-service-inventory.json) records the exact ECR source
digests for FE/BE 1.0.24 and both agents 1.1.3. Do not invent GHCR destination digests.

HM4 publication contract: inspect image contents/provenance for public release, copy the
approved source manifests/layers without rebuilding the same tag, compare source/destination
digests, and verify anonymous pulls with an empty client auth configuration. Inspect actual
platform manifests; support Linux amd64 first, and do not claim arm64 agent support without
a tested build. The backend helper integration requires a new reviewed image/release.
Keep existing ECR images throughout rollback. HM6 must check both CI task snippets and
user setup from a clean, unauthenticated client. Future CI must publish reviewed releases;
a one-time copy is not a sustainable release process. Publishing credentials stay in CI,
never in a public package or the home pull path.

Alternative: private ECR needs an additional scoped pull identity and renewable registry
authentication, and still leaves public users needing registry access. That is substantially
more ownership work for these public project images. [GitHub documents anonymous container
pulls](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
and [current free public-package/container billing](https://docs.github.com/en/billing/concepts/product-billing/github-packages).

## App configuration secrets: recommend Sealed Secrets

Sealed Secrets encrypts a Kubernetes Secret into a Git-storable resource; its in-cluster
controller decrypts it. Preserve `app/modelmatch-app-secrets` and all four existing values
(`JWT_SECRET`, `POSTGRES_PASSWORD`, `CHAT_READONLY_DB_PASSWORD`, `DEMO_SEED_PASSWORD`).
Preserve the original Google OAuth configuration/custody separately after inventory; do not
assume the four-key ESO resource contains every credential. Keep all seed hooks disabled.

HM3's encrypted credential export is the initial recovery source. HM4 installs the controller
through the home GitOps root, backs up **all** its sealing private keys using the existing age
recipient into versioned S3 plus the Mac, and proves recovery in isolation before depending
on encrypted manifests. Use strict name/namespace sealing. Back up again after sealing-key
renewal; old backups do not contain newly generated keys. Retain keys needed by retained
Git revisions and backups. Steve owns custody/recovery; HM5 owns automated backup/alerts.
Never seal or copy Roles Anywhere leaf keys into Git: the separate operator-managed Secrets
are bootstrapped/renewed independently. Preserve AWS ESO and `modelmatch/app` through rollback.
[Upstream recovery and key-renewal guidance](https://github.com/bitnami/sealed-secrets#how-to-backup-my-sealedsecrets).

Alternative: operator-managed Kubernetes app Secrets with age-encrypted recovery avoid a
controller, but configuration changes/restoration become operator procedures rather than
GitOps reconciliation. Neither option needs extra AWS runtime Secrets Manager permissions.

## Blob and reference behavior: a narrower decision than the old draft

**Verified source fact:** Jenkins setup has been metadata-only since S15c.
[Request schema](../../driftplain-backend/app/schemas/jenkins.py) forbids extra secret fields;
[service](../../driftplain-backend/app/projects/jenkins_service.py) stores only URL/job metadata.
[Existing tests](../../driftplain-backend/tests/test_jenkins.py) cover secret rejection and no
stored refs. The in-memory secret adapter is unused by this public write path. Preserve that
contract, nullable legacy columns, and the five existing CI-token hashes; do not add a new
BYOK vault or silently rotate tokens. HM3 refreshes the data inventory before export.

The actual gap is [ingestion source storage](../../driftplain-backend/app/ingest/service.py):
`POST /benchmarks/ingest` can leave a DB source reference whose bytes exist only in process
memory. The current source bucket is verified empty on September 15; this is not a DB export.

Recommend retaining the ingestion capability with a durable S3 adapter in HM4, using the
existing `modelmatch-ingestion-sources-957261948820` bucket. Content-hash keys, SSE-S3,
idempotent retries, bounded reads and failure-before-catalog-commit need focused tests.
This requires a separately reviewed Bedrock-role/profile permission addition for the exact
source prefix; the applied identity currently has **no** such grant. No new static key,
bucket or customer-managed KMS key is needed. Preserve the operator-only paid-feature gate.

Alternative: explicitly disable ingestion in the home profile before any model invocation
or mutation; existing catalog/recommender/CI/savings/chat remain. This removes a demonstrated
capability and needs Steve's product decision. An in-memory production store is not acceptable.

## Public routing: recommend an outbound Cloudflare Tunnel

Keep both registrars/domains and the original Google OAuth identity. Choose Cloudflare Free
with full authoritative DNS for both domains, stage the DNS move while AWS remains the
origin, then test separate home staging hostnames in HM5. Keep API/chat uncached and preserve
streaming, authorization, CORS and callback behavior. Use verified TLS from connector to
ingress; never disable certificate verification to make staging pass. Restrict routes to
the intended public hosts and exclude cluster administration. Tailscale remains separate.

This means moving authoritative DNS away from Route 53; a free partial-CNAME setup is not
available. [Cloudflare setup requirements](https://developers.cloudflare.com/dns/zone-setups/).
HM5 must export/compare all zone records and DNSSEC/delegation state, preserve Google TXT
proofs, and review the exact delegation/tunnel changes before executing. No account,
delegation, OAuth or routing change is authorized by choosing the design.
[Tunnel uses outbound connections](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/).

WAN address, ISP CGNAT status and measured uplink reliability remain unknown. A router WAN
address compared with the public egress address is needed to diagnose NAT; Tailscale's
100.x address is not evidence of ISP CGNAT. Direct ingress is an alternative only after
ISP reachability, forwarding, dynamic DNS and renewal are demonstrated. The outbound route
avoids requiring a public inbound address, but still needs working outbound connectivity.

## Rollback: recommend at least 48 verified hours, then review retirement

Start the window only after HM7 home acceptance. Keep AWS deployable and its database
read-only. A failed monitor, backup or identity check extends the window until resolved;
48 hours is a review threshold, never an automatic teardown trigger. AWS cost continues.

Before home writes: freeze AWS app and background writers, export/restore/compare, route,
validate and release only home writes. After home writes: freeze home, stop all writers,
take the newest consistent export, restore into an isolated AWS target, compare identities,
sequences/schema/data and verify app behavior before switching the authoritative writer.
No row-wise bidirectional merge and no DNS-only reversal. If the newest home data is lost,
state the verified backup's loss interval and obtain Steve's recovery decision.

Keep `modicum.cloud` and its API host on the selected new origin. Retire AWS-IP sslip.io
hosts only with HM8 approval after dependent integrations use registered-domain URLs.
Redirecting an API is not a reliable migration for POSTs/authentication. Retaining the exact
sslip.io URLs indefinitely requires retaining the AWS IP endpoint and its bill. No legacy
route is removed in HM2.

## Cost and ownership acceptance

Use [the measured worksheet](HOME-SERVER-COSTS.md). Steve owns domain/account renewals,
home hardware/network, Mac issuer availability, recovery, patching and bills. Proposed
operating cadence: daily alert/budget review during migration; weekly failed-job/storage
review; monthly private restore, security maintenance and invoice review. HM5 must turn
this into tested monitoring and operating procedures. Domain checks at 60/30/7 days before
expiry; Tailscale reauthentication before March 13, 2027; issuer replacement planning at
180 days before September 12, 2028. Do not install any schedule in this slice.

Unresolved acceptance: Steve's service choices and rollback/legacy policy; WAN evidence;
account-specific domain renewal dates/prices; wall power/tariff or an explicitly accepted
budget assumption; retained-service budget and future paid-LLM spending policy. No paid
LLM calls are authorized. A partial subtotal is not HM2 cost acceptance.
