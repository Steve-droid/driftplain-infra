# HM2 budget safeguard — applied September 13, 2026

**Steve explicitly approved both cloud updates; applied and verified.** AWS remained production
until September 21, 2026.

> **Outcome (September 21, 2026):** the 90 % ACTUAL trigger fired on September 18 at 23:16 UTC
> (September gross usage later reached $121 against the $110 budget, all credit-covered) and the
> CodeBuild teardown ran **as a dry run**, exactly as this safeguard intended. Compute was then
> retired by hand on September 21 ([record](AWS-COMPUTE-RETIREMENT.md)); the Lambda stays at
> `DRY_RUN=1` and the platform state is empty, so a future trigger exits 0 with nothing to do.
> HM8 decides whether the budget/kill-switch chain is re-scoped or removed.
Source publication was separately approved on September 13, 2026. Public routing changes
and AWS destruction still require their own approval.

## Applied scope

Terraform reported **0 creates, 2 in-place updates, 0 deletes**:

1. `aws_lambda_function.killswitch`: environment `DRY_RUN` changed `0` → `1`.
   At the $99 budget trigger, the existing teardown build now plans without deleting resources.
2. Existing ECR `modelmatch-agent-security`: ownership tag `stack` changed
   `manual-p38d` → `bootstrap`. Image identifiers and lifecycle policy remain unchanged.

The registry update accompanied adding this already state-managed repository to the configured
list. The initial plan exposed an omission that would have deleted the repository and policy.
Correcting the declaration preserved it. The ownership tag simply matches the persistent
bootstrap stack; tag normalization itself is not required to disable automatic teardown.
Four existing kill-switch outputs were also recorded in Terraform state, with no extra resource actions.

## Verification

- Account `957261948820`, region `ap-south-1` (Lambda/Budgets in us-east-1).
- Approved saved-plan and tfvars checksums matched; normal Terraform state locking used for apply.
- Lambda Active, LastUpdateStatus Successful, `DRY_RUN=1`; all other environment variables unchanged.
- Gross $110 budget configuration, notifications and subscribers matched the pre-apply inventory.
- All four registries retained; complete image/tag identifier lists matched before/after.
- No running teardown builds, no synthetic budget trigger or billed CodeBuild test launched.
- Three Ready EKS nodes, 14 Synced/Healthy ArgoCD apps, two healthy CNPG instances and public API ready/db ok.
- Fresh full follow-up plan: **No changes**, detailed exit code 0.

[Sanitized approved plan](hm2-budget-plan.json) · [Applied verification](hm2-budget-applied.json).
Verification timestamp: September 13, 2026, approximately 00:08 Asia/Jerusalem.
Private plans/logs formerly under `/tmp/driftplain-hm2-budget.yyqS6s/` were removed during
the approved September 13 cleanup. Sanitized evidence above is retained; generate a fresh
reviewed plan for future changes, never reconstruct/replay an old applied plan.

## Ongoing cost and teardown policy

Reported actual spend was **$48.833**, forecast **$101.66**. AWS billing is delayed.
The **$110 budget is an alert threshold, not a billing cap**. Steve explicitly accepted that
compute spending continues and may exceed it while automatic teardown is disabled. Existing
application token ceilings and the prohibition on unauthorized paid LLM calls remain.

Review spend daily during migration. AWS stays running until verified home cutover and separately
approved retirement. Any funded extension or data-preserving pause requires an explicit decision.
Do not automatically re-arm this Lambda on a date, after the post, or while source data is on AWS.
HM8 must replace this obsolete trigger with controls for the actually retained services.

The teardown script and manual live-build capability still exist; DRY_RUN=1 on the Lambda does
not protect against an operator explicitly launching a live build. Keep the separate teardown
approval gate. No new production backup or home restore was performed by this safeguard.
