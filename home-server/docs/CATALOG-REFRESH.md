# Catalog refresh operations — B16

September 24, 2026. B16 delivers backend orchestration and disabled GitOps manifests.
**The scheduler is not installed or enabled.** No production import/migration,
deployment, runtime activation, AWS identity change or destructive retention occurred.
B8–B12 exact live model verification remains pending independently.

## Source of truth and prerequisites

- Backend [operator contract](https://github.com/Steve-droid/driftplain-backend/blob/main/app/catalog/imports/OPERATIONS.md):
  CLI, health semantics, report review, immutable audit and migration b16c0a7a0001.
- GitOps [chart](https://github.com/Steve-droid/driftplain-gitops/tree/main/charts/home-server-catalog-refresh):
  19 independent UTC six-hour jobs, all opt-in/suspended and outside watched Applications.
- [HM5 operations](HM5-OPERATIONS.md), [maintenance](HM5-MAINTENANCE.md) and
  [lost-host recovery](HM5-LOST-HOST-RECOVERY.md) own runtime/backup custody.

Deployment requires explicit authorization and fresh backup/restore evidence.
Roll out additive backend migrations and compatible API image first, then opt-in
metrics, then a reviewed importer digest and suspended chart. Reuse existing app
database custody, service account and DNS/HTTPS/DB network access. No new AWS
permission, model call, blob dependency, user key or startup seed is needed.
Keep current production pins until separately reviewed rollout.

## Enable/disable and bounded manual refresh

Set only intended sources.SOURCE=true. Select a verified backend digest, leave
suspend=true, and review a local Helm render. Adding a home ArgoCD child is itself
a deployment and is not authorized by this document. After explicit installation
and production-import approval, create one Job from the selected CronJob:

```sh
kubectl --context driftplain-home -n app create job catalog-check-<unique-id> \
  --from=cronjob/catalog-refresh-testgeneval
```

The copied Job retains its 300-second outer deadline, zero pod retries and resource
limits. This explicitly runs a Job even when the CronJob is suspended. The CLI's
240-second deadline and shared nonblocking source lock also apply. Capture only
safe structured status and Job events; never dump environment/database credentials.
For an already authorized operator environment with the same DB custody, direct
CLI `python -m app.catalog.imports.scheduled --source testgeneval --refresh`
is bounded too. Omitting --refresh is read-only health.

Enable recurring work only after a successful manual check, provenance/coverage
inspection and metrics scrape. Set monitoring.enabled=true and suspend=false in
a reviewed GitOps change. Disable future checks with global suspension or a source
switch; suspension does not stop an in-flight Job. Wait for its deadline unless
cancellation is separately approved. Source isolation means a failing feed does
not postpone the others; overlapping duplicate/manual invocations emit overlap
and do not refresh health.

## Distinguish health from evidence age

Use the read-only CLI for one source, backend /metrics through the existing private
monitoring access, and job events. Successful check freshness, failed attempts,
pending human review, accepted snapshot age and upstream publication age are
separate signals. Unknown publication dates stay unknown. Rechecking a pinned
old version is not discovery of a new benchmark and cannot enable a CI runtime.
Dynamic HTML may change without scores changing; redirects fail closed.

Missing successful checks beyond seven hours trigger warning rules, as do missing
source metrics. Inspect suspension/source switches, image pull/scheduling events,
DB reachability and safe importer failure codes. A process killed at its deadline
may not write failure state; stale successful-check age still exposes the gap.
Restore connectivity/configuration, run one bounded approved manual refresh, then
verify failure_count resets, successful-check time advances and last-good history
remains. Never relabel an old snapshot as newly published.

## Report review and snapshot reversal

Report checks store exact fetched public bytes/hash/URL in catalog_report_artifact,
separate from accepted observations. First acquisition is conservatively pending.
An operator with authorized DB read access can inspect the exact row by bound
source_id/content_hash, verify SHA-256 locally and retain a private review artifact.
Do not use an HTTP re-fetch as a substitute: its bytes may have changed.
Review the existing source's frozen version, parser and citation contracts. If
scores changed, validate and import the reviewed manifest through the existing
importer in a deadline-bounded operator job. Pinned versions cannot be switched
through source data or an arbitrary URL.

Only after inspecting those bytes, use --review-report EXACT_HASH --snapshot
CURRENT_ACCEPTED_ID --reason 'operator identity and reviewed conclusion'. This is
an explicit human attestation bound to the exact acquisition and active snapshot,
not automatic proof of extraction correctness. A no-score-change conclusion can
be acknowledged against the unchanged snapshot. New extraction promotion or
snapshot selection invalidates prior acknowledgement.

For an erroneous extraction, suspend its scheduled source first so a later
structured update cannot immediately replace the operator's choice. Review retained
snapshot IDs and select the prior accepted one with --select-snapshot ID --reason
'operator and reason'. It records old/new selection without rewriting snapshots,
observations, aliases, payloads, selection references or check freshness. Re-select
the later snapshot to undo. An unchanged historical fetch never silently reverses
the pointer. No delete/reimport, runtime activation or billing changes are involved.

## Recovery, storage and rollback

Include catalog report artifacts/operator audit in normal database backup/restore.
After host loss, restore first and keep catalog schedules suspended. Verify schema,
accepted pointers, report hashes, source-bound audit and historical execution
references before any new fetch. Run approved manual checks one source at a time;
only then restore selected schedules. No AWS compute rebuild is a recovery step.

Repeated identical report bytes deduplicate; changed bodies remain immutable.
Monitor PostgreSQL/root-disk growth, especially pages with dynamic content.
Existing disk alerts remain the capacity signal. No automated evidence retention
or destructive maintenance is included. The existing retention report is advisory.
Populated B16 downgrade refuses before DDL: retain additive schema/history, disable
metrics and schedules to roll back behavior. Do not restore over current data or
downgrade destructively to clear operational failures.

Live installation, notification delivery, missed-check/recovery drills and complete
catalog coverage remain future operational evidence. Local fake-transport and Helm
tests demonstrate code contracts only.
