# HM5 lost-host recovery runbook

Scenario: the home laptop (or its disk) is lost, stolen or dead. The Mac, the AWS account,
the two Git repositories and the registrar/Cloudflare accounts are intact. Targets: **RTO 4 h**
with working hardware and internet at hand; **RPO ≤ 1 h + ~6 s** (hourly backups while the Mac is
awake; the status file's `last_success` names the exact recovery point). Before HM7 nothing
writes at home, so production on AWS is unaffected; after HM7 the first step is the rollback
routing in the HM7 runbook.

## Assets and where they live

| Asset | Location | Custody |
|---|---|---|
| Cluster desired state (children, charts, sealed manifests) | `driftplain-gitops` on GitHub | Git |
| Operator tools, Terraform, evidence, this runbook | `driftplain-infra` on GitHub | Git |
| Encrypted database exports (`postgres/hourly/`, `postgres/daily/`) + `recovery/app-credentials/` | versioned home-server backup bucket ([S3-BACKUPS.md](S3-BACKUPS.md)) | AWS, profile `saa` |
| Sealing-key backups `recovery/sealing-keys/<stamp>/` | same bucket + Mac copy | AWS + Mac |
| Recovery identity (age private key) | Secrets Manager `modelmatch/home-server/recovery-key-v1` + Mac Keychain ([RECOVERY-KEY.md](RECOVERY-KEY.md)) | never on the home host |
| Issuer CA key + ledger | Mac Keychain + encrypted recovery bundle in S3 ([IDENTITY.md](IDENTITY.md)) | Mac / AWS |
| Release images by digest | public GHCR (`ghcr.io/steve-droid/*`) | public |
| Tunnel token | Terraform state (`cloudflare/`, output `tunnel_token`) | AWS state bucket |
| Host bootstrap scripts | `home-server/` in infra (`prepare.sh`, `bootstrap.sh`, `verify.sh`) | Git |

Lost with the host, by design: the K3s admin kubeconfig, the cluster CA `home-server-ca` (a new one
is generated), the Roles Anywhere **leaf private keys** (`home-server-backup-identity`), the
PostgreSQL data (restored from S3), Prometheus history (2 days, not needed).

## Procedure

1. **Contain.** If the disk may be in someone else's hands: revoke both workload leaves
   (`home-server-issuer.py revoke` → publish the CRL, [ISSUER.md](ISSUER.md)); rotate the tunnel
   token (`terraform -chdir=cloudflare apply -replace=cloudflare_zero_trust_tunnel_cloudflared.home_server[0]`
   after approval) if the connector was enabled; rotate the app JWT key when the app is re-sealed
   (step 5). The sealing-key backups and DB exports are age-encrypted; the recovery identity was
   never on the host.
2. **Host.** Install Ubuntu on the Samsung disk, reserve `192.168.1.93` at the router,
   strict-key SSH + `sudo -n`, UFW/Tailscale per [REMOTE-ACCESS.md](REMOTE-ACCESS.md).
   If the Kingston is present, recreate its separate ext4 `/srv/home-server-storage` mount
   only after confirming the Samsung is the boot/root disk; no recovery-critical data is
   currently assigned to it. On the Mac, replace the old host key
   binding for `home-server` (the LaunchAgents use strict host-key checking and will fail closed
   until then).
3. **Cluster.** README "Run": `prepare.sh` → `bootstrap.sh --check` → `sudo … --apply` →
   `verify.sh`. Install ArgoCD and the home root exactly as in [HM3-RESTORE.md](HM3-RESTORE.md)
   (argo-cd chart, `argocd/home-server/root.yaml`). Children sync in wave order; `app-secrets`,
   `modelmatch` and `cloudflared` stay unhealthy until steps 4–6 (expected).
4. **Sealing keys.** The new controller has a new key, so the committed manifests do not unseal.
   Two options:
   - *Re-seal (default):* `home-server-sealing-keys.py fetch-cert`, download the newest daily
     export's credential bundle (`recovery/app-credentials/home-<stamp>.json.age`, listed by the
     daily `manifest.json`) with `home-server-database.py download`, then `seal --restore-dir …
     --identity-source aws --cert …` and merge the regenerated manifests. Also re-seal the heartbeat
     URL and the tunnel token for their namespaces.
   - *Restore the old key:* decrypt `recovery/sealing-keys/<stamp>/sealing-keys.json.age` with
     `recovery-key.py`, create the key Secret(s) in `sealed-secrets` labelled
     `sealedsecrets.bitnami.com/sealed-secrets-key=active`, restart the controller; the committed
     manifests unseal unchanged. Use this when many manifests exist.
5. **Database.** `modelmatch-postgres` creates an empty instance on a new Retain PV. Pick the
   newest `postgres/hourly/` or `postgres/daily/` object (`schedule-status.json` → `last_success_objects`,
   or list versions of the prefix), `download` by key + version (checksums verified), then
   `home-server-database.py restore --target production` from that restore directory (production
   here means the **home** instance; the tool never points at AWS) and the comparison step
   from HM3. Roles and verifiers come from `globals.sql.age`; the fingerprint proves the match.
6. **App.** `modelmatch` syncs with the GHCR digests; run `home-server-validate.py` over an SSH
   port-forward (HM4). No migration runs (alembic head = restored revision unless a newer release
   shipped a migration — then the migrate hook policy in `values-home-server.yaml` applies).
7. **Identity leaves.** Issue new backup/bedrock leaves ([IDENTITY.md](IDENTITY.md)
   bootstrap-leaf procedure), create `home-server-backup-identity` in `home-server-backups`;
   the old serials are on the CRL from step 1.
8. **Tunnel.** The cluster CA changed: paste the new `home-server-ca` public certificate into
   `charts/home-server-cloudflared/values.yaml` (`origin.caCertificate`) and
   `hm5-quick-tunnel-witness.yaml`, merge, re-seal the token; the connector reconnects with the
   same tunnel ID and the staging hosts come back. If the token was rotated, seal the new one.
9. **Monitoring and jobs.** `monitoring`, `heartbeat`, `backup` return with the root; re-seal the
   heartbeat URL (step 4). The Mac jobs resume by themselves once the host key binding is fixed;
   run both `check` commands from [HM5-OPERATIONS.md](HM5-OPERATIONS.md#routine-checks).
10. **Verify and record.** ArgoCD all `Synced Healthy`; alert query empty; one fresh hourly backup
    uploaded and restored into a `disposable-target`; staging URLs answer (if enabled); write
    `hm5-recovery-evidence.json` with the measured RTO and the recovery point used.

## If the Mac is lost instead

Follow [HM3-RESTORE.md — Recovery from S3-only assets](HM3-RESTORE.md#recovery-from-s3-only-assets-mac-lost)
for the data path and [IDENTITY.md](IDENTITY.md#certificate-custody-renewal-and-recovery) for the
issuer recovery bundle. The home cluster keeps running meanwhile; only the hourly backups and the
daily maintenance stop, which the external heartbeat monitor reports within 30 minutes / 6 hours.
## Catalog refresh recovery addition (September 24, 2026)

If B16 has subsequently been deployed, keep its catalog schedules suspended during
recovery. Restore immutable report bytes, accepted snapshots and operator audit
with the database; follow [CATALOG-REFRESH.md](CATALOG-REFRESH.md) before separately
approved manual checks or reenabling schedules. B16 delivery itself installs none.
