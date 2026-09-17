# HM5 operations runbook — sustainable public operation

**Status September 17, 2026 (evening):** every HM5 component is built, tested and either running
or gated on one of Steve's approvals/accounts. AWS still serves production; Route 53 is still
authoritative; nothing at home is publicly routed yet. This runbook says what runs where, what to
check, how to turn the gated pieces on, and in which order.

## What runs where

| Component | Where | State | Config / status |
|---|---|---|---|
| Hourly encrypted backup of the home CNPG instance | Mac LaunchAgent `dev.driftplain.home-server-backup` → `home-server-backup-schedule.py run` | **Running** (hourly; first run of each UTC day is `daily` with the credential bundle) | `~/.local/share/driftplain/home-server-backups/schedule.json`, status `schedule-status.json` |
| Daily identity maintenance (deadlines, CRL refresh, sealing-key re-backup, renewal no-op) | Mac LaunchAgent `dev.driftplain.home-server-maintenance` (09:15 local + at load) | **Running**; one standing warning (`backup_required` in the ledger) | `maintenance.json`, status `maintenance-status.json`, log `maintenance.log` |
| Leaf renewal | Mac LaunchAgent `dev.driftplain.home-server-renewal` | **Staged, disabled** (Keychain readback denied non-interactively) | `~/.local/share/driftplain/home-server-identity/renewal.json` |
| Monitoring (kube-prometheus-stack 85.2.2, Prometheus 2 d / 1 GiB, Grafana, 10 home rules) | home child `monitoring` (+ `monitoring-dashboards`) | **Running**, all 15 scrape pools up | gitops `argocd/home-server/apps/monitoring.yaml` |
| Cluster heartbeat (pings only when no critical alert fires) | home child `heartbeat`, CronJob `*/5` | **Suspended** until the URL is sealed | gitops `charts/home-server-heartbeat/values.yaml` |
| In-cluster backup CronJob (Roles Anywhere leaf) | home child `backup`, CronJob `23 * * * *` | **Suspended**; sessions enabled and owner credential sealed (September 17), waits for the public image digest | gitops `charts/home-server-backup/values.yaml`; image in `backup-image/` |
| Cloudflare Tunnel connector | home child `cloudflared`, Deployment | **0 replicas** until the token is sealed | gitops `charts/home-server-cloudflared/values.yaml` |
| Cloudflare zones, tunnel, staging hosts | infra `cloudflare/` root | **Plan-only** (no account yet) | [`../cloudflare/README.md`](../cloudflare/README.md) |
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

### 1. External monitor and heartbeats (Steve: create the account)

UptimeRobot Free (50 monitors, 5-minute checks, heartbeat monitors) or an equivalent. Create:

| Monitor | Type | Expected interval / grace | Where the URL goes |
|---|---|---|---|
| home-server backup | heartbeat | 60 min / 30 min | `schedule.json` → `heartbeat_url` |
| home-server maintenance | heartbeat | 24 h / 6 h | `maintenance.json` → `heartbeat_url` |
| home-server cluster | heartbeat | 5 min / 15 min | gitops heartbeat chart: `enabled: true` + `sealed.encryptedUrl` (seal the URL strict-scope for `monitoring/home-server-heartbeat`, key `url`, with `home-server-sealing-keys.py seal`) |
| staging app / API (after the tunnel) | HTTPS keyword | 5 min | `https://staging.driftplain.dev/`, `https://api-staging.driftplain.dev/healthz` |

Notification test (the HM5 acceptance item "alert tested"): pause the cluster heartbeat once by
suspending the CronJob for 20 minutes (`kubectl -n monitoring patch cronjob home-server-heartbeat
-p '{"spec":{"suspend":true}}'`, then ArgoCD self-heal restores it or patch it back), confirm the
"down" and "up" e-mails arrive, and record the timestamps in the evidence file. For the backup
heartbeat, set `enabled: false` in `schedule.json` for 90 minutes and restore it, or simply let
the Mac sleep through one interval.

### 2. Cloudflare (Steve: account + scoped token; delegation is a separate approval)

Follow [`../cloudflare/README.md`](../cloudflare/README.md) exactly: export/diff → plan →
apply (approval) → verify the zones answer like Route 53 → delegate `driftplain.dev` at Porkbun
(approval; modicum.cloud later) → seal the tunnel token for `cloudflared/home-server-cloudflared-token`
→ gitops PR setting `enabled: true` + `sealed.encryptedToken` → connector pod appears, the
`HomeServerTunnel*` rules become active.

Staging exercise once the connector runs (record in `hm5-staging-evidence.json`):

1. `https://staging.driftplain.dev/` → 200, `<title>Driftplain</title>`; `https://api-staging.driftplain.dev/healthz`, `/readyz` → 200.
2. Chat streaming through `api-staging` (the cache-bypass rule keeps it uncached: `cf-cache-status: DYNAMIC`).
3. Restart behaviour: `kubectl -n cloudflared rollout restart deploy/home-server-cloudflared`; measure the seconds until `/ready` returns 200 and the staging host answers again.
4. CORS / OAuth: the frontend served at `staging.driftplain.dev` must call `api-staging.driftplain.dev`, which needs a `staging` host set in the umbrella profile (`global.additionalHosts`) with that API URL — a gitops follow-up before this check; the existing Google client keeps its runtime origins, so Google sign-in on staging is a separate authorization.
5. Confirm the runtime hosts still resolve to the AWS NLB (`dig +short driftplain.dev`).

### 3. In-cluster backup CronJob (replaces the Mac interim)

Done September 17: sessions enabled (identity root apply), the owner credential sealed with
`home-server-sealing-keys.py seal-backup-owner` (gitops v0.25.0), the helper image built for
linux/amd64 and pushed as `ghcr.io/steve-droid/home-server-backup:2026.09.17`
(`sha256:8ec804bd9d3ab16e0b1df0fec3aff2db7df6bb4bf8a3f968aa1ba77f4b64ffb7`; pg_dump 16.15, age
1.2.1, aws-cli 1.45.24, signing helper 1.8.5, uid 10001; anonymous pull verified). Remaining: the
gitops PR that sets `enabled: true` + `image.digest`, one manual Job from the CronJob verified
with a `disposable-target` restore, then `enabled: false` in the Mac `schedule.json`.

### 4. Identity automation

Authorize the identity venv python in Keychain for non-interactive readback (today OSStatus
-25293), then `renewal.json` `enabled: true` (the first enabled run also clears the standing
`backup_required` warning), then `crl_publish: true` with `crl_id` and `trust_anchor_arn` after one
reviewed manual `update-crl` ([ISSUER.md](ISSUER.md)). CRL 3 expires October 20, 2026; the
maintenance job flags the refresh from October 10.

## Guardrails that never change here

DRY_RUN=1; AWS production authoritative and writable; no public runtime hostname routed at home
before HM7; never `noTLSVerify`; no Cloudflare Access on API/OAuth; secrets only as sealed
manifests or Mac-held files with mode 0600; any `terraform apply`, DNS delegation, session
enablement or bucket lifecycle apply is a separate explicit approval.
