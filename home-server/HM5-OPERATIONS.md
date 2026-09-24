# HM5 operations runbook — sustainable public operation

**Status September 22, 2026:** every HM5 component runs; the home cluster is the only runtime
and holds the production data. AWS compute was retired on September 21
([record](AWS-COMPUTE-RETIREMENT.md)), HM7 restored the final export
`postgres/final/aws-20260921T210119Z/` at home and routed `driftplain.dev` /
`api.driftplain.dev` through the tunnel ([HM7-CUTOVER.md](HM7-CUTOVER.md)), and HM8 reviewed
what AWS still holds ([HM8-RETAINED-SERVICES.md](HM8-RETAINED-SERVICES.md): ownership, the
measured availability limits, retained cost about $2–3/month, releases from GitHub Actions to
GHCR). This runbook says what runs where, what to check, how to turn the gated pieces on, and
in which order.

## What runs where

| Component | Where | State | Config / status |
|---|---|---|---|
| Daily encrypted bundle from the Mac (roles, fingerprint, credential bundle) | Mac LaunchAgent `dev.driftplain.home-server-backup` → `home-server-backup-schedule.py run` (`daily_only: true` since September 18; the hourly slots skip) | **Running** (one daily upload; the hourly recovery point now comes from the in-cluster CronJob, whose export lacks roles, fingerprint and the credential bundle) | `~/.local/share/driftplain/home-server-backups/schedule.json`, status `schedule-status.json` |
| Daily identity maintenance (deadlines, CRL refresh, sealing-key re-backup, renewal no-op) | Mac LaunchAgent `dev.driftplain.home-server-maintenance` (09:15 local + at load) | **Running**; the `backup_required` warning clears at the next run (flag cleared September 18) | `maintenance.json`, status `maintenance-status.json`, log `maintenance.log` |
| Leaf renewal | Mac LaunchAgent `dev.driftplain.home-server-renewal` | **Running** since September 18 23:02 UTC (Keychain authorized; loaded with `launchctl load -w`; at login, 09:00 local, hourly retries; checks cached 20 h) | `~/.local/share/driftplain/home-server-identity/renewal.json` |
| Monitoring (kube-prometheus-stack 85.2.2, Prometheus 2 d / 1 GiB, Grafana, 10 home rules) | home child `monitoring` (+ `monitoring-dashboards`) | **Running**, all 15 scrape pools up | gitops `argocd/home-server/apps/monitoring.yaml` |
| Cluster heartbeat (pings only when no critical alert fires) | home child `heartbeat`, CronJob `*/5` | **Running** since September 18 22:26 UTC (gitops v0.30.0, sealed URL); notification test passed September 18 (`hm5-monitor-evidence.json`) | gitops `charts/home-server-heartbeat/values.yaml` |
| In-cluster backup CronJob (Roles Anywhere leaf) | home child `backup`, CronJob `23 * * * *` | **Running** hourly (gitops v0.27.0, image by digest, package public September 18); first run restored and verified | gitops `charts/home-server-backup/values.yaml`; image in `backup-image/` |
| Cloudflare Tunnel connector | home child `cloudflared`, Deployment | **1 replica** with the sealed token (gitops v0.28.0) | gitops `charts/home-server-cloudflared/values.yaml` |
| Cloudflare zones, tunnel, runtime + staging hosts | infra `cloudflare/` root | **Applied September 18**; `driftplain.dev` delegated and active September 18, staging exercise passed; HM7 adds the runtime pair and drops the dangling NLB twins; `modicum.cloud` will not be delegated (Steve, September 18: the domain is not needed; its zone stays applied and unused; HM8 kept both zones) | [`../cloudflare/README.md`](../cloudflare/README.md) |
| S3 retention lifecycle (hourly 1 d, daily 30 d, noncurrent/delete-marker cleanup) | infra `bootstrap/` | **Applied September 17** (three rules Enabled) | `hm5-backup-evidence.json` → `retention_plan` |
| Roles Anywhere trust anchor + both profiles | infra `home-server/identity` | **Enabled September 17** (`home_server_sessions_enabled=true`); sessions: 1 h, leaf-bound | `dev.tfvars`; emergency denial = flag back to false + apply |

## Routine checks

Weekly, or after any Mac sleep longer than a day:

```sh
source ~/.local/share/driftplain/home-server-identity/operator.env
R=~/.local/share/driftplain/home-server-backups
"$HOME_SERVER_PYTHON" $R/bin/home-server-backup-schedule.py check --config $R/schedule.json
"$HOME_SERVER_PYTHON" $R/bin/home-server-maintenance.py check --config $R/maintenance.json
```

Both exit non-zero when the last success is older than the configured maximum
(backup 2 h, maintenance 36 h) or a critical finding stands. On the cluster:

```sh
ssh home-server 'export KUBECONFIG=/home/steve/.kube/driftplain-home.yaml
kubectl -n argocd get application -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status
kubectl -n monitoring exec deploy/kube-prometheus-stack-grafana -c grafana -- wget -qO- "http://kube-prometheus-stack-prometheus:9090/api/v1/query?query=ALERTS%7Balertstate%3D%22firing%22%2Cseverity%21%3D%22none%22%7D"'
```

Expected: every Application `Synced Healthy`; the alert query returns an empty result
(`Watchdog` and `InfoInhibitor` carry `severity=none` and fire by design).

## Alert catalogue (home Prometheus)

| Alert | Fires when | Severity |
|---|---|---|
| HomeServerRootDiskFilling / Critical | root filesystem > 70 % / > 85 % | warning / critical |
| HomeServerCertificateExpiringSoon / ExpiryCritical | any cert-manager certificate < 14 d / < 3 d | warning / critical |
| HomeServerDatabaseNotReady | CNPG cluster not ready 5 m | critical |
| HomeServerAppUnavailable | backend or frontend has no ready replica 5 m | critical |
| HomeServerIngressUnavailable | F5 ingress has no ready replica 5 m | critical |
| HomeServerArgoCDNotHealthy | an Application is not Healthy/Synced 15 m | warning |
| HomeServerNodeNotReady | node NotReady 5 m | critical |
| HomeServerMemoryPressure | node memory > 90 % 10 m | warning |
| HomeServerTunnelDisconnected | 0 tunnel HA connections 5 m (only while the connector is enabled) | critical |
| HomeServerTunnelDegraded | < 2 tunnel HA connections 15 m | warning |

There is no Alertmanager. A critical alert withholds the cluster heartbeat, so the external
monitor is the notification path for cluster-side problems; the Mac jobs notify on their own
failures (macOS notification + non-zero exit) and withhold their heartbeats.

## Turning the gated pieces on

### 1. External monitor and heartbeats (account and the three heartbeat monitors created September 18)

Healthchecks.io (free tier; Steve's account, September 18) holds the three heartbeat checks. It
monitors only inbound pings, so the two staging HTTPS checks run as edge probes inside the cluster
heartbeat job (gitops v0.31.0, September 20, Steve's choice over a second service): each URL is
fetched through the Cloudflare edge and must answer 2xx with its keyword, or the ping is withheld
and Healthchecks.io raises DOWN. Create:

| Monitor | Type | Expected interval / grace | Where the URL goes |
|---|---|---|---|
| home-server backup (Mac daily bundle) | heartbeat | 24 h / 6 h | `schedule.json` → `heartbeat_url` (pinged once a day in `daily_only` mode) |
| home-server maintenance | heartbeat | 24 h / 6 h | `maintenance.json` → `heartbeat_url` |
| home-server cluster | heartbeat | 5 min / 15 min | gitops heartbeat chart: `enabled: true` + `sealed.encryptedUrl` (seal the URL strict-scope for `monitoring/home-server-heartbeat`, key `url`, with `home-server-sealing-keys.py seal-value`, value on stdin) |
| staging app / API (edge probes in the cluster heartbeat, gitops v0.31.0) | HTTPS keyword (`Driftplain`, `ok`) | 5 min, folded into the cluster ping | `https://staging.driftplain.dev/`, `https://api-staging.driftplain.dev/healthz` (chart `edgeProbes`; first passing run 2026-09-20 with "edge probes ok") |

Notification test (the HM5 acceptance item "alert tested"): done September 18. The CronJob was
suspended at 22:29:57 UTC and resumed at 2026-09-17T22:56:18Z (26 minutes, past the 5-minute interval plus
15-minute grace); the first ping after the resume succeeded at 2026-09-17T22:56:23Z. Steve confirmed the e-mails:
DOWN at 22:46:04 UTC ("success signal did not arrive on time, grace time passed"), UP at
22:56:21 UTC ("downtime lasted 10 minutes, 16 seconds"). Every home app
self-heals, so a bare `suspend` patch is reverted within seconds; the test switches automation off
on `home-server-root` and `heartbeat` first, patches the CronJob, and restores all three afterwards
(steps and timestamps in [`hm5-monitor-evidence.json`](hm5-monitor-evidence.json); Steve confirms
the down/up e-mails). For the backup heartbeat, set `enabled: false` in `schedule.json` for 90 minutes and restore it, or simply let
the Mac sleep through one interval.

### 2. Cloudflare (Steve: account + scoped token; delegation is a separate approval)

Follow [`../cloudflare/README.md`](../cloudflare/README.md) exactly: export/diff → plan →
apply (approval) → verify the zones answer like Route 53 → delegate `driftplain.dev` at Porkbun
(approval; modicum.cloud later) → seal the tunnel token for `cloudflared/home-server-cloudflared-token`
→ gitops PR setting `enabled: true` + `sealed.encryptedToken` → connector pod appears, the
`HomeServerTunnel*` rules become active.

Staging exercise once the connector runs (done September 18; results in [`hm5-staging-evidence.json`](hm5-staging-evidence.json)):

1. `https://staging.driftplain.dev/` → 200, `<title>Driftplain</title>`; `https://api-staging.driftplain.dev/healthz`, `/readyz` → 200.
2. Chat streaming through `api-staging` (the cache-bypass rule keeps it uncached: `cf-cache-status: DYNAMIC`).
3. Restart behaviour: `kubectl -n cloudflared rollout restart deploy/home-server-cloudflared`; measure the seconds until `/ready` returns 200 and the staging host answers again.
4. CORS / OAuth: the frontend served at `staging.driftplain.dev` must call `api-staging.driftplain.dev`, which needs a `staging` host set in the umbrella profile (`global.additionalHosts`) with that API URL — a gitops follow-up before this check; the existing Google client keeps its runtime origins. Decision September 18: Google sign-in stays production-only; staging keeps password login.
5. Confirm the runtime hosts still resolve to the AWS NLB (`dig +short driftplain.dev`).

### 3. In-cluster backup CronJob (replaces the Mac interim)

Done September 17: sessions enabled (identity root apply), the owner credential sealed with
`home-server-sealing-keys.py seal-backup-owner` (gitops v0.25.0), the helper image built for
linux/amd64 and pushed as `ghcr.io/steve-droid/home-server-backup:2026.09.17`
(`sha256:8ec804bd9d3ab16e0b1df0fec3aff2db7df6bb4bf8a3f968aa1ba77f4b64ffb7`; pg_dump 16.15, age
1.2.1, aws-cli 1.45.24, signing helper 1.8.5, uid 10001). September 18: the GHCR package is public,
the CronJob runs on schedule and a manual Job succeeded in 10 s
(`postgres/hourly/cluster-20260917T214304Z/`, dump 93,760 bytes + manifest, SSE-S3, SHA-256
checksums). That object was restored into the disposable target from S3 alone (receipt built from
`list_object_versions` + the manifest; `restore --roles-from <Mac daily export> --target disposable`,
roles from the verified Mac bundle, comparison against the live home source): match in all 16
categories, 23 tables, 2.4 s. In-cluster objects carry the dump only, so a restore needs roles
from a Mac export (`--roles-from`); the Mac schedule stays on until that gap is decided
(see [S3-BACKUPS.md](S3-BACKUPS.md)).

### 4. Identity automation

Done September 18: Steve set `renewal.json` `enabled: true` and ran the LaunchAgent's command
once in a terminal (the identity venv `python3`, `home-server-renew.py --config renewal.json`).
That run read the Keychain, verified the existing issuer bundle against S3 (the ledger digest already
had a September 15 receipt, so nothing new was uploaded), cleared the standing `backup_required`
flag, and then failed once with the tool's fixed message at a later step (cause not captured). A
read-only replay of every later step passed, and the next run (23:01:54 UTC, non-interactive from
a script) wrote `ledger.status.json` `status: ok`, so Keychain readback is authorized for that
`python3`. The plist
carries `Disabled: true`, so plain `launchctl load` fails with error 5; `launchctl load -w` loads it
and records the override. Loaded 23:02 UTC; the load-time run exited 0. Both leaves expire
December 14 and are not due before November 14. Next, `crl_publish: true` with `crl_id` and `trust_anchor_arn` after one
reviewed manual `update-crl` ([ISSUER.md](ISSUER.md)). CRL 3 expires October 20, 2026; the
maintenance job flags the refresh from October 10.

## Guardrails that never change here

DRY_RUN=1; no AWS production exists any more (retired September 21; the home instance holds the
restored final export since HM7 and the old Retain PV `pvc-7aba0fae-a8f2-4edc-a6fe-0c2c744e3edb`
is kept until a separate approval deletes it); modicum.cloud stays un-routed; never `noTLSVerify`; no Cloudflare Access on API/OAuth; secrets only as sealed
manifests or Mac-held files with mode 0600; any `terraform apply`, DNS delegation, session
enablement or bucket lifecycle apply is a separate explicit approval.
## Catalog refresh operator entry point (September 24, 2026)

See [CATALOG-REFRESH.md](CATALOG-REFRESH.md) for B16's disabled scheduler, separate
check/content freshness, pending reviews and bounded recovery. It is not installed
or enabled, and requires separate migration/deployment/import authorization.
