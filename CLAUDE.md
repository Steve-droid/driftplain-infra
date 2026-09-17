# CLAUDE.md — driftplain-infra

## Claude Code continuation — HM5 sustainable public operation — September 17, 2026

Read [the HM5 handoff](../docs/session-handoffs/E21-home-hosting/2026-09-17-hm5-sustainable-public-operation.md)
first and follow [umbrella instructions](../CLAUDE.md). HM3 and HM4 are complete:
[HM3-RESTORE.md](home-server/HM3-RESTORE.md) / [HM4-HOME-APP.md](home-server/HM4-HOME-APP.md)
and their evidence are authoritative; do not repeat the export/restore, the GHCR image copy,
the sealing or the isolated validation. [HM2 acceptance](home-server/HM2-ACCEPTANCE.md)
supersedes earlier pending-state prose. Never re-enroll the CA.

**HM5 status (September 17, evening; infra v0.26.0, gitops v0.23.0):** landed — hourly Mac
backups + second independent restore + retention plan (v0.24.0), daily maintenance schedule,
sealing-key listing, backup image Dockerfile (v0.25.0), Route 53 export/diff tool, `cloudflare/`
root with mocked tests, quick-tunnel witness (v0.26.0), runbooks `HM5-OPERATIONS.md` /
`HM5-MAINTENANCE.md` / `HM5-LOST-HOST-RECOVERY.md`. Gated on Steve: Cloudflare account/token
(then plan → apply approval → delegation approval), external monitor URLs, Keychain
authorization for renewal/CRL signing, Roles Anywhere session enablement, bucket lifecycle apply,
backup image build/publish. Next tags: infra v0.32.0, gitops v0.30.0.

HM5 owns, in `home-server/`: the scheduled encrypted backup of the **home** CNPG instance
(Mac launchd interim now: export over strict-key SSH, single-PUT upload with checksums,
hourly + daily copies, sanitized status file, heartbeat, notification on failure; the
in-cluster CronJob path with the Roles Anywhere backup leaf is prepared but gated on session
enablement), the second independent restore from an automated object with the RPO measured,
the reviewed bucket lifecycle change in `bootstrap/` (hourly 1 day, daily 30 days, noncurrent
version and delete-marker cleanup — **plan only**, apply is a separate approval), the Mac
schedules (leaf renewal launchd, monthly CRL refresh, sealing-key re-backup after the ~October 15
controller key renewal) with heartbeats, the Route 53 zone export/diff tool and the Cloudflare
Terraform root (`cloudflare/`: both zones with identical records, AWS origin, the named tunnel
and staging hostnames — zone creation needs Steve's Cloudflare account/token; delegation at
Porkbun is a separate explicit approval), the `HM5-*.md` runbooks (operations, maintenance/
upgrade, lost-host recovery), sanitized evidence, and the cost worksheet update.

Mac operator profile `saa`, account 957261948820, ap-south-1 may read ECR and upload/download
backups; never copy its credentials or the private recovery key persistently to home.
Use strict `ssh home-server`, existing host-key binding and `sudo -n`. Keep Tailscale/UFW and
the explicit K3s kubeconfig/context separate from AWS. Anchor/profiles stay disabled, CRL 3
enabled until October 20; live Bedrock at home is a separate approval. Keep DRY_RUN=1 and AWS
production authoritative. Never restore into or point home at production. Never `noTLSVerify`;
no Cloudflare Access on API/OAuth flows. The GitOps repo owns the home profile (root, children,
umbrella values, connector, monitoring, backup CronJob); this repo owns operator tooling and evidence.

**Current working preference (Steve, September 15):** keep progressing and pause only
for critical architectural decisions. Plan, use focused tests for new behavior, verify and
self-review before routine commits/PRs; do not reintroduce the generic approval loops or
full-suite repetition below for unchanged work. This supersedes those older instructions
for this continuation. No paid LLM calls, public cutover, production teardown or destructive
source changes without explicit scope. No subagents/review agents, unsolicited diagrams
or additional tasks. Keep answers concise.

## Home migration override — September 12, 2026

See `home-server/README.md` and `home-server/RESULTS.md` for the isolated home profile and live evidence.
Steve authorized a single-node home K3s migration while keeping AWS production running.
Do not apply the historical daily-destroy instructions during migration. Home local
persistent storage is an intentional departure from EBS; production needs a reviewed
Retain policy and tested off-machine backup/restore before cutover. Keep AWS Terraform
and public DNS unchanged until their separately reviewed steps. Stop before commits.

IAM Roles Anywhere is selected for home AWS identity. The separate persistent root
`home-server/identity/` and [identity runbook](home-server/IDENTITY.md) describe the enrolled
issuer and AWS deployment. The [September 14 apply](home-server/IDENTITY-APPLIED.md)
provisioned eight resources after separate approval, with creation on and sessions off.
The enrolled public CA and disabled resources are verified; leaves/CRL/enablement/scheduling
remain gated. Use explicit `-var-file=dev.tfvars` for fresh plans; never include
it in platform retirement. Automatic leaf renewal is staged on the Mac, not installed.

> Driftplain was previously Modicum / ModelMatch. The four public repositories use `driftplain-*`; existing infrastructure, images, database names, metrics and CI credential/environment identifiers retain `modelmatch` for compatibility. Modicum DNS is live; P38r added and delegated Driftplain without replacing that zone. Public Google ownership TXT proof lives in the same DNS state.

**Status: ACTIVE** (activated P1, 2026-06-10). Terraform for Driftplain's AWS infrastructure.
Region **`ap-south-1`**, account **`957261948820`**.

> Part of the [Modicum portfolio build](../CLAUDE.md). Spec: `../docs/planning/architecture.md` §12,
> the locked DevOps backlog `../docs/planning/01-devops-backlog.md` (Epic **E10**), and
> `../docs/instructions/lesson-03` (Infrastructure) + `lesson-04` (FinOps).

## Three stacks, three lifecycles (the central design rule)

So the daily `destroy` can never nuke state, images, the budget, **or the CI controller**, Terraform is
split into three root stacks with **separate state**:

```
driftplain-infra/
├── bootstrap/   # PERSISTENT — applied once, NEVER in the daily destroy
│   └── S3 state bucket + lock (P1) · AWS Budget+SNS (P2) · ECR repos imported (P5) · S3 ingestion bucket (P6)
│       · budget kill switch (P34b): Lambda (us-east-1) + CodeBuild `modelmatch-platform-teardown` running scripts/teardown-platform.sh
├── platform/    # EPHEMERAL — `apply` at day start / `destroy` at day end
│   └── VPC + NAT per AZ (P3/P37) · EKS+OIDC+nodes (P4) · IRSA roles A/B (P7)   ← the ONLY stack destroyed daily
├── jenkins/     # PERSISTENT — CI controller; survives every platform destroy (P16, E11; Roey 2026-06-15)
│   └── Jenkins EC2 + EIP + SG + IAM instance profile + persistent EBS (/var/lib/jenkins) + optional backup bucket
├── scripts/     # teardown-platform.sh (platform teardown, DRY_RUN=1 default) + its CodeBuild buildspec — never edit without a DRY_RUN=1 run
└── modules/     # our OWN reusable modules (vpc, eks, ecr, iam-irsa, …) — populated from P3
```

- `bootstrap/` outputs (state-bucket ARN, ECR URLs, ingestion-bucket ARN) are read by `platform/` via
  `terraform_remote_state` — never duplicated.
- Each stack has its own `versions.tf` · `providers.tf` · `backend.tf` (+ `variables.tf` / `main.tf` /
  `outputs.tf` as needed). Same S3 bucket, **different state key** (`bootstrap/…` vs `platform/…` vs
  `jenkins/…`).
- **`jenkins/` is persistent** (like `bootstrap/`) but kept a **separate root** because it isn't
  foundational *shared* infra — it has its own lifecycle + operational surface (EC2, plugins, jobs, an
  attached disk). Built/proven in **P16**; destroyed only intentionally, never in the daily ritual.
  `JENKINS_HOME` lives on the persistent EBS volume; S3 = encrypted backup/DR only. See
  `../docs/planning/mentor-notes-2026-06-15.md` §1–§3.

## State backend

- **S3 remote state**, bucket `modelmatch-tfstate-957261948820` (account-id suffix = globally unique).
  Versioned, AES256-encrypted, all public access blocked, `prevent_destroy` on the bucket.
- **Locking is S3-native** (`use_lockfile = true`, needs Terraform **≥ 1.10** — we run 1.15.5).
  **No DynamoDB lock table.** Backend blocks can't take variables, so bucket/key/region are literals
  kept in sync across the two `backend.tf` files.
- Chicken-and-egg resolved by **create-then-migrate**: `bootstrap/` was applied once with local state to
  create the bucket, then `terraform init -migrate-state` moved its own state into the bucket.

## Module rule — providers YES, third-party modules NO (Roey, hard rule)

**Providers are allowed and required** — use the official `hashicorp/aws` (and other official) providers
normally. The ban is on **third-party / community / registry Terraform _modules_**, because the course
grades whether *we* understand the AWS wiring (VPC, subnets, route tables, NAT, EKS, IAM/IRSA, ECR, S3
state) rather than hiding it behind someone else's module.

| Allowed | Not allowed |
|---|---|
| `source = "../modules/vpc"` (our own) | `source = "terraform-aws-modules/vpc/aws"` |
| direct `resource "aws_*"` blocks we write | `source = "terraform-aws-modules/eks/aws"` |
| `provider "aws" { … }` (official provider) | `source = "github.com/…"` / any registry module |

All reusable logic lives in **our own** `modules/` only. When in doubt: if `terraform init` would
download a module from the registry or a Git URL, it's banned.

## Variables & values — defaultless `variables.tf` + explicit `-var-file` (Roey, hard rule)

**`variables.tf` declares inputs only, with NO defaults.** Concrete **non-secret** values live in an
explicit `.tfvars` file (`bootstrap/dev.tfvars`, `platform/dev.tfvars`), passed on **every** command
with **`-var-file=dev.tfvars`**. **Do not rely on auto-loaded `terraform.tfvars` / `*.auto.tfvars`** —
the var-file is always named explicitly so nothing is implicit.

- **Why:** predictability — a forgotten `default` (or a silently auto-loaded tfvars) can feed a
  `terraform apply` and create resources you didn't mean to. Every value is explicit and in one place.
- **Modules** (`modules/*/variables.tf`): defaultless variables = the module interface (values come
  from the calling stack, never module defaults).
- **Stack roots** (`bootstrap/`, `platform/`, and `jenkins/` once it lands at P16): defaultless
  `variables.tf` + a committed non-secret `dev.tfvars`; `module`/`provider` blocks read `var.*` (e.g.
  region is `var.aws_region`).
- **Secrets never go in tfvars** — they reach the cluster via Secrets Manager (ESO + IRSA). The
  committed `dev.tfvars` is non-secret on purpose; `.gitignore` ignores `*.tfvars` but **un-ignores
  `bootstrap/dev.tfvars` + `platform/dev.tfvars`** specifically (`jenkins/dev.tfvars` joins the un-ignore
  list when the `jenkins/` root is created at P16).
- Always run `plan`/`apply` with the var-file explicit:
  `terraform -chdir=bootstrap <cmd> -var-file=dev.tfvars`,
  `terraform -chdir=platform <cmd> -var-file=dev.tfvars`, and
  `terraform -chdir=jenkins <cmd> -var-file=dev.tfvars` (once `jenkins/` exists).

> **All three stack roots follow this rule** (`jenkins/` adopts it when it lands at P16). `bootstrap/`
> originally carried `default`s (it predated the rule); it was migrated to defaultless `variables.tf` +
> `bootstrap/dev.tfvars` — the tfvars values are byte-identical to the old defaults, so the migration is
> a no-op to the plan.

## Tagging (FinOps + orphan hunt depend on it)

`default_tags` on the AWS provider in **every** stack stamps all taggable resources:

| key | value |
|---|---|
| `owner` | `steve` |
| `project` | `modelmatch` |
| `environment` | `dev` |
| `stack` | `bootstrap` \| `platform` \| `jenkins` (lifecycle discriminator for orphan-hunting) |

## Lifecycle & cost discipline

- `terraform apply` on **`platform/`** at day start → **`terraform destroy` on `platform/` at day end**.
  `bootstrap/` **and `jenkins/`** are persistent — **never** in the daily ritual (`jenkins/` is destroyed
  only intentionally).
- **Orphan ritual** after every platform destroy: verify **zero** stray ELBs, **unattached EBS volumes**,
  unattached EIPs, NAT GWs. (An EIP *attached* to the Jenkins box is fine.) Tags make orphans findable.
- **EKS:** latest in-support k8s version (stale = silent ~6× control-plane charge). **One NAT GW per AZ**
  (`az_count` NATs, each private RT → its own AZ's NAT; P37, 2026-09-07 — replaced the single-NAT egress
  SPOF so the P40 AZ-failure drill keeps egress in the surviving AZ; ≈ +$33/mo). **ECR lifecycle policy**
  (expire untagged, keep last N).
- **No hardcoded secrets, no static AWS keys.** In-cluster → Bedrock/S3 via **IRSA** (OIDC → role scoped
  to the 2 Nova ARNs + the S3 bucket → annotated on the backend SA). Auth on this machine = the default
  credential chain (`default` profile); no `profile` is hardcoded in HCL.

## Repo boundaries (what's here vs not)

- **Jenkins** — **revised 2026-06-15: now IN scope for this repo**, managed by the persistent **`jenkins/`**
  root above (P16; it was previously planned as pre-provisioned / out-of-Terraform). Kept **out of the
  daily-destroyed `platform/` stack** so `destroy` can't take out CI, and still **outside the EKS cluster**
  (standalone EC2). ECR *is* here too (managed AWS service, outside the cluster).
- **No managed database (no RDS).** The DB is **in-cluster** (Helm subchart + PVC — see
  [`driftplain-gitops`](../driftplain-gitops)).

  > **Postgres persistence (forward note for gitops P13/P14, Roey 2026-06-10):** the PVC must be backed
  > by the **AWS EBS CSI driver** (EBS-backed StorageClass) — never hostPath / container disk. The volume
  > must survive pod restarts and Helm redeploys. If survival across **cluster rebuilds** is required,
  > use `reclaimPolicy: Retain` + a documented re-bind/restore procedure (or EBS snapshots). A retained
  > EBS volume is a **cost orphan** if forgotten — document how it's found, reattached, or deleted in the
  > orphan ritual. (Driver itself installs via EKS addon / gitops; persistence config lives in the chart.)

## Branching

`feature/<story-id>-<desc>` → PR (self-review) → `main` (protected). The scaffold/shell commit was the
one direct-to-`main` exception; everything since is on feature branches. Confirm with Steve before any
commit/push. Local-first (no remote yet).

## P38m DNS lifecycle (2026-09-09)

The fourth root, `dns/`, owns the persistent Route 53 public zone and app/API aliases;
state key `dns/terraform.tfstate`. Registration remains at Porkbun. The Kubernetes-owned
NLB is read-only data. Keep the zone through platform teardown; disable aliases with
`records_enabled=false`. Use `AWS_PROFILE=saa` and explicit `-var-file=dev.tfvars`.
See [dns/README.md](dns/README.md) for delegation, staged HTTPS, rollback and rebuilds.

## Home-server naming

Use `home_server` in Terraform/Python identifiers and `home-server` in filenames, directories
and resource names. Avoid bare `home` for new server-specific names. Source lives in
`home-server/`. Existing installed K3s identifiers (`driftplain-home`),
its kubeconfig path and `10-home.conf` are compatibility values; changing them requires a
separate runtime migration. Preserve historical evidence IDs, Git branch names and Linux
`/home/steve` paths verbatim.
