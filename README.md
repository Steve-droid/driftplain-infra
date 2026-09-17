# Driftplain — Infrastructure

[Website](https://driftplain.dev) · [Frontend](https://github.com/Steve-droid/driftplain-frontend) · [Backend](https://github.com/Steve-droid/driftplain-backend) · [Infra](https://github.com/Steve-droid/driftplain-infra) · [GitOps](https://github.com/Steve-droid/driftplain-gitops)

**Home migration (September 12, 2026):** the separate [home K3s bootstrap and staged
migration plan](home-server/README.md) has a verified private single-node foundation. See
[test results](home-server/RESULTS.md). AWS still serves production; public cutover and teardown
require separate verification and approval. Historical AWS instructions below do not
authorize destroying production during this migration.

**E21/HM5 (September 17, 2026):** hourly encrypted home backups with a second independent
restore, Mac maintenance schedules, the [Cloudflare DNS + tunnel root](cloudflare/README.md)
(applied September 18, zones pending delegation, AWS stays the origin) and the [operations](home-server/HM5-OPERATIONS.md),
[maintenance](home-server/HM5-MAINTENANCE.md) and [lost-host recovery](home-server/HM5-LOST-HOST-RECOVERY.md)
runbooks. Gated on Steve: DNS delegation, GHCR package visibility, external monitor URLs, Keychain authorization.

> **P38r — shipped September 12, 2026:** Driftplain is live at **https://driftplain.dev**, with **https://api.driftplain.dev** as its runtime API. Trusted HTTPS, Google domain ownership, published Google branding and real sign-in are verified. Modicum/sslip.io endpoints and operational identifiers remain compatible. FE/BE 1.0.24, agents 1.1.3; runtime cutover GitOps v0.18.22.


> Driftplain was previously Modicum / ModelMatch. The four public repositories use `driftplain-*`; existing infrastructure, images, database names, metrics and CI credential/environment identifiers retain `modelmatch` for compatibility. Driftplain and retained Modicum DNS/HTTPS are live. See [DNS runbook](dns/README.md).

> **ACTIVE** (since P1). Terraform foundation for Driftplain's AWS infrastructure — region
> **`ap-south-1`**, account **`957261948820`**. Part of the four-repository Driftplain project linked above.
> Operator guidance: [`CLAUDE.md`](CLAUDE.md).

## Table of Contents

- [Overview](#overview)
- [Four stacks, separate lifecycles](#four-stacks-separate-lifecycles)
- [Technology Stack](#technology-stack)
- [Repository Structure](#repository-structure)
- [Prerequisites](#prerequisites)
- [Usage — the daily cycle](#usage--the-daily-cycle)
- [Teardown & orphan check](#teardown--orphan-check)
- [State backend](#state-backend)
- [Cost alerting (P2)](#cost-alerting-p2)
- [Conventions](#conventions)
- [Contact](#contact)

## Overview

Terraform (**own modules — no third-party/registry modules**) for Driftplain's AWS infrastructure, built
cost-aware from the start: lifecycle discipline (`apply` at day start / `destroy` at day end) and the
orphan-resource ritual are first-class.

What it provisions (region **`ap-south-1`**):

- **EKS** — latest in-support Kubernetes version (a stale version is a silent ~×6 control-plane charge);
  managed node group of **3× `t3a.medium`** (scaled at P23 for the EFK stack), CNI **prefix delegation**
  (110 pods/node), OIDC provider for IRSA.
- **VPC** — public + private subnets across **2 AZs** with **one NAT Gateway per AZ** (each AZ's private
  route table defaults to its own NAT, so an AZ loss cannot take down the other AZ's egress — P37,
  2026-09-07; it replaced the single-NAT SPOF the HLD listed as a limitation); **single ingress load balancer** (FE + BE behind one LB, provisioned by
  the in-cluster controller, not Terraform — see teardown).
- **ECR** — two repos (backend, frontend) with a lifecycle policy (expire untagged, keep last N tagged).
- **IAM + IRSA** — OIDC → **role A** (`modelmatch-backend-irsa`: Bedrock Nova ARNs + the S3 bucket) and
  **role B** (`modelmatch-eso-irsa`: Secrets Manager for External Secrets Operator) → annotated onto the
  respective ServiceAccounts. **No static keys in the cluster.**
- **S3** — Terraform remote state + the ingestion source-doc bucket.
- **Jenkins controller** — the graded persistent CI box (EC2 + EIP + SG + instance profile + EBS-backed
  `JENKINS_HOME`), in its own root stack so the daily `destroy` can never take out CI.

> **No managed database (no RDS).** Per the build module the **database is in-cluster** (a CNPG cluster on
> an EBS-CSI PVC — see [`driftplain-gitops`](https://github.com/Steve-droid/driftplain-gitops)). This repo provisions **no RDS**.

## Four stacks, separate lifecycles

So the daily `destroy` can never nuke state, images, the budget, **or the CI controller**, Terraform is
split into **four root stacks with separate state**. The persistent [`dns/`](dns/README.md) root
owns the Route 53 public zone and app/API aliases; its state is `dns/terraform.tfstate`.
Registration stays at Porkbun. The other three roots are:

```
driftplain-infra/
├── bootstrap/   # PERSISTENT — applied once, NEVER in the daily destroy
│   └── S3 state bucket + lock (P1) · AWS Budget+SNS (P2) · ECR repos (P5) · S3 ingestion bucket (P6) · budget kill switch (P34b)
├── platform/    # EPHEMERAL — `apply` at day start / `destroy` at day end   ← the ONLY stack destroyed daily
│   └── VPC + NAT per AZ (P3/P37) · EKS+OIDC+nodes (P4) · IRSA roles A/B (P7) · ArgoCD bootstrap + app namespace (P9/P10)
├── jenkins/     # PERSISTENT — graded CI controller; survives every platform destroy (P16)
│   └── Jenkins EC2 + EIP + SG + IAM instance profile + persistent EBS (/var/lib/jenkins) + backup bucket
└── modules/     # our OWN reusable modules: vpc · eks · ecr · iam-irsa · jenkins-controller
```

- `bootstrap/` outputs (state-bucket ARN, ECR URLs, ingestion-bucket ARN) are read by `platform/` via
  `terraform_remote_state` — never duplicated.
- Each stack has its own `versions.tf` · `providers.tf` · `backend.tf` (+ `variables.tf` / `main.tf` /
  `outputs.tf`). Same S3 bucket, **different state key** per stack.
- **`bootstrap/` and `jenkins/` are persistent** and never in the daily ritual; `jenkins/` is a *separate*
  root because it has its own operational surface (EC2, plugins, jobs, an attached disk) and lifecycle —
  destroyed only intentionally.

## Technology Stack

| Category           | Technologies   |
| ------------------ | -------------- |
| **Infrastructure** | AWS — EKS · VPC · NAT · ECR · IAM/IRSA · S3 · EC2 (Jenkins) |
| **IaC**            | Terraform 1.15.x — own modules only; S3 remote state, S3-native locking (no DynamoDB) |
| **Region / Acct**  | `ap-south-1` (Mumbai) · `957261948820` |

## Repository Structure

```
driftplain-infra/
├── bootstrap/   backend.tf · budget.tf · ecr.tf · ingestion.tf · sns.tf · killswitch.tf · lambda/killswitch.py · main.tf · variables.tf · dev.tfvars · …
├── platform/    backend.tf · vpc.tf · eks.tf · irsa.tf · argocd.tf · namespaces.tf · variables.tf · dev.tfvars · …
├── jenkins/     backend.tf · main.tf · iam.tf · variables.tf · dev.tfvars · outputs.tf · …
├── modules/     vpc · eks · ecr · iam-irsa · jenkins-controller   (our own only)
├── scripts/     teardown-platform.sh (the platform teardown, DRY_RUN=1 default) · teardown-buildspec.yml (its CodeBuild wrapper)
├── docs/        diagrams (E10 infra foundation)
├── README.md · CLAUDE.md · .gitignore
```

## Prerequisites

- Terraform ≥ 1.10 (we run 1.15.x — required for S3-native locking).
- AWS credentials on the **default credential chain** (`default` profile / env vars) — no `profile` is
  hardcoded in HCL, and no static keys are committed.
- Authority in account `957261948820`, region `ap-south-1`.

## Usage — the daily cycle

**Every command passes the var-file explicitly** (`-var-file=dev.tfvars`) — `variables.tf` is defaultless
and nothing is auto-loaded (see [Conventions](#conventions)).

```bash
# bootstrap (persistent) — applied once; rarely re-run
terraform -chdir=bootstrap init
terraform -chdir=bootstrap apply -var-file=dev.tfvars

# jenkins (persistent) — applied once; the CI controller box
terraform -chdir=jenkins init
terraform -chdir=jenkins apply -var-file=dev.tfvars

# platform (ephemeral) — the DAILY cycle
terraform -chdir=platform init
terraform -chdir=platform apply   -var-file=dev.tfvars   # day start
terraform -chdir=platform destroy -var-file=dev.tfvars   # day end → then run the orphan check
```

> Only **`platform/`** is in the daily apply→destroy ritual. `bootstrap/` and `jenkins/` persist.

## Teardown & orphan check

After **every** `platform/` destroy, verify **zero** stray resources (tags make them findable —
`stack=platform`):

- **Stray ELB** — the **single ingress LB is created by the in-cluster cloud-controller-manager, not
  Terraform.** Before `terraform destroy`, delete the `nginx-ingress` ArgoCD app/Service first so the CCM
  releases the LB (else it orphans). cert-manager/ESO create none — keep it to **one** LB.
- **Unattached EBS volumes** — the CNPG Postgres PVC is EBS-CSI-backed (`reclaimPolicy: Delete`); delete
  the `modelmatch-postgres` ArgoCD app **before** `terraform destroy` so the CSI driver removes its EBS
  volumes. A retained/forgotten volume is a cost orphan.
- **Unattached EIPs** — an EIP *attached* to the Jenkins box is **fine** (persistent); only **unattached**
  EIPs are orphans.
- **Stray NAT Gateways** — there should be exactly **`az_count` (2, one per AZ)** while `platform/` is up,
  and zero after destroy. `terraform -chdir=platform output -json nat_gateway_ids` /
  `nat_eip_allocation_ids` list them in AZ order (P37 replaced the singular `nat_gateway_id` output).

> Every wait/poll in teardown is **time-capped** — surface "stuck", never hang silently. `terraform
> destroy` on `platform/` has been run end-to-end many times (it works); the wrinkles below are
> **expected, not failures** — the Helm-stall + `state rm` step applies to *every* destroy.

### Graceful pre-destroy (nodes up — the normal end-of-day case)

With the cluster healthy, let the **in-cluster controllers** delete the AWS resources *they* created (the
ingress ELB **+ its SG**, the CSI EBS volumes) so nothing orphans — then destroy. Verified 2026-06-18.

1. **Disable root auto-sync** so deleting child apps doesn't trigger a re-sync:
   `kubectl -n argocd patch app root --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}'`
2. **Delete the stateful + ingress apps** so CSI/CCM release their AWS resources:
   `kubectl -n argocd delete app modelmatch-postgres nginx-ingress` (plus `kubectl -n app delete pvc --all`
   as belt-and-suspenders — `reclaimPolicy: Delete` → CSI removes the EBS). **Bounded-poll** until the ELB,
   its `k8s-elb-<hash>` SG, **and** the Postgres EBS volumes are all gone in AWS. (CCM removes the SG for
   you here — *unlike* the nodes=0 case below.)
3. `terraform -chdir=platform destroy -var-file=dev.tfvars -auto-approve` → then the Helm-stall step.

### The Helm-uninstall stall + `state rm` (BOTH cases — expect it)

`terraform destroy` **stalls ~5 min on `helm_release.argocd_apps` / `argocd`** and exits
`uninstallation … context deadline exceeded` (rc=1 — masked to 0 if a trailing `echo` follows in a wrapper,
so grep the log, not the harness rc). This is **not** nodes-specific: uninstalling the app-of-apps
cascade-deletes the whole ArgoCD app tree (cert-manager, monitoring, ECK Elasticsearch with slow
finalizers, …), which exceeds Helm's 5-min timeout **even with nodes up**. Terraform halts with the EKS
cluster + private subnets + VPC still in state. Fix:

1. **`terraform state rm`** the in-cluster-only resources — `helm_release.argocd`, `helm_release.argocd_apps`,
   and every `kubernetes_namespace.this[…]` (`app`/`argocd`/`logging`/`monitoring`). They die with the
   cluster; removing them strands **no** AWS resources.
2. **Re-run the destroy** → `Destroy complete!` (cluster ~8–10 min → private subnets → VPC).

### Differences when the 1AM cost Lambda already scaled nodes to 0

The Lambda scales the nodegroup to `desired=0`, **killing CCM/CSI/ArgoCD with the nodes** — so the graceful
pre-destroy can't run. Instead:

- **Manually delete the ingress ELB** (`aws elb delete-load-balancer`; find by tag
  `kubernetes.io/service-name=nginx-ingress/...`), bounded-poll until its ENIs clear.
- Do the `state rm` + re-destroy. The **VPC then stalls** because the ELB's leftover `k8s-elb-<hash>` SG was
  never removed (no CCM) → **`aws ec2 delete-security-group`** it → the next ~10 s retry finishes.
- **Manually delete the 2 detached Postgres EBS volumes** (`available`, not in TF state).

Then run the orphan check; confirm `bootstrap/` + `jenkins/` survived. (After a full teardown the
in-cluster Postgres DB is gone — `reclaimPolicy: Delete` — so re-seed on the next bring-up.)

### Automated teardown — `scripts/teardown-platform.sh` (budget kill switch + final teardown)

Since P34b (2026-09-07) the nodes=0 path above **is a script**, `scripts/teardown-platform.sh`, runnable
from a laptop or from the CodeBuild project **`modelmatch-platform-teardown`** (bootstrap stack,
`bootstrap/killswitch.tf`). It needs **no cluster auth** — only the AWS API + Terraform state:

1. scale the managed node group to 0 and wait for the instances to go (kills the in-cluster CCM/CSI so
   nothing re-creates what is deleted next — reproduces the nodes=0 state on purpose);
2. delete the CCM-created ingress **NLB** by its `kubernetes.io/cluster/<name>` tag (bounded poll);
3. `terraform state rm` the 6 in-cluster-only addresses (skips the Helm-uninstall stall);
4. `terraform -chdir=platform destroy -var-file=dev.tfvars -auto-approve`;
5. delete the detached CSI EBS volumes (CNPG PVCs) by cluster tag;
6. print the orphan check (NAT / unattached EIP / LBs / `available` EBS — all 0 after a live run).

**`DRY_RUN=1` is the default** (and the CodeBuild project's default): every step is read-only —
`terraform plan -destroy -refresh=false -lock=false` + lists. Every wait is capped and prints a `TIMEOUT`
marker; the last line is always `teardown-platform: mode=… rc=…`. An empty platform state exits 0.

Two things run it:

- **The budget kill switch (P34b):** the **90% ACTUAL** notification of `modelmatch-monthly-cost` → SNS
  `modelmatch-budget-alerts` (us-east-1) → Lambda `modelmatch-budget-killswitch` (us-east-1; fires on the
  ACTUAL ≥ 90% alert text for our budget **or** when the Budgets API reports spend/limit ≥ 0.9) →
  `codebuild:StartBuild` cross-region with **`DRY_RUN=0`**. Build state changes go via EventBridge to SNS
  `modelmatch-killswitch-events` (no email subscriber — an SNS email's unauthenticated unsubscribe link
  gets prefetched and kills it; the human trace is the budget-native 90% mail + CloudWatch Logs).
  Verified 2026-09-07: dry-run build green (plan =
  43 to destroy, rc=0, ~70 s), synthetic SNS publish → Lambda → build start in CloudWatch Logs; then armed.
- **The final teardown (P47), on demand** — `terraform -chdir=bootstrap output killswitch_final_teardown_cmd`:
  ```bash
  aws codebuild start-build --region ap-south-1 --project-name modelmatch-platform-teardown \
    --environment-variables-override name=DRY_RUN,value=0,type=PLAINTEXT
  ```
  Follow it in CloudWatch `/aws/codebuild/modelmatch-platform-teardown`; then run the orphan check
  yourself once more and confirm `bootstrap/` + `jenkins/` survived.

> The build's service role is **AdministratorAccess fenced by explicit Denies** (regions outside
> ap-south-1/us-east-1, deleting the state/ingestion buckets, ECR/budget/SNS/Lambda/CodeBuild deletes,
> destructive EC2 calls on `stack=jenkins`): a hand-built least-privilege policy cannot be proven by a
> dry run, and one missing action would leave a half-destroyed platform at the exact moment the budget
> is exhausted. A conscious trade-off, documented in the HLD §7.

> **Note on time limits:** Terraform's *own* per-resource destroy retry (the `Still destroying … NNm
> elapsed` line on `aws_vpc`) has **no client-side cap** — it loops on a dependency violation for many
> minutes. The fix is never a longer timeout; it's to inspect the VPC's remaining SGs/ENIs and clear the
> blocker (step 4), so the next retry succeeds.

## State backend

- **S3 remote state**, bucket `modelmatch-tfstate-957261948820` (account-id suffix = globally unique).
  Versioned, AES256-encrypted, all public access blocked, `prevent_destroy` on the bucket.
- **S3-native locking** (`use_lockfile = true`, Terraform ≥ 1.10) — **no DynamoDB lock table**. Backend
  blocks can't take variables, so bucket/key/region are literals kept in sync across the `backend.tf`
  files.

## Cost alerting (P2)

The financial ground-truth backstop, created **early** (before the first EKS apply) in the persistent
`bootstrap/` stack so it survives the daily `platform/` destroy.

```
  aws_budgets_budget (modelmatch-monthly-cost, $110/mo GROSS — credits not netted out)
        │   notify @ 80% ACTUAL  +  90% ACTUAL (kill-switch trigger)  +  100% FORECASTED
        ├──────────────────────────────────────────────┐
        ▼                                               ▼
  subscriber_email_addresses              subscriber_sns_topic_arns
  → stevelevit230@gmail.com               → aws_sns_topic modelmatch-budget-alerts ⚠ us-east-1
    (RELIABLE channel)                       ├─ email subscription (human trace)
                                             └─ Lambda modelmatch-budget-killswitch (P34b) → CodeBuild teardown
```

- **Gross, not net (Phase 2, 2026-09-06):** the account runs on Free Tier credits; the Terraform default
  `include_credit = true` would keep the measured "actual" near $0 until the credit was gone. `cost_types`
  tracks gross usage so the thresholds mean what they say.
- **90% ACTUAL = the kill switch** (see "Automated teardown" above): Budgets refreshes a few times a day,
  so the automated teardown lands at roughly 90% + one refresh of burn — still under the credit.

- **Budget-native email** is the **reliable** channel (no unsubscribe link → Gmail link-prefetch can't
  silently deactivate it).
- **SNS** is kept for the topic/subscription pattern + future fan-out (Slack/Lambda), **not** relied on
  for email (SNS raw-email subscriptions are fragile with Gmail's link prefetch).
- **Why us-east-1:** AWS Budgets is a global service whose backend runs in us-east-1, and a budget can only
  notify an SNS topic that also lives there (only the topic needs the `aws.us_east_1` alias).
- **Scope:** AWS-side spend (EKS control plane + NAT + EBS + transfer). Anthropic app-API spend is tracked
  separately on the Anthropic console. Per lesson-04, **cost stays out of Prometheus** — this Budget +
  Cost Explorer is the financial signal; token metrics are the operational one.

> **Verification performed 2026-06-10:** limit temporarily dropped to `$1` via `-var` against a
> month-to-date spend of `$1.55`. Both notifications fired as real budget-native emails from
> `budgets@costalerts.amazonaws.com`, delivered to the **inbox**: **ACTUAL** `$1.55 > $0.80` and
> **FORECASTED** `$4.82 > $1.00`. Limit restored to `$25`; final `terraform plan` = no changes.

## Conventions

- **Providers YES, third-party modules NO** (hard rule): official providers like `hashicorp/aws` are
  required, but **every module must be ours** (`source = "../modules/…"`). No registry/Git modules.
- **Defaultless `variables.tf` + explicit `-var-file=dev.tfvars`** (hard rule): variables declare inputs
  only (no defaults); concrete **non-secret** values live in a committed `dev.tfvars` per stack, passed
  explicitly on every command — **never** rely on auto-loaded `terraform.tfvars`/`*.auto.tfvars`. Secrets
  never go in tfvars (they reach the cluster via Secrets Manager + ESO/IRSA).
- **No hardcoded secrets / no static AWS keys**; least-privilege IRSA; **tag every resource** via
  `default_tags` (`owner` / `project` / `environment` / `stack`).
- **EKS** pinned to a current in-support version; **one NAT GW per AZ** (HA egress since P37); **ECR
  lifecycle policy** in place.
- `apply` (platform) at day start, **`destroy` (platform) at day end**, then the orphan check. An AWS
  Budget is wired to an alert (P2).
- Branching: `feature/<story-id>-<desc>` → PR (self-review) → `main`. Conventional Commits; SemVer tags.

## Contact

Steve Levit — stevelevit230@gmail.com
</content>
