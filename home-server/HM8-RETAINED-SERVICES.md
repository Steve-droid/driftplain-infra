# HM8 — retained AWS services review and hand-over

**Decision + result record.** After the AWS compute retirement (September 21, 2026,
[record](AWS-COMPUTE-RETIREMENT.md)) and the HM7 cutover ([record](HM7-CUTOVER.md)) the account
still held services that only made sense with a platform to serve: ECR, the budget kill-switch
chain, the old runtime credential bundle, and the June-era Route 53 setup. This slice reviewed
each one with Steve, removed what had no remaining purpose, moved image publication to GitHub
Actions → GHCR, and states the ownership and availability limits of the home service.
Sanitized results: [hm8-retained-services-evidence.json](hm8-retained-services-evidence.json).

**Result (September 21, 22:40–23:30 UTC; September 22 local):** bootstrap holds 24 resources
(state, ingestion and backup buckets, the recovery-key entry, budget + SNS); ECR and the
kill-switch chain are gone; the budget is $10/month gross with email alerts; the 29 release
images are on public GHCR and both app repos publish new releases there from a tag; the old
`modelmatch/app` bundle is scheduled for deletion; the old home PV is removed. Retained AWS
cost: about $2–3/month.

## Decisions (Steve, September 22, 2026)

| Item | Options | Decision |
|---|---|---|
| Route 53 zones (`driftplain.dev` delegated to Cloudflare; `modicum.cloud` NS/SOA only, un-routed) | keep both / drop modicum.cloud / drop both | **Keep both** ($1/month); `dns/` unchanged |
| ECR (29 images, 4 repos) + future releases | keep ECR / GHCR history + GitHub Actions, delete ECR | **GHCR + Actions, delete ECR** |
| Kill switch (Lambda → CodeBuild teardown; DRY_RUN=1 since HM2) + $110 budget | keep dormant / remove chain, budget → $10 | **Remove the chain, budget → $10** with 80 % ACTUAL, 100 % ACTUAL, 100 % FORECASTED emails |
| Old copies: `modelmatch/app` (ESO source for EKS) and the September 15 home PV `pvc-7aba0fae-…` | keep / retire | **Retire both** (30-day recovery window on the bundle) |
| `modelmatch/home-server/recovery-key-v1` | — | custody; untouched |

## 1. Release history to GHCR

`crane copy` of every ECR tag not yet on GHCR, manifest and layers as-is so the digest is
preserved, then a digest comparison per tag: **25 copied, 4 already present, 0 failed, every
destination digest equal to its source**; all four packages are public (anonymous `crane ls`
lists every tag). Images: `modelmatch-backend` 11 tags, `modelmatch-frontend` 10,
`modelmatch-agent` 5, `modelmatch-agent-security` 4 (the 1.0.24 / 1.1.3 copies already ran at
home since HM4; 1.0.25 was built locally at HM7).

## 2. Publishing from CI

Tag-triggered GitHub Actions workflows, plain `docker buildx` on the amd64 runner, no
third-party actions beyond checkout, `GITHUB_TOKEN` with `packages: write`:

| Repo | Workflow | Trigger | Image |
|---|---|---|---|
| driftplain-backend | `release-image.yml` (PR #28) | `vX.Y.Z` tag or dispatch | `ghcr.io/steve-droid/modelmatch-backend:X.Y.Z` |
| driftplain-backend | `release-agent-images.yml` (PR #28) | `agent-vX.Y.Z` tag or dispatch | `modelmatch-agent`, `modelmatch-agent-security` |
| driftplain-frontend | `release-image.yml` (PR #26) | `vX.Y.Z` tag or dispatch | `ghcr.io/steve-droid/modelmatch-frontend:X.Y.Z` |

A published tag is never overwritten (`docker manifest inspect` guard); the job summary prints
the digest to pin in `charts/modelmatch/values-home-server.yaml`. The first dispatch failed
with `write_package` denied because crane-created packages are not linked to a repository;
Steve granted each package *Manage Actions access → Write* for its repo, after which the
throwaway runs 35666779480 (backend), 35666782798 (frontend) and 35666875842 (both agent
images) succeeded with the OCI `revision` / `version` / `source` labels set. The four
`0.0.0-ci-test-*` tags remain until deleted from the package UI (the CLI token has no
`delete:packages` scope by design).

## 3. Bootstrap changes (two applies, both after plan review)

**Stage 1** (23:12 UTC): `force_delete = true` on the four ECR repositories — 4 changed. ECR
refuses to delete a repository that still holds images, so this must land before the
resources leave the configuration.

**Stage 2** (23:12–23:13 UTC): `Plan: 0 to add, 3 to change, 24 to destroy` →
`Apply complete! Resources: 0 added, 3 changed, 24 destroyed`:

- destroyed: 4 ECR repositories + 4 lifecycle policies (`modules/ecr` removed); the P34b
  chain — Lambda `modelmatch-budget-killswitch` (us-east-1) with its role, policy, log group
  and SNS permission/subscription, CodeBuild `modelmatch-platform-teardown` with its role,
  guardrail policy, admin attachment and log group, the `killswitch_events` topic + policy, the
  EventBridge rule + target (`killswitch.tf`, `lambda/killswitch.py`, the `archive` provider,
  nine variables and `scripts/teardown-buildspec.yml` removed; `scripts/teardown-platform.sh`
  stays as the laptop-runnable platform teardown).
- changed: budget `modelmatch-monthly-cost` 110 → **10** USD gross, the 90 % ACTUAL trigger
  replaced by 100 % ACTUAL (80 % ACTUAL and 100 % FORECASTED kept, email + the retained
  `modelmatch-budget-alerts` topic); the `DenyPlatformTeardownAccess` statement dropped from the
  backup-bucket policy and the recovery-key policy (the role it named no longer exists — each
  policy now has exactly one statement, asserted by `terraform test`: 10 passed).

Note: this month's gross spend ($124.42, almost all of it before the retirement and covered by
credits) already exceeds the new $10 limit, so the 80 % and 100 % ACTUAL emails arrive at the
next Budgets refresh; from October the budget measures the retained services alone.

## 4. Retired copies

- `modelmatch/app`: `delete-secret --recovery-window-in-days 30` at 23:13 UTC; deletion date
  **October 21, 2026 23:13 UTC**; restorable until then with `restore-secret`. The remaining
  entry is `modelmatch/home-server/recovery-key-v1`.
- Home PV `pvc-7aba0fae-a8f2-4edc-a6fe-0c2c744e3edb` (Released, the September 15 restore,
  593 M) deleted with its local-path directory; production stays on `pvc-199e3046-…` (CNPG
  "Cluster in healthy state", `/readyz` 200 before and after). Root disk 8 % used.

## 5. Ownership and availability

**Owner / operator:** Steve (single operator; the Mac holds the identity custody and the
interim backup/maintenance LaunchAgents; see [HM5-OPERATIONS.md](HM5-OPERATIONS.md)).

**Measured and known limitations of the home service:**

- One node, one Postgres instance, one residential ISP, one tunnel connector: any of them down
  means the site is down. There is no failover; recovery is restore-from-S3 on the same or a
  rebuilt host ([HM5-LOST-HOST-RECOVERY.md](HM5-LOST-HOST-RECOVERY.md)).
- Public reachability depends on Cloudflare (DNS + tunnel). The connector re-dials on its own;
  DNS delegation and tunnel changes are manual approvals.
- Hourly backups run from the Mac while it is on and reachable; a day without the Mac is a day
  without new exports (the in-cluster backup image is the follow-up).
- Windows so far: **September 21, 21:00–22:00 UTC** offline between the retirement and the
  cutover; **22:15–22:20 UTC** a false DOWN from the heartbeat while the backend rolled to 1.0.25.
  The heartbeat CronJob in `monitoring` is the availability record going forward.
- The in-cluster LLM and blob store run on the fake seam: the grounded chat answers with an
  honest offline note (backend 1.0.25); the dashboard, findings and CI-run ingestion are live.

**Runbooks:** bootstrap [README](README.md) · restore [HM3-RESTORE.md](HM3-RESTORE.md) /
[S3-BACKUPS.md](S3-BACKUPS.md) · upgrades and calendar [HM5-MAINTENANCE.md](HM5-MAINTENANCE.md)
· identity renewals [IDENTITY.md](IDENTITY.md) (leaves expire December 14, CRL 3 next update
October 20, sealing key renewal about October 15) · lost host
[HM5-LOST-HOST-RECOVERY.md](HM5-LOST-HOST-RECOVERY.md) · budget
[BUDGET-SAFEGUARD.md](BUDGET-SAFEGUARD.md) (superseded chain; alerts only now).

## Open

- Delete the four `0.0.0-ci-test-*` GHCR tags (package UI).
- `platform/` and `jenkins/` still read `ecr_repository_*` bootstrap outputs through
  `terraform_remote_state`; a rebuild needs those references reworked — explicit scope, not
  incidental.
- HM6 wording (retention/service statements in the UI) and then P39, the first post.
