# HM2 disabled-first identity plan — September 14, 2026

**Source and PR/merge publication approved September 14; cloud apply not approved or performed.** The fresh complete plan at
**18:14:39 Asia/Jerusalem (15:14:39 UTC)** proposes **8 creates, 0 updates, 0 deletes**.
[Proposed inputs](identity/dev.tfvars) enable resource creation, keep sessions disabled,
and bind the enrolled public CA. [Sanitized evidence](hm2-identity-plan-evidence.json)
records the complete action set, policies, source/input/plan hashes and verification.
HM2 remains open; HM3 has not started.

## Exact proposed scope

| Count | Resource | Behavior after a separately approved apply |
|---|---|---|
| 1 | Trust anchor `modelmatch-home-server-issuer-v1` | Enrolled public CA; **disabled** |
| 2 | IAM roles `modelmatch-home-server-backup` / `modelmatch-home-server-bedrock` | Exact account, anchor ARN, issuer CN and respective workload CN; one-hour maximum sessions |
| 2 | Same-named inline workload policies | Backup: prefix-scoped PutObject only. Bedrock: the existing two Nova profiles and profile-conditioned models |
| 2 | Same-named Roles Anywhere profiles | **Disabled**; each references only its matching role, one-hour duration, workload policy repeated as its session ceiling |
| 1 | Inline `home-server-identity-protection` on existing `modelmatch-platform-teardown-codebuild` | Additive deny for Roles Anywhere, the two workload roles and identity state prefix |

The eighth create changes an **existing principal's effective permissions**. It does not
replace that role or its original `guardrails` policy. The anchor depends on this guard.
Anchor, roles, profiles and the guard retain `prevent_destroy`; the two workload inline
policies do not have their own destruction protection. Creation stays on after provisioning; use the
session flag to disable authentication. These protections do not prevent an administrator
from changing configuration. In particular, the teardown role's existing AdministratorAccess
can remove its own inline guard; this scope protects routine teardown, not administrator
compromise. No bootstrap/platform change or broader IAM hardening is proposed.

Backup policy: only `s3:PutObject` on `postgres/hourly/*`, `postgres/daily/*` and `recovery/*`
in `modelmatch-home-server-backups-957261948820`. Bedrock policy: only `bedrock:InvokeModel`
on `apac.amazon.nova-lite-v1:0` and `global.amazon.nova-2-lite-v1:0`, plus their exact
foundation models conditioned on those profile ARNs. No workload grant to Secrets Manager,
ECR, IAM, Terraform state, backup reads/deletes or role chaining. Full policy JSON is in
the evidence; [main.tf](identity/main.tf) is unchanged from merged source.

## Verified inputs and live preflight

- Operator `arn:aws:iam::957261948820:user/steve`, profile `saa`, region `ap-south-1`.
  All five canonical repo revisions match the handoff. The two expected umbrella
  documentation edits are preserved. Local work uses `feature/e21-hm2-disabled-first-identity`.
- No identity state version existed before planning. No Roles Anywhere anchor/profile/CRL
  or matching workload IAM role existed; the additive policy name was unused. The existing
  teardown role had `guardrails` and AdministratorAccess. No collision or import is needed.
- Public certificate DER SHA-256:
  `b125437b857bf35561dd93e589b477d7284fce7e457a953ff83db859839b00b4`.
  The PEM in the saved plan equals the committed public certificate. OpenSSL and independent
  X.509 checks verify the self-signature, exact CN, RSA-3072/e=65537/SHA-256, critical
  `CA:true,pathlen:0`, certificate/CRL signing only, and current validity through
  **September 12, 2028, 01:38:19 UTC**. No private custody was read or changed.
- Persistent S3 backend: `modelmatch-tfstate-957261948820`, key
  `home-server/identity/terraform.tfstate`, encryption on, S3 native locking on.
  Initialized without migration or provider upgrade; Terraform 1.15.5 / AWS provider 5.100.0.
  The tracked provider lock is unchanged. No TF environment overrides or automatic tfvars.

## Verification and local plan

The new contract first failed against the old disabled/empty-CA inputs, then passed after
staging the enrolled CA with creation on and sessions off. Three existing scenarios now
explicitly set their negative/draft inputs, so they keep testing the intended case when
the real dev inputs change. All **8 Terraform contracts** pass against the changed input
set; validation and formatting pass. Unchanged issuer/renewal/SDK suites and completed
custody/recovery drills were not repeated.

Parsed checks cover every resource action, disabled flag, workload/session policy, CA,
account/region, defaultless variables, dependency and lifecycle protection. Saved-plan
Terraform source bytes match the checkout. The plan is complete/applyable with no failed
checks or deferred actions. Twelve read-only IAM simulations against the existing teardown
role **plus the proposed policy** pass: targeted identity operations are explicitly denied,
platform-state Get/Put stays allowed, and existing backup/recovery-key denies remain.
No policy was installed by these simulations.

Private local directory (0700, plan 0600):
`/Users/steve/.local/share/driftplain/home-server-identity/plans/20260914T151300Z-disabled-first/`.
It retains `reviewed.tfplan`, full text/JSON, preflight, simulations, postchecks and the
local full-plan verification script. Raw plans/state are outside Git.

```bash
AWS_PROFILE=saa AWS_REGION=ap-south-1 AWS_PAGER='' AWS_CLI_AUTO_PROMPT=off \
  terraform -chdir=home-server/identity plan -input=false -var-file=dev.tfvars \
  -lock-timeout=60s \
  -out=/Users/steve/.local/share/driftplain/home-server-identity/plans/20260914T151300Z-disabled-first/reviewed.tfplan \
  -no-color -detailed-exitcode
```

Exit **2** is the successful changes-present result. Normal locking acquired and released
the S3 lock; versioning retains its noncurrent lock object and delete marker. No identity
state object was written. Postcheck at **18:17:40 Asia/Jerusalem** confirms the lock released
and budget Lambda **`DRY_RUN=1`**. No IAM/CRL/leaf, scheduling, production or paid-model action.

- Saved plan SHA-256: `0c71a5b85ffc7217b1adb2f4e6191999950bbd3ad1d23c2b6bfd6e3771af24a0`.
- `dev.tfvars` SHA-256: `d7ec9f25a13b0b8d6fb7027187d4f9165d7dfa6a6605c4b0a94446e0ddc1389a`.

## Remaining review gates

There is no fresh collision or policy-scope fork to resolve in this plan. AWS-generated
anchor/role/profile ARNs remain unknown until apply. Consequently, the live plan cannot
fully render either role trust policy or profile role-ARN list. The unchanged source and
mocked contract verify the exact bindings; the saved-plan references verify their wiring.
After an approved apply, read back the final trust policies, session policies, disabled
states and default subject/issuer mappings before any authentication enablement.
[AWS trust conditions](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/trust-model.html)
and [default attribute mapping](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/attribute-mapping.html)
were checked September 14. Simulation is not a live certificate exchange or CRL proof.

Steve approved this source and its PR/merge publication on September 14. Cloud apply requires **separate explicit
approval of this concrete eight-resource scope**; publication approval is insufficient.
Before a later apply, recheck operator, resource collisions, backend and saved-plan/source/
input hashes; regenerate and review the complete plan if any changed or the plan is stale.
Keep normal locking. A saved plan already embeds its explicit dev.tfvars inputs; Terraform
does not accept new `-var-file` values when applying a saved plan. Do not replace reviewed
inputs or use an unsaved apply as a shortcut. [Terraform saved-plan behavior](https://developer.hashicorp.com/terraform/cli/commands/plan).

Initial leaves, populated-ledger recovery, imported current CRL, authentication tests,
renewal scheduling and alerts are later approved scopes. Image access, app secrets, routing,
rollback/legacy URLs and the final cost worksheet remain separate HM2 decisions. Preserve
AWS production, existing modelmatch identifiers and CA/age custody throughout.
