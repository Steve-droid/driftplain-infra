# HM5 maintenance and upgrade runbook

The home service is one Ubuntu laptop running K3s, reconciled by ArgoCD from `driftplain-gitops`,
with the Mac as operator workstation. Every change reaches the cluster through a merged PR; the
Mac jobs are replaced by re-installing their runtime copy. This runbook lists the calendar, the
procedures and the checks. Nothing here touches AWS production.

## Calendar (as of September 17, 2026)

| When | What | Who / how |
|---|---|---|
| September 29, 2026 | AWS CNPG 1.29 end of life (production side) | HM7 consideration; home runs CNPG 1.30.0 |
| from October 10, 2026 | CRL refresh due (`crl_refresh_before_days: 10`); CRL 3 expires **October 20, 13:28 UTC** | maintenance job signs and publishes once `crl_publish` is on; until then the manual [ISSUER.md](ISSUER.md) procedure |
| ~October 15, 2026 | Sealed Secrets controller renews its sealing key (30-day cycle) | maintenance job runs `home-server-sealing-keys.py backup` automatically and withholds its heartbeat until the new key is backed up |
| from November 14, 2026 | Leaf renewal window (backup + bedrock leaves expire **December 14, 2026**) | renewal LaunchAgent once enabled; otherwise `home-server-renew.py` by hand |
| September 12, 2027 / yearly | Domain renewals at Porkbun (driftplain.dev, modicum.cloud) | Steve; keep auto-renew on |
| September 12, 2028 | `home-server-ca` (cluster CA) — 10-year, no action | — |
| September 12, 2028 | Roles Anywhere issuer CA expiry (725 d left) | re-enrollment decision in 2028; never re-enroll casually |
| monthly | cloudflared, curl, kube-prometheus-stack, CNPG operator/PostgreSQL minor releases; Ubuntu security updates | procedures below |

## Procedures

### Image or chart bump (any home child)

1. Resolve the new digest read-only: `crane digest <image>:<tag>`; for charts read the upstream release notes.
2. Edit the chart values or the child `Application` in `driftplain-gitops` on a feature branch; run
   `../driftplain-backend/.venv/bin/python tests/test_home_server_profile.py` and `helm lint`.
3. PR → merge → tag `v0.X.0`; ArgoCD applies it within three minutes (or annotate
   `home-server-root` with `argocd.argoproj.io/refresh=normal`). Watch the child reach
   `Synced Healthy`; for a Deployment, `kubectl rollout status`.
4. Roll back with `git revert` of the merge commit — never by editing the cluster.

Bumping the monitoring stack: keep `retention: 2d`, `retentionSize: 1GiB` and the container
limits; after the sync confirm all scrape pools are up and the 10 rules loaded.

### PostgreSQL / CNPG

The home instance is single-replica, so any CNPG or image change restarts the database
(≈ 30 s). Before it: trigger a backup (`home-server-backup-schedule.py run`), confirm the status
file's `last_success`, then bump the `cnpg-operator` child version or the image in
`charts/modelmatch-postgres/values-home-server.yaml`. The PV is `Retain`; it survives the restart.

### K3s and Ubuntu

Single node: an upgrade is downtime. Order: backup → confirm → `sudo apt update && sudo apt
upgrade` (kernel updates need a reboot; use the [coordinated reboot](RECOVERY.md#coordinated-reboot)
steps, ≈ 150 s) → for K3s, re-run the pinned install script with the new version (the node
restarts K3s in place; PVs, secrets and ArgoCD state persist) → `verify.sh` from the bootstrap
directory → ArgoCD children all Healthy → CNPG cluster ready → app health via port-forward.

### Mac operator runtime

The LaunchAgents execute the **runtime copy** under
`~/.local/share/driftplain/home-server-backups/bin/` (checksums in `SHA256SUMS`), never the Git
checkout. After merging a change to any `home-server-*.py` tool, re-stage the copy:

```sh
SRC=~/bootcamp/portfolio/driftplain-infra/home-server; RUN=~/.local/share/driftplain/home-server-backups
for f in home-server-database.py recovery-key.py recovery-key-v1.recipient home-server-backup-schedule.py \
         home-server-maintenance.py home-server-sealing-keys.py home-server-issuer.py home-server-renew.py; do
  install -m 0600 "$SRC/$f" "$RUN/bin/$f"; done
(cd "$RUN/bin" && shasum -a 256 home-server-*.py recovery-key.py recovery-key-v1.recipient > SHA256SUMS)
```

Then run each job once in the foreground (`… run --config …`) and `launchctl print gui/$(id -u)/<label>`
to confirm `last exit code = 0`. Plist changes: `launchctl bootout` + `bootstrap` the label.

### Identity

- **CRL refresh:** automatic once `crl_publish` is on; manual = `home-server-issuer.py crl` →
  `aws --profile saa --region ap-south-1 rolesanywhere update-crl …` → `verify-crl` (ISSUER.md).
- **Leaf renewal:** `home-server-renew.py` (renewal LaunchAgent) 30 days before expiry; the
  backup leaf lives in the `home-server-backup-identity` Secret and the CronJob mounts it fresh at
  each run; the bedrock leaf is unused until live Bedrock at home is approved.
- **Sealing keys:** after every controller renewal `home-server-sealing-keys.py backup` (automatic
  via the maintenance job); before re-sealing anything `fetch-cert`; never seal Roles Anywhere leaves.

### Disk and retention

Root disk was 11.4 % used on September 17. Alerts at 70 % / 85 %. Growth points: Prometheus (capped
at 1 GiB), the PostgreSQL PV, container images (`sudo k3s crictl rmi --prune`), the Mac's local
export copies (`keep_local_days: 2`). S3 growth is bounded once the lifecycle is applied (planned in
`bootstrap/`, apply is an approval); until then hourly objects accumulate at ≈ 130 KB each.

## Tests to run before any maintenance PR

| Repo | Command |
|---|---|
| gitops | `../driftplain-backend/.venv/bin/python tests/test_home_server_profile.py` (50) |
| infra, home-server tools | `"$HOME_SERVER_PYTHON" home-server/test-home-server-{backup-schedule,maintenance,sealing-keys,database,issuer,renewal}.py` |
| infra, Terraform | `terraform -chdir=<root> test -var-file=dev.tfvars` (`cloudflare/` adds `-var-file=records.tfvars.json`) |
| infra, DNS | `dns/scripts/test-route53-cloudflare-sync.py`; before any Cloudflare plan: `export` → `diff` must print OK |
