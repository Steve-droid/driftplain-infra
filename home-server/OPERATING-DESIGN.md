# HM2 operating choices — September 12, 2026

**Operating design in progress.** Steve selected S3 and hourly backups, with one-day hourly
and 30-day daily retention and a four-hour restore target. The backup bucket is approved,
provisioned and verified. Recovery-key custody is AWS Secrets Manager plus a local-only Mac
Keychain copy, selected September 13; see [the custody runbook](RECOVERY-KEY.md).
IAM Roles Anywhere is selected for separate backup and Bedrock identities. The local
[identity implementation](IDENTITY.md) is provisioned with authentication disabled;
[applied verification](IDENTITY-APPLIED.md) records the September 14 result. Additional
[issuer tooling](ISSUER.md) is merged, and separately approved issuer enrollment/independent
empty-ledger recovery now pass. Public route and remaining
service choices are proposals.
The approved budget safeguard is applied; AWS is still production. HM2 is not yet accepted.

**September 15 continuation:** [concrete service decisions](HM2-DECISIONS.md),
[measured cost worksheet](HOME-SERVER-COSTS.md) and [initial identity bootstrap package](IDENTITY-BOOTSTRAP.md)
were published in infra PR #25 after review. The separately approved initial bootstrap
and independent populated-ledger recovery now pass; AWS authentication remains disabled.
Service choices remain pending. The worksheet adds actual ECR/S3 sizes and current public
domain-renewal rates; account-specific renewals, power and WAN facts remain open.

## Fresh baseline

Read at approximately 23:15–23:18 Asia/Jerusalem, September 12, 2026:

- Home: `.93` on DHCP, Ethernet MAC `A8:B1:3B:73:F8:7A`, gateway `.1`. K3s active/enabled,
  one Ready node; only three system pods before this drill, no PVs or app. Samsung root UUID
  `9ce972e8-f060-45fc-97fe-296664de6a7c`, 422 GiB available; 14 GiB memory available,
  swap unused. UFW active, secret encryption enabled/hashes match, no failed system units.
- AWS account `957261948820`, profile `saa`: three Ready EKS nodes / three running
  t3a.medium instances; two available NAT gateways; 14 ArgoCD apps Synced/Healthy.
  CNPG has two healthy instances and two 5 GiB gp3 PVCs. Public API readiness is ready/db ok.
- Four private ECR repos, one Secrets Manager secret (`modelmatch/app`), two S3 buckets
  (Terraform state and ingestion sources), two Route 53 zones. No secret values read.
  The two recorded teardown builds on September 7 succeeded in `DRY_RUN=1`; no new build ran.
- No fresh table export/count performed here. HM1/P38r counts remain historical evidence;
  HM3 must inventory and compare the full production export, not assume counts are current.
- Canonical checkouts retained. Backend has an unrelated untracked
  `data/catalog/benchmark-seed.demo.json`; it was left untouched.

## Approved AWS source safeguard

**Applied September 13, 2026 after Steve's explicit approval:** Lambda
`modelmatch-budget-killswitch` is Active with `DRY_RUN=1`. Its $99 threshold now starts
plan-only builds. The $110 gross budget, notifications/subscribers and application token
ceilings remain unchanged. Latest actual spend was $48.833, forecast $101.66; these are
delayed billing figures. The initial September 12 inventory above is historical.

The full reviewed plan also fixed the omitted existing security-agent registry declaration
and aligned its ownership tag to `stack=bootstrap`. Apply result: 0 creates, 2 updates,
0 deletes; all registry image identifiers unchanged. No teardown build was running, AWS
production remained healthy and the follow-up Terraform plan reported no changes.
See [the applied scope and evidence](BUDGET-SAFEGUARD.md).

The existing [teardown script](../scripts/teardown-platform.sh) still contains destructive
operations, including detached database-volume deletion. It must only execute in live mode
after explicit teardown approval; the safeguard does not remove the script or prevent an
operator from manually requesting a live build. Do not automatically re-arm the Lambda.

Steve accepted the temporary loss of automatic compute-cost enforcement; the $110 budget
is an alert, not a billing cap. Check spend daily during migration and resolve a funded
extension or data-preserving service pause explicitly if required. No such future action
is authorized by the safeguard. HM8 must replace the obsolete trigger after approved retirement.

## Host, data and recovery

Keep Ubuntu Desktop, AC/lid settings and Kingston disk unchanged. Reserve `.93` at the
router; instructions and the reusable drill are in [RECOVERY.md](RECOVERY.md).
Steve saved the correct `.93`/Ethernet-MAC reservation on `brlan0` (screenshots verified).
After his router power cycle, DHCP reacquired `.93` at 23:42:23 and SSH/pod networking
recovered automatically; see [results](RESULTS.md). WAN/CGNAT details remain unknown.

Use one CNPG PostgreSQL 16 instance on the Samsung filesystem. A dedicated home StorageClass
must explicitly use `Retain`; use the existing K3s storage root
`/var/lib/rancher/k3s/storage` with PVC-specific directories and node affinity. Record the
actual bound directory/UID after provisioning. A requested PVC size is not a disk quota;
alert at 70%/85% filesystem usage and bound logs/images. Do not hand-mount the second SSD.
Retain prevents ordinary reclaim deletion, not SSD failure, root deletion or K3s uninstall.

**Selected backup destination:** a dedicated S3 bucket in `ap-south-1`, in Steve's existing
AWS account, so no additional platform/account is required. It is separate from state and
ingestion storage and lives in the persistent bootstrap stack. Steve approved hourly backups,
one-day retention for hourly copies, 30-day retention for daily copies, and a four-hour restore
target once working hardware/internet are available. These are implementation targets, not
current capabilities. The one-hour recovery-point target must account for backup duration.

[The S3 design and applied bucket foundation](S3-BACKUPS.md) specifies private/versioned/SSE-S3 storage,
client encryption, explicit teardown-role denies, key custody and temporary home access.
It also explains S3 expiration timing: one-day/30-day windows do not mean exactly 24 hourly
objects or guaranteed physical deletion at 30 days. Lifecycle/version expiry is deferred
until an independent restore succeeds. No backup schedule or production export exists yet.

The private recovery key remains off the home server: AWS Secrets Manager is the primary,
with a local-only login Keychain item on this Mac as the offline-capable copy. This Mac
copy is not removable offline media or iCloud Passwords. [Custody details](RECOVERY-KEY.md)
include operator-only access and retention of old keys. Keep an encrypted Mac-held migration
copy as well as S3. The initial
operator upload uses local AWS access; scheduled home uploads need a dedicated temporary
identity, without copying Mac admin credentials or static IAM keys. S3 is off-site but in the
same AWS account; no immutability/account-compromise guarantee is claimed.

## External services and credential ownership

| Component | Proposed home design | Ownership / verification still required |
|---|---|---|
| Public app/API | Cloudflare outbound tunnel with exact existing hostnames | Steve owns account/MFA; scoped connector credential in Kubernetes; test restart, streaming/chat, CORS and TLS before cutover |
| DNS and OAuth | Preserve both domains, all verification TXT records and original Google client/subjects | Steve retains Porkbun/Google ownership; export and compare complete zones before any DNS move |
| App secrets | Preserve exact values; home Sealed Secrets with encrypted off-host controller-key recovery | GitOps owns encrypted manifests; Mac/operator handles bootstrap; never commit plaintext; restore the key before syncing encrypted secrets |
| Images | Publish approved release images to public GHCR with immutable digest references | Requires separate publishing approval; prove anonymous pull from a clean client; retain ECR through rollback |
| Bedrock | Retain Nova, existing scopes/caps and paid-feature restrictions; IAM Roles Anywhere for home | Dedicated short-lived AWS role credentials from a home certificate; never Mac admin credentials/static IAM keys; helper integration and automatic refresh must pass in HM4 |
| Terraform state | Retain current protected S3 bucket | Operator-only access; version/lifecycle budget; no home runtime access required |
| Ingestion-source blobs | Existing in-memory blob adapter is insufficient for durable operation | Decide durable S3 source persistence versus explicit ingestion disablement; preserve existing catalog/data |
| Jenkins/BYOK references | Current API is metadata-only and rejects secret fields; in-memory secret adapter is unused by that route | Preserve this existing contract, nullable legacy refs and CI-token hashes; no new backend secret vault is needed |

**Selected identity:** IAM Roles Anywhere, with separate backup-upload and Bedrock roles.
Steve approved CA-key custody in local Mac Keychain plus an age-encrypted issuer recovery
bundle in S3/on the Mac. He then required automatic certificate renewal. The local draft
uses a Mac launchd renewal job for 90-day leaves at 30 days remaining, with retries/failure
notifications and independent expiry monitoring required before use. The issuer is now
enrolled with encrypted S3/Mac recovery and a home-held public checkpoint. AWS identity
is provisioned but disabled; initial leaves/Secrets and populated-ledger recovery are verified
in [the bootstrap record](IDENTITY-BOOTSTRAP.md). No renewal job is deployed. See [enrollment evidence](ISSUER-ENROLLMENT.md)
and [identity design/code/tests](IDENTITY.md).
The September 14 [identity plan](IDENTITY-PLAN.md) was refreshed, separately approved and
[applied](IDENTITY-APPLIED.md): eight creates, no updates/deletes, with the enrolled public CA
and authentication disabled. The full follow-up plan proposes no changes. HM2 is open.
App secrets and image distribution remain independent choices; neither runtime role gets
Secrets Manager or ECR permissions. Existing ESO/app-secret custody remains intact on AWS.

[Public GHCR images support anonymous pulls](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
This avoids recurring ECR-token refresh and makes the public CI agent usable without Steve's
registry login. Publishing is still gated; do not change package visibility incidentally.

## Public-route and rollback choice

An outbound tunnel is a connection initiated by the home server to an edge service, which
forwards public HTTP traffic back over it. It avoids inbound router forwarding and dependence
on a fixed residential IPv4 address. WAN/CGNAT status is still unknown; direct ingress would
need ISP reachability, router port-forwarding, dynamic DNS and certificate-renewal verification.

Recommend Cloudflare's standard full DNS setup for the registered domains. Free/Pro requires
Cloudflare authoritative DNS; keeping Route 53 authoritative via partial CNAME setup requires
Business/Enterprise. This is a material DNS ownership change, **not** just adding a CNAME.
[Cloudflare setup requirements](https://developers.cloudflare.com/dns/zone-setups/).
Porkbun can remain registrar and the original Google OAuth client remains unchanged.
No delegation, tunnel account, route or OAuth setting has been changed here.

Stage DNS migration separately from compute cutover, keeping AWS as the origin and comparing
all DNS/TXT records. Then validate a separate staging hostname through the tunnel. Runtime
app/API names change origin only at approved HM7. TLS terminates at Cloudflare; HM5 must
choose/verify the connector-to-ingress transport and HM6 must accurately disclose providers.
Do not place interactive Cloudflare Access in front of public CI/OAuth API flows.

Propose **48 hours of AWS overlap** after successful home cutover, with an additional paid
extension only by decision. One authoritative writable database: freeze writes, final export,
restore/compare, route, validate, then release the freeze. After home accepts writes, rollback
must first freeze again and transfer/verify the newest data to AWS. DNS reversal alone loses data.

Legacy `modicum.cloud` can follow the new origin. AWS-IP sslip.io URLs cannot: choose their
retirement date and update dependent integrations before deleting the NLB. Keeping those
exact IP URLs indefinitely would retain paid AWS infrastructure; this is unresolved, not
silent compatibility. Keep their current routes during overlap.

## Recurring cost worksheet (USD, no promotional credits)

These are planning allowances, not a measured complete bill or approved subscriptions.
The [September 15 worksheet](HOME-SERVER-COSTS.md) supersedes the unmeasured size/domain
placeholders below with recorded quantities and explicit remaining unknowns.

| Item | Basis / allowance |
|---|---|
| S3 backup storage | Mumbai Standard $0.025/GB-month: 10 GB retained ≈ $0.25/month; meter all versions; no promotional credits assumed |
| S3 backup requests/restores | PUT/COPY/LIST $0.005/1,000; GET $0.004/10,000; applicable download charges extra; see the S3 design |
| Route 53 while both zones remain | 2 × $0.50 = $1/month plus applicable queries, even after changing authoritative nameservers |
| Secrets Manager during overlap | One secret ≈ $0.40/month plus $0.05/10,000 API calls |
| Secrets Manager recovery key, retained | Selected: one additional versioned recovery secret ≈ $0.40/month plus $0.05/10,000 API calls; no hourly reads required |
| Private ECR while retained | $0.10/GB-month plus applicable outbound transfer; actual stored size still to measure before HM8 |
| Public GHCR | Public packages free under current policy; revisit pricing during maintenance |
| Roles Anywhere / private operator CA | No additional Roles Anywhere service fee or AWS Private CA; encrypted issuer bundles add S3 storage/requests. Mac renewal job uses existing operator access; no extra Secrets Manager secret. See [identity costs](IDENTITY.md). |
| S3 state/source buckets, logs/budgets/identity | Retain and measure; proposed combined $2/month allowance is not yet validated |
| Bedrock | No calls authorized now; proposed $2/month operating allowance needs approval and an enforceable monthly limit in addition to current hourly token caps |
| Domains | Driftplain renewal previously quoted $12.87/year (recheck receipt/renewal price); Modicum renewal still to verify; keep both |
| Home power/internet | Existing service; measure wall power. 20 W average would be 14.4 kWh/30 days × Steve's actual tariff, not a measured load |

Sources checked September 12–13, 2026: [S3 Mumbai](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/ap-south-1/index.json),
[Route 53](https://aws.amazon.com/route53/pricing/),
[Secrets Manager](https://aws.amazon.com/secrets-manager/pricing/),
[ECR](https://aws.amazon.com/ecr/pricing/),
[GitHub packages billing](https://docs.github.com/en/billing/concepts/product-billing/github-packages).
Do not claim a final $/month total until provider selection, image/state sizes, domain renewals,
identity charges and actual host power are known. AWS compute continues accruing separately
until approved HM8 retirement; delayed budget actuals are not its future run-rate.

## Steve's operating responsibilities

- Daily during migration: source health, budget actuals/forecast and backup/restore progress.
- Ongoing alerts: external availability, backup age/failure, disk, certificate and credential expiry;
  test notification delivery in HM5. A home-only monitor cannot report a total home outage.
- Weekly: inspect failed jobs/backup inventory and storage growth; verify AC, temperatures and fan vents.
- Monthly: security updates in a maintenance window, read release notes and check K3s/CNPG
  compatibility before upgrading; review bills and domain/credential expiry; restore a backup privately.
- Before cutover: record account/key custody and a replacement-host recovery runbook. Keep Mac plus
  independent encrypted copies; verify recovery without relying on a still-running original cluster.

The approved reboot and service-interruption checks passed; [results](RESULTS.md) include
the 150-second recovery and shutdown/DNS transients. DHCP reservation and router power-cycle
recovery are also verified. HM2 remains open for WAN facts needed by the route choice and
the material service decisions. The approved budget safeguard is applied. HM3 must not be represented as complete by this disposable witness.
