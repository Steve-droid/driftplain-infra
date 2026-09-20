# Home-server recurring costs — September 15, 2026

**HM2 planning envelope accepted September 15, no credits:** **$10/month** for retained
services/domains plus **₪36/month** electricity. Steve accepted conservative assumptions
pending exact pre-cutover readings/renewal checks. This excludes AWS overlap and paid LLM
use; it is not an enforced billing cap or an assertion of the measured complete bill.
Existing AWS production keeps accruing compute charges until HM8 approval. The selected
home service choices are in [HM2 decisions](HM2-DECISIONS.md); none adds a paid subscription
here. No paid LLM calls occurred or are authorized.

## Measured storage and current recurring services

[Read-only inventory](hm2-service-inventory.json): September 14 at 19:07 UTC, except source
bucket verification on September 15 at 12:20 UTC. Secret values and object contents were
not read. Listed services are in ap-south-1 unless global. This is a scoped inventory,
not a whole-account orphan audit or a current production-health claim.

| Item | Measured quantity / rate | Monthly USD basis |
|---|---|---|
| Two Route 53 zones | `driftplain.dev`, `modicum.cloud`; $0.50/zone | $1.00 plus queries; still charged after delegation moves unless separately deleted |
| Two Secrets Manager secrets | `modelmatch/app` and recovery-key-v1; $0.40 each | $0.80 plus $0.05/10,000 API requests |
| ECR four repositories | 29 image entries; summed compressed image sizes 2,098,682,587 bytes before shared-layer deduplication | About $0.21/month planning upper estimate at $0.10/GB; not measured billed storage; internet pulls extra |
| Terraform S3 state | 1,902,401 bytes across 76 object versions; 48 delete markers | About $0.00005 storage at $0.025/GB-month, plus requests/marker overhead |
| Home-server backup S3 | 6,378 bytes, one issuer version; no production exports | Below $0.000001 current storage; future production retention measured in HM3/HM5 |
| Ingestion-source S3 | Zero objects/versions | Zero current object storage; future source bytes/requests if durable ingestion selected |
| CloudWatch logs | Teardown log group 18,006 stored bytes, 90-day retention | Meter ingestion/storage; this size does not price future logs or us-east-1 Lambda logs |
| IAM Roles Anywhere / local CA | Existing operator-managed issuer; no AWS Private CA | No additional Roles Anywhere service charge; ordinary AWS operations still billed |
| Public GHCR | Selected public image packages | $0 under current public-package/container policy; CI runner/build costs are separate |
| Cloudflare | Selected Free DNS/Tunnel, no paid add-ons | $0 plan assumption, verify selected account plan before provisioning |
| Monitoring/notifications | Healthchecks.io free tier (three heartbeats); the staging HTTPS checks run inside the cluster heartbeat job | $0 planning basis; no paid plan/SMS; account setup/delivery not yet proven |

Rates: [Route 53](https://aws.amazon.com/route53/pricing/),
[Secrets Manager](https://aws.amazon.com/secrets-manager/pricing/),
[ECR](https://aws.amazon.com/ecr/pricing/),
[Mumbai S3 source verified September 13](S3-BACKUPS.md#recurring-cost-without-credits),
[GitHub packages](https://docs.github.com/en/billing/concepts/product-billing/github-packages),
[Roles Anywhere](https://aws.amazon.com/about-aws/whats-new/2023/12/iam-roles-anywhere-additional-aws-regions/).
ECR and S3 size calculations above use decimal GB as a conservative planning convention;
provider metering, shared layers, time weighting and taxes determine the bill.

The ingestion bucket name was corrected from an initial failed guessed lookup using
`bootstrap/dev.tfvars`; the correct bucket is `modelmatch-ingestion-sources-957261948820`.
No bucket was created or changed.

## Domains and home costs

Porkbun's public standard renewal table checked September 15 lists `.dev` at **$12.87/year**
and `.cloud` at **$21.11/year**: **$33.98/year**, or **$2.8317/month** accrued together.
These are renewal prices, not the lower first-year promotions. They do not prove the exact
account-specific price, premium status, auto-renew setting, funding or next renewal date
for either registered domain. Verify those in Steve's registrar account before accepting
the worksheet. [Porkbun pricing](https://porkbun.com/products/domains).

Home power is unmeasured. Formula: `average watts × 24 × days / 1000 × tariff per kWh`.
At 30 days, **10/20/30 W = 7.2/14.4/21.6 kWh**. This is a scenario range, not a measured
server load or guaranteed bound. Steve's electricity tariff, taxes and any discount must
come from his bill. Use a wall meter under normal operation including AC losses; software
CPU readings cannot measure whole-laptop wall power. Existing internet has no confirmed
incremental charge; additional static-IP/service plans would need an explicit decision.
Hardware replacement and operator time are excluded from cash subtotals but remain real
dependencies. The four-hour restore target assumes available working hardware and internet.

## Proposed budget calculation and remaining decisions

If both domains renew at the public standard prices, both DNS zones/secrets and all current
ECR repositories are retained, the known fixed/storage planning subtotal is **about $4.84/month**
before backups, transfer/requests, logs/monitoring, tax, power, internet and LLM use.
This is deliberately a partial subtotal, not a promise of total monthly hosting cost.

For illustration, **10 GB** of retained backup versions adds **$0.25/month**. 720 hourly
plus 30 daily single-PUT archives add **$0.00375/month**, before manifests, reads, extra
recovery bundles and downloads. The resulting roughly **$5.10** still excludes everything
listed above. The old 9.5 MiB allocated DB size is not an archive-size measurement.
Use `retained version bytes × $0.025/GB-month` after HM3 measurement. Lifecycle is not
enabled yet; do not pretend retention currently bounds growth or that 24+30 copies is an
exact S3 limit. Review daily during the temporary no-expiration interval.

Retained-service recommendation: keep state, backups, recovery-key secret, app-secret
recovery source and current ECR repositories; their current storage cost is small. Review
unused Route 53 zones/source storage/logs explicitly in HM8 after the selected DNS and
ingestion path is verified. Never delete them as incidental housekeeping. Steve owns every
retained item and its invoice. A future budget should separately track retained AWS
services, domain renewals, home electricity and paid-model usage.

AWS overlap is separate: `overlap hours × actual current compute/network/storage hourly
run-rate + transfer/request charges`. Price the then-current EKS/EC2/NAT/NLB/EBS/IP inventory
before HM7's 48-hour overlap approval; delayed budget actuals do not price that window.
The $110 AWS gross alert and `DRY_RUN=1` remain. No automatic teardown or paid Bedrock
allowance is introduced. The earlier proposed $2 LLM allowance is still unapproved and
the hourly token ceiling is not a monthly dollar cap.

**HM2 cost-design acceptance:** Steve chose the conservative envelope above on September 15.
Its electricity component is **50 W × 720 h = 36 kWh/month**, budgeted at an intentionally
conservative **₪1/kWh assumption**. Neither figure is a wall measurement or his actual tariff.
Verify wall power and registrar renewal
settings/dates/prices before cutover. HM3/HM5 measure real export size, retention behavior
and recovery transfer; adjust forecasts and alert thresholds from those results.

A current free monitoring candidate supports HTTP and heartbeat/cron checks at five-minute
intervals with 50 monitors: [UptimeRobot Free eligibility/features](https://help.uptimerobot.com/en/articles/11604710-who-should-use-uptimerobot-s-free-plan)

## HM5 measured update — September 17, 2026

| Item | Measured / selected | Monthly cost |
|---|---|---|
| One scheduled export set (dump + fingerprint + roles + manifest, age-encrypted) | 131,983 B hourly; the daily set adds the 998 B credential bundle | 720 hourly + 30 daily ≈ 99 MB retained under the planned lifecycle → ≈ $0.0025 storage + $0.00375 PUTs ≈ **$0.01** |
| Cloudflare Free: two zones, named tunnel, cache rule, proxied staging hosts | free-plan features only ([cloudflare/README.md](../cloudflare/README.md)) | **$0** |
| Route 53 during the overlap (both zones stay until the HM8 review) | 2 × $0.50 | **$1.00** |
| External monitor (Healthchecks.io free tier) | 3 heartbeat monitors; the 2 HTTPS checks are in-cluster edge probes | **$0** |
| Domains (Porkbun renewals) | unchanged | **$2.83** accrued |
| Home electricity | still the 50 W × 720 h assumption; no wall measurement yet | ₪36 planning figure |

The known fixed subtotal therefore stays at about **$4.84/month + $0.01 backups**, inside the
accepted $10 envelope, before AWS overlap and paid LLM use. The wall-power measurement remains
an open HM5/HM7 item.
and [pricing](https://uptimerobot.com/pricing/), checked September 15. HM5 must verify the
selected account's actual notification delivery and heartbeat behavior; this budget does
not assume paid SSL/domain checks, SMS, voice calls or a paid plan. Custom expiry checks
can send sanitized heartbeat success/failure. Provider/account setup is not performed here.

Retained-service/domain forecasts remain about $4.84 before measured backup usage; the
$10 allowance adds planning headroom, not a guarantee against arbitrary API/storage use.
Existing $110 AWS alerts and DRY_RUN=1 stay until HM8's separately scoped budget transition.
No paid-LLM allowance is introduced. Steve owns monthly invoice review and changes to the
funding envelope. Actual charges and the remaining AWS compute bill must remain visible.
