# Driftplain infra

[driftplain.dev](https://driftplain.dev) · [Frontend](https://github.com/Steve-droid/driftplain-frontend) · [Backend](https://github.com/Steve-droid/driftplain-backend) · [GitOps](https://github.com/Steve-droid/driftplain-gitops) · **Infra**

Terraform and operator tooling for Driftplain. Since September 22, 2026 the app runs on a
single-node K3s cluster at home behind a Cloudflare tunnel. AWS keeps only persistent services:
state, backups, DNS, identity and a budget. This repo owns both sides.

## What runs where

| Where | What | Owned by |
|---|---|---|
| Home (K3s `driftplain-home`, one node) | the app, CloudNativePG Postgres, ArgoCD, cert-manager, F5 NGINX ingress, the tunnel connector, backup and heartbeat jobs | [`home-server/`](home-server/) and the [gitops home profile](https://github.com/Steve-droid/driftplain-gitops/tree/main/argocd/home-server) |
| Cloudflare | authoritative DNS for `driftplain.dev` and the named tunnel to the private ingress | [`cloudflare/`](cloudflare/) |
| AWS `957261948820`, `ap-south-1` | S3 (Terraform state, encrypted Postgres backups, ingestion sources), Secrets Manager (the backup recovery key), Route 53 (two zones; `driftplain.dev` is delegated to Cloudflare), IAM Roles Anywhere (the home node's short-lived identity), Budgets + SNS email | [`bootstrap/`](bootstrap/), [`dns/`](dns/) |

Retained AWS spend is about $2 to $3 a month. The budget emails at 80 % and 100 % of $10 gross.
Release images are on public GHCR (`ghcr.io/steve-droid/modelmatch-*`, the project's old name),
pushed by GitHub Actions from the app repos.

## Layout

```
bootstrap/    persistent AWS: state bucket, ingestion bucket, backup bucket and lifecycle,
              recovery-key entry, budget and SNS. terraform test covers the backup and key contracts.
dns/          Route 53 zones
cloudflare/   Cloudflare zones, records and the home tunnel
home-server/  the home node: bootstrap.sh, prepare.sh, verify.sh, identity (Roles Anywhere issuer and
              leaf renewal), S3 backups and restore, maintenance, and the HM2 to HM8 records with evidence
platform/     the retired EKS platform (VPC, EKS, IRSA, ArgoCD seed), kept as the rebuild path
jenkins/      the retired Jenkins controller root, kept for reference
modules/      vpc, eks, iam-irsa, jenkins-controller (own modules only)
scripts/      teardown-platform.sh, discover-ingress-dns.py
```

## Working with it

Every root is defaultless: `variables.tf` declares inputs only and each command passes the
committed non-secret values explicitly.

```bash
terraform -chdir=bootstrap init
terraform -chdir=bootstrap plan  -var-file=dev.tfvars
terraform -chdir=bootstrap test  -var-file=dev.tfvars
```

`AWS_PROFILE` selects the credentials. `apply` is a reviewed, explicit step. State is in S3 with
S3-native locking, one key per root.

The home node is reached over strict-key SSH (`ssh home-server`). The runbooks in
[`home-server/`](home-server/README.md) cover bootstrap, restore from S3, identity renewals,
lost-host recovery and the operating calendar. Start with
[HM5-OPERATIONS.md](home-server/HM5-OPERATIONS.md) for what runs and what to check, and
[HM7-CUTOVER.md](home-server/HM7-CUTOVER.md) for how production moved home.

## History

The graded June 2026 delivery ran on EKS: VPC with NAT per AZ, EKS with IRSA, ECR, a Jenkins
controller on EC2, an ArgoCD app-of-apps and a budget kill switch. Phase 2 (September 2026)
rebuilt it in this account, went public at `driftplain.dev`, then moved home when the credits ran
out ([decision record](home-server/AWS-COMPUTE-RETIREMENT.md)). The review that removed ECR and
the kill switch is [HM8-RETAINED-SERVICES.md](home-server/HM8-RETAINED-SERVICES.md).

## Conventions

- Own modules only. Defaultless variables with an explicit `-var-file`. No secrets in tfvars or state.
- `feature/<slice>-<description>`, then a PR to `main`. Conventional Commits. A SemVer tag per merged slice.
- Nothing mutating runs unattended: no auto-apply, no scheduled destroy.

Steve Levit, stevelevit230@gmail.com
