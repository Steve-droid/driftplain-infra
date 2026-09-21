# Driftplain — Infra

[driftplain.dev](https://driftplain.dev) · [Frontend](https://github.com/Steve-droid/driftplain-frontend) · [Backend](https://github.com/Steve-droid/driftplain-backend) · [GitOps](https://github.com/Steve-droid/driftplain-gitops) · **Infra**

Terraform and operator tooling for **Driftplain** — *prove a cheaper LLM is good enough for your
CI, and show the money saved.* Since **September 22, 2026** the app serves from a single-node K3s
cluster at home through a Cloudflare tunnel; AWS keeps only persistent services (state, backups,
DNS, identity, a budget). This repo owns both sides.

> Driftplain was previously Modicum / ModelMatch. Resource names, images and identifiers keep
> `modelmatch` for compatibility.

## What runs where

| Where | What | Owned by |
|---|---|---|
| **Home** (K3s `driftplain-home`, one node) | the app, CloudNativePG Postgres, ArgoCD, cert-manager, F5 NGINX ingress, the tunnel connector, backup + heartbeat jobs | [`home-server/`](home-server/) (bootstrap, identity, backups, runbooks) + the [gitops home profile](https://github.com/Steve-droid/driftplain-gitops/tree/main/argocd/home-server) |
| **Cloudflare** | authoritative DNS for `driftplain.dev` and the named tunnel `driftplain-home-server` (four hostnames → the private ingress) | [`cloudflare/`](cloudflare/) |
| **AWS `957261948820`, `ap-south-1`** | S3 (Terraform state, encrypted Postgres backups, ingestion sources), Secrets Manager (the backup recovery key), Route 53 (the two zones, `driftplain.dev` delegated to Cloudflare), IAM Roles Anywhere (the home node's short-lived identity), Budgets + SNS email | [`bootstrap/`](bootstrap/), [`dns/`](dns/) |

Retained AWS spend is **about $2–3/month** (Secrets Manager ≈ $0.40, Route 53 $1, S3 cents); the
budget alerts at 80 % / 100 % of **$10 gross**. Release images live on public GHCR
(`ghcr.io/steve-droid/modelmatch-*`), published by GitHub Actions from the app repos.

## Layout

```
bootstrap/    persistent AWS: state bucket, ingestion bucket, backup bucket + lifecycle,
              recovery-key entry, budget + SNS. terraform test covers the backup/key contracts.
dns/          Route 53 zones (driftplain.dev delegated to Cloudflare; modicum.cloud un-routed)
cloudflare/   Cloudflare zones, records and the home tunnel
home-server/  the home node: bootstrap.sh / prepare.sh / verify.sh, identity (Roles Anywhere issuer,
              leaf renewal), S3 backups + restore, maintenance, and the HM2–HM8 records + evidence
platform/     the retired EKS platform (VPC, EKS, IRSA, ArgoCD seed) — kept as the rebuild path
jenkins/      the retired Jenkins controller root — history only
modules/      vpc · eks · iam-irsa · jenkins-controller (own modules only)
scripts/      teardown-platform.sh (platform teardown without cluster auth), discover-ingress-dns.py
```

## Working with it

Every root is **defaultless**: `variables.tf` declares inputs only and each command passes the
committed non-secret values explicitly.

```bash
terraform -chdir=bootstrap init
terraform -chdir=bootstrap plan  -var-file=dev.tfvars
terraform -chdir=bootstrap test  -var-file=dev.tfvars
```

`AWS_PROFILE` selects the credentials (nothing is hardcoded); `apply` is a reviewed, explicit
step. State is in S3 with S3-native locking (no DynamoDB), one key per root.

The home node is reached over strict-key SSH (`ssh home-server`); the runbooks in
[`home-server/`](home-server/README.md) cover bootstrap, restore from S3, identity renewals,
lost-host recovery and the operating calendar. Start with
[HM5-OPERATIONS.md](home-server/HM5-OPERATIONS.md) (what runs, what to check, known limits) and
[HM7-CUTOVER.md](home-server/HM7-CUTOVER.md) (how production moved home).

## History

The graded June 2026 delivery ran on EKS: VPC + NAT per AZ, EKS with IRSA, ECR, a Jenkins
controller on EC2, an ArgoCD app-of-apps, and a budget kill switch (Budgets → SNS → Lambda →
CodeBuild teardown). Phase 2 (September 2026) rebuilt it in this account, went public at
`driftplain.dev`, then moved to home hosting when the credits ran out
([decision record](home-server/AWS-COMPUTE-RETIREMENT.md)). The retained-services review that
removed ECR and the kill switch is [HM8-RETAINED-SERVICES.md](home-server/HM8-RETAINED-SERVICES.md).

## Conventions

- Own modules only; defaultless variables + explicit `-var-file`; no secrets in tfvars or state.
- `feature/<slice>-<description>` → PR → `main`; Conventional Commits; a SemVer tag per merged slice.
- Nothing mutating runs unattended: no auto-apply, no scheduled destroy; dry-run defaults everywhere.

Steve Levit — stevelevit230@gmail.com
