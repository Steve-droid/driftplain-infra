# HM7 — cutover to home

**Runbook + result record.** Operator-only, run from the Mac against the home K3s cluster
(`driftplain-home`, strict-key `ssh home-server` + `sudo -n`, explicit kubeconfig/context).
AWS compute was retired on September 21, 2026 ([record](AWS-COMPUTE-RETIREMENT.md)) after the
verified final export `postgres/final/aws-20260921T210119Z/`; there is no AWS rollback. This
slice made that export the home production database and routed the public runtime pair through
the Cloudflare tunnel. Sanitized results: [hm7-cutover-evidence.json](hm7-cutover-evidence.json).
Raw receipts stay under `~/.local/share/driftplain/home-server-backups/` (0700/0600), never in Git.

**Result (September 21, 21:30–22:05 UTC; September 22 local):** `https://driftplain.dev` and
`https://api.driftplain.dev` serve from home with the restored data (16/16 comparison categories
match, 23 tables / 2,598 rows); staging keeps serving; modicum.cloud stays un-routed.

Guardrails that applied: the old Retain PV is kept (deleting it is a separate approval); no
identity/custody change; no `platform/` apply; `LLM_CLIENT=fake` / `BLOB_STORE=fake` at home
(Steve's choice for the cutover — no paid LLM call); DRY_RUN=1; no secret value, hash or row in
output, evidence or Git.

## Approvals (Steve)

| Decision | Outcome |
|---|---|
| Cutover (Cloudflare apply + gitops `runtimeHostSet` merge) | approved together after the plan and the restored-data validation were presented |
| LLM/blob at home | fake client now ($0); Roles Anywhere Bedrock leaf is a follow-up slice |
| modicum.cloud | stays un-routed and undelegated |

## 1. Empty the home instance (keep the old PV)

Order matters because every home ArgoCD app self-heals: disable automation on `home-server-root`,
`modelmatch` and `modelmatch-postgres`, scale the backend to 0, delete the CNPG `Cluster`
`app/modelmatch-postgres` (7 s), confirm the PV `pvc-7aba0fae-a8f2-4edc-a6fe-0c2c744e3edb` is
`Released` with its directory intact (593 M), then re-run `install-owner-secret` (the sealed
value already equalled the bundle: `unchanged`) and re-enable the postgres app. CNPG
bootstrapped a fresh `initdb` on a new PV (`pvc-199e3046-482f-4a3f-8ef6-4843935d32d4`) in
about 30 s.

## 2. Restore the final export as production

```sh
source ~/.local/share/driftplain/home-server-identity/operator.env
"$HOME_SERVER_PYTHON" home-server-database.py restore \
  --restore-dir ~/.local/share/driftplain/home-server-backups/restore-20260921T210143Z \
  --identity-source aws --target production
```

2.438 s (roles 0.58, pg_restore 0.779, fingerprint 0.606); comparison `match: true` in every
category (server_version, database, alembic_version, extensions, schema, relations, columns,
constraints, indexes, views, enums, default ACL, memberships, sequences, roles, tables), 0
mismatches. The first attempt failed because the September 21 disposable proof had left its
`pg_restore.stderr` / `psql.stderr` / `restore-result.json` in the same directory: the tool now
rotates a previous run's files to `<name>.<stamp>` before writing (`rotate_run_files`, tested).
Then backend + root automation back on (backend Ready in ~20 s, 14/14 apps Synced/Healthy) and
the private validation (`home-server-validate.py --config-api-host api-staging.driftplain.dev`)
passed in 3.1 s.

## 3. Cloudflare: route the runtime pair (infra v0.36.0)

`cloudflare/` now describes `tunnel_hosts` (runtime `app`/`api` + `staging_app`/`staging_api`) and
mirrors only the Google TXT proof; the four NLB CNAME twins pointing at the deleted load balancer
are gone. The plan was **2 to add, 3 to change, 4 to destroy** (staging records and the TXT proof
`moved`, not replaced); the apply took 7 s (21:51:56–21:52:03 UTC). `api.driftplain.dev`
answered `/readyz` `{"status":"ready","db":"ok"}` through the edge within a minute.

```sh
source ~/.config/driftplain/cloudflare.env; export AWS_PROFILE=saa
terraform -chdir=cloudflare plan  -var-file=dev.tfvars -var-file=records.tfvars.json -out=cloudflare.tfplan
terraform -chdir=cloudflare apply cloudflare.tfplan        # separate approval
```

## 4. GitOps: select the runtime host set (gitops v0.33.0)

PR #66: `runtimeHostSet: driftplain`, the `driftplain` host set enabled beside `staging`, four
edge probes in the heartbeat. ArgoCD converged at 21:57:53 UTC (pods rolled 21:57:40). Rendered
at home: `API_BASE_URL` / `PUBLIC_BASE_URL` = `https://api.driftplain.dev`; CORS = private branded
origin + `https://driftplain.dev` + `https://staging.driftplain.dev`; five `modelmatch-*-driftplain`
ingress objects. **Order:** Cloudflare first, gitops second — the reverse points staging's
`config.js` at an unresolvable host and fails the edge probes.

## 5. Validate from outside

```sh
"$HOME_SERVER_PYTHON" home-server-validate.py --edge app=driftplain.dev,api=api.driftplain.dev \
  --config-api-host api.driftplain.dev [--edge-resolve 172.67.213.44]
```

The validator's new `--edge` mode runs the HM4 checks through the public edge (system trust store)
and adds a CORS preflight from `https://driftplain.dev` and the uncached-API check. Runtime pair:
**all pass in 9.6 s**; staging pair: all pass in 8.5 s. Certificate: Let's Encrypt for
`driftplain.dev` + `*.driftplain.dev` (to December 16, 2026, Cloudflare-managed); HTTP → 301
HTTPS on both hosts; one chat exchange through the edge: 200, `cf-cache-status: DYNAMIC`, stored.
Steve's own checks passed: a mobile-data load off Wi-Fi with a project dashboard opened (~22:10 UTC) and Google sign-in at https://driftplain.dev (~22:14 UTC; authorized origin unchanged from P38r).

## 6. Monitoring and the negative-cache incident

The house ISP resolver serves both the Mac and the home node. A probe of `https://driftplain.dev/`
from the Mac *before* the apply cached NODATA for the apex (SOA minimum 1800 s), so the first
heartbeat run with the new probes (22:00 UTC) withheld its ping ("Could not resolve host"). With
the last success at 21:55 and a 15-minute grace, Healthchecks was expected to report DOWN at about
22:15 and recover once the cache expired. It did: the apex resolved at home at 22:20:09 UTC and the
22:20 heartbeat pinged ("edge probes ok") after four withheld runs — a ≈5-minute false DOWN.
**Lesson:** never probe a hostname that does not exist yet from the home network.

## What changed in the repos

| Piece | Repo / path |
|---|---|
| `rotate_run_files` in the restore path + test; validator `--config-api-host`, `--edge`, `--edge-resolve` + tests | this directory |
| Cloudflare root: `tunnel_hosts`, address-twin collision / SNI preconditions, `moved` blocks, 10 mocked runs; `records.tfvars.json` re-rendered (TXT only); sync tool wording | `../cloudflare/`, `../dns/scripts/` |
| Runbooks: this file, `HM5-OPERATIONS.md` status/guardrails, `../cloudflare/README.md` | this directory |
| Home profile `runtimeHostSet: driftplain` + probes (55 tests) | driftplain-gitops v0.33.0 |

## Open after HM7

- **Honest UI note for the fake LLM at home:** chat currently answers with the generic
  "I wasn't able to look that up… try rephrasing" text. Making it say the assistant is offline
  needs a backend/frontend change and a new image (Jenkins is gone; local build → GHCR), or the
  Roles Anywhere Bedrock leaf. Follow-up slice.
- **HM8** retained-services review: Route 53 zones, the old Retain PV, ECR, the modicum.cloud zone.
