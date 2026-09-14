# HM2 disabled identity provisioning — September 14, 2026

**Applied after Steve's separate “Yes” approval: 8 creates, 0 updates, 0 deletes.**
The trust anchor and both Roles Anywhere profiles are disabled. HM2 remains open;
HM3 has not started. Steve approved this source evidence and PR/merge publication
on September 14; later slices retain their review gates.
[Sanitized evidence](hm2-identity-applied-evidence.json) records actual policies,
identifiers, source/input/plan hashes and verification results.

## Applied scope

| Resource | Result |
|---|---|
| `modelmatch-home-server-issuer-v1` trust anchor | Disabled; enrolled public CA |
| `modelmatch-home-server-backup` / `modelmatch-home-server-bedrock` roles | Exact account, anchor ARN, issuer CN and respective workload CN; one-hour maximum |
| Two same-named inline workload policies | Backup prefix-scoped PutObject; existing two Nova profiles and profile-conditioned models |
| Two same-named Roles Anywhere profiles | Disabled; one matching role each; one-hour duration; matching session-policy ceiling |
| `home-server-identity-protection` on `modelmatch-platform-teardown-codebuild` | Additive deny; existing role, AdministratorAccess and original guardrails preserved |

The last create changes an existing principal's effective permissions. The guard was
created before the anchor. It protects routine teardown, not an administrator who can
remove its own deny. Keep resource creation on; use the session flag to disable authentication.

## Plan and apply

The full normally locked plan at **19:05:36 Asia/Jerusalem (16:05:36 UTC)** used explicit
`-var-file=dev.tfvars`, creation on, sessions off and the enrolled public CA. Its complete
parsed contents match the earlier published plan except for the timestamp. No source,
input, action or policy change was required. Steve separately approved this exact saved plan.

Preapply checks at **20:10:09 Asia/Jerusalem** again verified account/operator, no collisions
or identity state, backend, source/input/plan hashes and `DRY_RUN=1`. Terraform applied the
approved saved binary with normal locking; no new variables, targeting or lock bypass.
Profiles were created at **20:10:27 Asia/Jerusalem**.

- Operator: `arn:aws:iam::957261948820:user/steve`, profile `saa`, `ap-south-1`.
- Source: infra main `ceb964e26f4d1a131916f60381325d9446ec6744`.
- Saved plan SHA-256: `56db2b95c4aca77bbe39d7f58a6735a8e501c62a13c475ef5c6802aea686c929`.
- Inputs SHA-256: `d7ec9f25a13b0b8d6fb7027187d4f9165d7dfa6a6605c4b0a94446e0ddc1389a`.
- CA DER SHA-256: `b125437b857bf35561dd93e589b477d7284fce7e457a953ff83db859839b00b4`.
- CA expiry: **September 12, 2028, 01:38:19 UTC**.

Raw plans, state and readbacks remain outside Git in the private directory
`/Users/steve/.local/share/driftplain/home-server-identity/plans/20260914T160413Z-disabled-provisioning/`.
The saved plan has already been applied; do not reuse it.

## Verification and actual readback differences

Readback verified disabled states, exact public PEM/fingerprint, final role trusts,
workload/session policies, matching single-role lists, one-hour durations and tags.
Neither workload has extra policies. All eight resources belong to the encrypted persistent
state `modelmatch-tfstate-957261948820/home-server/identity/terraform.tfstate`, separate from
platform retirement. Twelve read-only simulations against the **installed** teardown
policies passed without injecting a proposed policy: identity access denied, existing
backup/recovery-key denials retained, platform-state Get/Put allowed.

The profiles use default `x509Subject=*` and `x509Issuer=*` mappings, including CN, plus
SAN `DNS`, `URI` and `Name/*`. No custom mapping was needed.
[AWS mapping semantics](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/attribute-mapping.html).
AWS omitted the optional unused `requireInstanceProperties` field; configuration and state
contain false. It is not an authentication control.
[AWS ProfileDetail](https://docs.aws.amazon.com/rolesanywhere/latest/APIReference/API_ProfileDetail.html).
Default CA/end-entity expiry notifications are enabled at 45 days, channel `ALL`; this
does not prove delivery of the required independent expiry/CRL/heartbeat monitoring.

The complete normally locked follow-up plan at **20:13:06 Asia/Jerusalem**, with explicit
`-var-file=dev.tfvars`, exited **0: no changes**. All eight resource actions and outputs are
no-op. Its JSON still records five post-create readback differences: each role now observes
its separately created inline policy; tags normalize null to `{}`, and profile managed
policy lists normalize null to `[]`. Exact policies/tags were verified. No corrective action
or refresh-only apply was performed. State version remained unchanged and the lock released.

Local checkers initially expected an explicit false API field, CN-only mapping entries and
an empty JSON drift list. They were corrected to verify the actual documented defaults and
exact five readback differences above. These were verification assumptions, not failed applies.

## Remaining gates

AWS production and `DRY_RUN=1` remain unchanged. No unchanged test suite, production health
or recovery drill was repeated. All five repos and pre-existing umbrella handoff/progress
content were preserved. CA/age custody was neither read nor modified. No leaf, CRL,
authentication exchange/enablement, scheduler, paid LLM, routing or teardown action occurred.

Initial leaves, populated-ledger recovery, current CRL import and live authentication tests
need separate approval. Scheduling and independent alerts retain later gates. Other HM2
operating decisions remain open. Cloud apply and source publication received separate
approvals. The September 14 publication approval covers these records, not future changes.
