# AWS compute retirement — September 21, 2026

**Decision record + result.** Steve's AWS credits were going to run out before the home
migration could finish, so on September 21, 2026 he asked for every AWS compute resource to be
brought down "such that my spend is the bare minimum", keeping only cheap persistent services
(S3, Secrets Manager, DNS, ECR). He approved the AWS commands up front. This document records
what was done, the evidence, what is left in AWS with its monthly estimate, and how the plan
changed (HM7/HM8). Sanitized evidence:
[aws-compute-retirement-evidence.json](aws-compute-retirement-evidence.json). Raw receipts and
encrypted objects stay under `~/.local/share/driftplain/home-server-backups/` (0700/0600) and the
full teardown log in the session scratchpad; no secret value, hash, row or key reached stdout,
argv, Git or evidence.

## Decision

| | |
|---|---|
| **What** | Destroy the ephemeral `platform/` Terraform root (EKS `modelmatch`, managed node group, 2 NAT gateways + EIPs, ingress NLB, CNPG EBS volumes, VPC) and remove the Route 53 NLB aliases. Keep `bootstrap/` (state bucket, ECR, backup bucket, budget/kill-switch chain), `dns/` (two zones + Google TXT), `cloudflare/` and `home-server/identity` (Roles Anywhere). |
| **Why now** | September gross usage was $121.30 against the $110 budget (all credit-covered; net $0.00) at about **$8.35/day** — EC2-Other $3.09, EKS $2.40, EC2 compute $1.77, ELB $0.58, VPC $0.48 — and the credits would have been exhausted before HM7. The 90 % ACTUAL trigger had already fired on September 18 at 23:16 UTC and run the CodeBuild teardown as a **dry run** ([BUDGET-SAFEGUARD.md](BUDGET-SAFEGUARD.md)). |
| **Approval** | Steve, September 21, 2026: "I approve the AWS commands up front, so don't stop to get permissions." Scope: compute down, persistent cheap services kept, decision documented, plan updated. |
| **Consequence** | No AWS overlap or rollback origin exists any more. The home K3s cluster is the only runtime; the production data lives in the encrypted final export and its home restores. Public runtime hostnames answer nothing until HM7. |

## Timeline (UTC, September 21, 2026)

| Time | Step | Result |
|---|---|---|
| 21:00:51 | **Write freeze**: ArgoCD `root` and `modelmatch` automated sync disabled, backend scaled to 0 | API returned 502; no writer left on the primary |
| 21:01:19–21:01:31 | **Final export** `export-20260921T210119Z` (`home-server-database.py export --source aws`, `REPEATABLE READ`) | snapshot `00000004-00019534-1` at 21:01:21, WAL `1/28000000`, **2,598 rows / 23 tables**, alembic `a4b5c6d7e8f9`, PostgreSQL 16.10, dump 93,912 B (age) |
| 21:01:39 | **Upload** with SHA-256 checksums and version IDs to `postgres/daily/aws-20260921T210119Z/` + `recovery/app-credentials/aws-20260921T210119Z.json.age` | 5 objects, receipt kept locally |
| 21:01:43 | **Independent download** `restore-20260921T210143Z` by key + VersionId | checksums and manifest verified |
| 21:02 | **Lifecycle-free copies**: server-side `CopyObject` to `postgres/final/aws-20260921T210119Z/` and `recovery/final/aws-20260921T210119Z.app-credentials.json.age` | checksums equal to the originals; the daily prefix expires after 30 days, `final/` never |
| 21:02:52 | **Restore proof** into the home **disposable** target (`home-server-restore-check`) | `pg_restore` 2.436 s, comparison **match: true, 16/16 categories**; disposable deleted afterwards |
| 21:03:37 | **Graceful pre-delete**: ArgoCD apps `modelmatch-postgres` and `nginx-ingress` deleted with their PVCs | by 21:04:03 the CCM had removed the NLB and the CSI driver both CNPG EBS volumes; no `k8s-*` security groups left |
| 21:04:12 | **`DRY_RUN=0 scripts/teardown-platform.sh`** (live) | node group → 0; two nodes stayed in the EKS `Terminate-LC-Hook` (30-min heartbeat) — completed by hand at 21:11:00 with `complete-lifecycle-action CONTINUE`; the script's 600 s node wait is bounded and would have proceeded anyway |
| 21:13:05 | **Route 53**: `records_enabled = false` in `dns/dev.tfvars`, `terraform apply` | 4 A-alias records destroyed (modicum.cloud, api.modicum.cloud, driftplain.dev, api.driftplain.dev); zones and the Google TXT kept; dns state = 3 addresses |
| 21:15:30 | **`terraform destroy` platform/** | **Destroy complete — 39 resources**; EKS cluster deletion took 2 m 19 s |
| 21:15:38 | **Orphan check** | NAT 0, unattached EIPs 0, LBs 0/0, available EBS 0 — `ORPHAN CHECK: OK`; script `mode=LIVE rc=0 elapsed=686s` |
| 21:16:04 | **Account inventory** (read-only) | see below; `platform` state 0 addresses; `bootstrap` 53, `dns` 3, `cloudflare` 19, `home-server/identity` 8 |

The home cluster was not touched: all 14 ArgoCD applications stayed Synced/Healthy, the staging
hostnames kept answering, and the Mac daily backup schedule exports the **home** instance
(`home-…` keys), so nothing on the backup side depended on AWS.

## What remains in AWS and what it costs

Inventory at 21:16:04 UTC (region `ap-south-1`; Lambda/SNS/Budgets in `us-east-1`). Compute is
zero: no EKS cluster, EC2 instance, NAT gateway, Elastic IP, load balancer, EBS volume/snapshot,
AMI, ENI, VPC endpoint, ASG, launch template or non-default security group; only the AWS default
VPC (`172.31.0.0/16`, free) remains.

| Service | Resources | Monthly estimate (USD) |
|---|---|---|
| Route 53 | 2 hosted zones: `modicum.cloud.` (2 records), `driftplain.dev.` (3 records: NS/SOA + Google TXT; the public delegation points at Cloudflare since September 18) | **1.00** + queries (≈ 0.01) |
| Secrets Manager | `modelmatch/app`, `modelmatch/home-server/recovery-key-v1` | **0.80** |
| ECR | `modelmatch-backend` (10 images, 0.98 GB), `modelmatch-frontend` (10, 0.26 GB), `modelmatch-agent-security` (4, 0.50 GB), `modelmatch-agent` (5, 0.35 GB) — 2.10 GB before layer de-duplication | **≤ 0.21** (0.10/GB; less while the 12-month 500 MB free tier still applies) |
| S3 | `modelmatch-home-server-backups-957261948820` (148 current objects, 5.7 MB; 239 versions, 10.0 MB incl. non-current), `modelmatch-tfstate-957261948820` (5 objects, 167 KB), `modelmatch-ingestion-sources-957261948820` (empty) | **≈ 0.01** |
| KMS | the AWS-managed `aws/secretsmanager` key only | 0 |
| IAM Roles Anywhere | trust anchor `modelmatch-home-server-issuer-v1`; profiles `modelmatch-home-server-bedrock`, `modelmatch-home-server-backup` (enabled); roles of the same names | 0 (sessions are free; Bedrock usage is pay-per-token — the home profile runs `LLM_CLIENT=fake`) |
| Budget kill switch | Budget `modelmatch-monthly-cost` ($110, September actual $121.30), SNS `modelmatch-budget-alerts`, Lambda `modelmatch-budget-killswitch` (`DRY_RUN=1`), CodeBuild `modelmatch-platform-teardown` + EventBridge rule + role, CloudWatch log group (27 KB, 90-day retention) | 0 (first two budgets free; Lambda/SNS/CodeBuild inside the always-free tier at this volume) |
| IAM | roles `modelmatch-budget-killswitch`, `modelmatch-home-server-backup`, `modelmatch-home-server-bedrock`, `modelmatch-platform-teardown-codebuild`; user `steve` | 0 |
| **Total** | | **≈ $2.05/month** (say **$2–3** with query/request noise) — versus ≈ $250/month before |

The Cost Explorer API itself bills $0.01 per request; the checks above were a handful.

## Effects on the plan

- **HM7 → "cutover to home" (re-scoped):** no AWS rollback, no dual-running. Steps: recreate an
  empty home CNPG instance (the old `Retain` PV is kept as a fallback), restore
  `postgres/final/aws-20260921T210119Z/` with the production target, add `driftplain.dev`,
  `api.driftplain.dev`, `modicum.cloud`, `api.modicum.cloud` to the tunnel routes and the
  umbrella host set, authorize the Google OAuth origins, validate. Public cutover itself remains
  a separate explicit approval.
- **HM8 → retained-services review:** compute is done; decide the Route 53 zones (the
  `driftplain.dev` zone is no longer authoritative), the `modelmatch/app` secret (values also live
  in the encrypted `recovery/final/` bundle), ECR (CI could publish to GHCR only), the budget /
  kill-switch chain (nothing left to tear down), and the sslip.io/Modicum endpoints.
- **Superseded:** `home-server-database.py export --source aws`, the `/demo-bringup` skill, the
  HM6 "AWS remains authoritative" guardrail and the CNPG 1.29 EOL maintenance row.
- **Rebuild path (only with explicit scope):** `terraform -chdir=platform apply -var-file=dev.tfvars`
  → `scripts/discover-ingress-dns.py` → `records_enabled = true` → ArgoCD app-of-apps → restore
  the final export; ≈ $8.35/day gross while it runs.

## Guardrails that still apply

`DRY_RUN=1` on the kill switch; no `terraform apply` on `platform/` and no public cutover without
Steve's explicit approval; keep all identities/custody and the S3 lifecycle; never put secret
values, hashes or rows in Git, terminal output or evidence.
