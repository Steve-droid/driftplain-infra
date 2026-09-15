# HM3 — encrypted production export and independent private home restore

**Runbook + result record.** Operator-only, run from the Mac. Source = the AWS CNPG primary in
EKS (explicit context); target = one private CNPG PostgreSQL 16 instance on the home K3s node,
reached over strict-key `ssh home-server` + `sudo -n` with the explicit home kubeconfig/context.
Sanitized results: [hm3-restore-evidence.json](hm3-restore-evidence.json). Raw receipts, encrypted
objects and the private `restore-result.json` stay under `~/.local/share/driftplain/home-server-backups/`
(0700/0600), never in Git.

Guardrails that apply to every step: production stays authoritative and writable (write freeze is
HM7); nothing restores into the EKS context (the tool refuses); no migrate/seed hook runs against the
restored copy; no secret value, password hash, row, dump or key reaches stdout, argv, Git or evidence.

## Tool and runtime

`home-server-database.py` (this directory), run under the stable operator runtime:

```sh
cd driftplain-infra/home-server
source ~/.local/share/driftplain/home-server-identity/operator.env   # private umask, PATH, config
"$HOME_SERVER_PYTHON" home-server-database.py <subcommand> …
```

| Subcommand | Does | Side |
|---|---|---|
| `export` | One `REPEATABLE READ` snapshot on the AWS primary: `pg_dump -Fc --snapshot`, the same-snapshot fingerprint (per-table row counts + content digests, full contract), `pg_dumpall --roles-only`, the original app credential Secrets (`modelmatch-app-secrets`, `modelmatch-db-app`); each age-encrypted to the published recipient; a public `manifest.json` of SHA-256 checksums. | source (EKS) |
| `upload --export-dir` | Single `PutObject` per object with SHA-256 checksums, profile `saa`; exact version IDs into `upload-receipt.json`. Refuses objects above 5 GB. | S3 |
| `download --receipt` | An **independent** copy by key + `VersionId` into a new `restore-<stamp>/` directory; S3 checksum and manifest verified. | S3 |
| `install-owner-secret --restore-dir --identity-source` | Creates `app/modelmatch-db-app` (basic-auth, original value) on home from the encrypted bundle. Refuses to overwrite a different existing Secret. | target (home) |
| `restore --restore-dir --identity-source` | Refuses a non-empty target → filtered app roles with original SCRAM verifiers → `pg_restore --single-transaction --exit-on-error` from the age pipe → target fingerprint → full comparison against the export snapshot. Writes the private `restore-result.json`. | target (home) |

`--identity-source aws` reads the private age identity from Secrets Manager
`modelmatch/home-server/recovery-key-v1` as the operator; `keychain` reads the same-named Mac
login-keychain item. The identity lives only in memory and feeds `age`'s stdin. Tests:
`venv/bin/python3 -W ignore test-home-server-database.py` (19; includes a Docker `postgres:16`
two-container rehearsal, skipped without Docker).

Home ArgoCD bootstrap helper: `home-server-argocd.sh {render|install|wait-argocd|apply-root|wait-apps|wait-cluster|status}`
(every wait is bounded and prints `TIMEOUT` instead of hanging).

## Procedure

1. **Export (source = EKS).** Verify the operator's public IP is in the EKS endpoint allowlist
   (`platform/dev.tfvars`, `public_access_cidrs`); if `kubectl` times out, that is the first thing to check.
   `export` refuses unless the context is the EKS cluster ARN and the CNPG cluster is two healthy
   instances. Output: `export-<stamp>/` with the four `.age` objects, `manifest.json`, `export-result.json`.
2. **Upload.** `upload --export-dir export-<stamp>` → `upload-receipt.json` (keys under
   `postgres/daily/hm3-<stamp>/` and `recovery/app-credentials/hm3-<stamp>.json.age`, version IDs).
3. **Independent download.** `download --receipt export-<stamp>/upload-receipt.json` → `restore-<stamp>/`.
   **Restore from this directory, never from the export directory.**
4. **Home ArgoCD (once per home cluster; GitOps profile `driftplain-gitops/argocd/home-server/`).**
   `home-server-argocd.sh render` (argo-cd 9.5.21 on the Mac) → `install` (namespace + server-side apply
   over SSH) → `wait-argocd` (five workloads, 600 s bound).
5. **Owner Secret before the Cluster exists.** `install-owner-secret --restore-dir restore-<stamp> --identity-source aws`.
   CNPG `bootstrap.initdb.secret` needs it at Cluster creation; it is never in Git (Sealed Secrets in HM4).
6. **Root Application.** The gitops profile PR must be **merged to `main`** first (the root's children sync
   from `main`). `home-server-argocd.sh apply-root` → `wait-apps` (root, `cnpg-operator`, `modelmatch-postgres`
   Synced/Healthy; 900 s bound) → `wait-cluster` (`Cluster in healthy state`, 1/1 instance; 900 s bound).
   Record PV name/path/UID and `reclaimPolicy: Retain` from the bound PVC.
7. **Restore + compare.** `restore --restore-dir restore-<stamp> --identity-source aws --timeout 1800`.
   Exit 0 and `comparison.match: true` in every category is the acceptance; any mismatch exits 1 and
   leaves everything in place — inspect `restore-<stamp>/*.stderr` and `restore-result.json`; never delete
   or re-seed the target to force a pass.
8. **Evidence.** Copy the sanitized summary (counts, booleans, timings, sizes, keys/versions/checksums,
   storage binding, limits) into `hm3-restore-evidence.json`; update the active backlog/HLD/progress.

## Result — September 15, 2026 (PASS)

| Step | When (UTC) | Measured |
|---|---|---|
| Export (EKS primary, 2 instances healthy) | 15:02:28–15:02:41 | 12.4 s; snapshot `00000008-0001327E-1`, LSN `0/EF000000`; dump 93,910 B + globals 1,890 B + fingerprint 33,813 B + credentials 998 B (all age-encrypted) |
| Upload (5 objects, single PUT + SHA-256) | 15:02:55 | 3.9 s; versioned keys under `postgres/daily/hm3-20260915T150228Z/` and `recovery/app-credentials/` |
| Independent download by key + version | 15:03:23 | 2.3 s; S3 checksums + manifest verified |
| Home ArgoCD 9.5.21 (server-side apply over SSH) | 15:43:19–15:44:00 | five workloads Ready in ~40 s |
| Owner Secret `app/modelmatch-db-app` | 15:44 | created from the bundle (`--identity-source aws`) |
| Root → children Synced/Healthy | 16:07:00 (merge seen) → 16:09:12 | cnpg-operator 0.29.0 / 1.30.0; Cluster created 16:08:30, healthy 1/1 |
| Restore + compare | 16:17:04–16:17:13 | **2.7 s** (roles 0.58 s, pg_restore 0.88 s, fingerprint 0.66 s); `comparison.match: true` in all 16 categories |

Restored: 23 tables (2,598 live rows; every table's row count **and** content digest match), 19
sequences, 1 view, 36 indexes, 60 constraints, 151 columns, 10 enums, `alembic_version a4b5c6d7e8f9`;
both app roles present with matching attributes and **matching SCRAM verifiers**; the PG16
auto-membership grant matches; database `modelmatch` owner/encoding/collate/ctype (`UTF8`, `C`/`C`)
identical; `server_version 16.10` identical. Restored size 9,646,563 B (source inventory 9,949,667 B;
the difference is bloat/free space, not rows). Storage: PVC `app/modelmatch-postgres-1`
(UID `7aba0fae-…`) bound to PV `pvc-7aba0fae-a8f2-4edc-a6fe-0c2c744e3edb`, **Retain**,
`/var/lib/rancher/k3s/storage/pvc-7aba0fae-…_app_modelmatch-postgres-1` (81 MiB after restore),
node affinity `driftplain-home`. Full sanitized record: [hm3-restore-evidence.json](hm3-restore-evidence.json).

The first `restore` attempt (16:15:23) was refused before any write: psql arguments appended after
the ssh remote command were unquoted, so the remote shell parsed the SQL and exited 2. Fixed in
`PodShell(remote=True)` with a focused test; the target was re-verified empty and the run repeated.

End-to-end from a cold Mac (export → S3 → download → bootstrap → restore) is well inside the
four-hour recovery target: the data path totals under 30 s; the bounded human/GitOps steps
(chart render, merge, sync) dominate. Nothing on AWS changed; DRY_RUN=1 remains.

## Recovery from S3-only assets (Mac lost)

Needs: an AWS operator identity allowed `GetObject`/`GetObjectVersion` on the backup bucket and
`GetSecretValue` on the recovery key (today: IAM user `steve`, profile `saa`); this repository; a
Python 3.12 + boto3 + `age` runtime; SSH access to home. Then: `download` by key/version (the manifest
object lists the objects; the receipt names the versions — without a receipt, list versions of the
`postgres/daily/<stamp>/` prefix), steps 4–7 above. The private recovery key never leaves Secrets
Manager/Keychain custody ([RECOVERY-KEY.md](RECOVERY-KEY.md)); home never holds it.

## What the export includes — and its consistency limits

- **In the snapshot (consistent):** every table's rows, sequences (`setval` as of the snapshot),
  schema, constraints, indexes, views, enums, ACLs, default privileges, `alembic_version`.
- **Outside the snapshot:** `pg_dumpall --roles-only` runs as a separate statement after the snapshot
  is exported; role attributes/passwords could in principle change between the two (they did not here:
  the same-snapshot fingerprint's role verifier digests match the restored roles).
- **Filtered on purpose:** only the two app roles (`modelmatch`, `modelmatch_chat_ro`) are replayed,
  with their original SCRAM verifiers and the PG16 auto-membership grant. CNPG-managed cluster roles
  (`postgres`, `streaming_replica`, `app`) are **not** replayed; the home operator creates its own.
  `SUPERUSER`/`REPLICATION`/`BYPASSRLS` attributes are refused.
- **Not a PITR:** a logical dump has no WAL; the recovery point is the snapshot time. Hourly
  scheduling/retention and the one-hour RPO result are HM5.
- **Different operator versions by design:** AWS CNPG 1.29.1 (chart 0.28.3; 1.29 is EOL
  September 29, 2026 — HM4/HM5 maintenance item) vs home CNPG 1.30.0 (chart 0.29.0, supports
  Kubernetes 1.34–1.36). Same PostgreSQL image `16.10-system-trixie` on both sides.
- **Storage:** home `local-path` PV under `/var/lib/rancher/k3s/storage/` with `Retain`; the PVC size
  request (10Gi) is not a disk quota. Deleting the Cluster/PVC leaves the directory and PV in place;
  re-binding a retained PV to a fresh Cluster is a manual step (clear `claimRef`, matching PVC name).

## Open operator items

- Mac Keychain readback of the recovery key is denied non-interactively (OSStatus -25293); authorize
  the operator venv `python3` for the item in Keychain Access, or keep using `--identity-source aws`.
- The hotspot allowlist entry (`46.210.169.119/32`) rotates with the hotspot; prune when unused.
