# Driftplain home-server Kubernetes migration

Started September 12, 2026. This implements **E21 — durable home hosting** in the
[active backlog](../../docs/planning/02-showcase-backlog.md), which must finish before P39.
Driftplain will stay online and be maintained; it has no showcase expiry date. AWS compute
was retired on September 21, 2026 and production moved here at HM7; Steve approved each slice
at its review gate.

## Status — September 22, 2026

HM5, HM7 and HM8 are complete: production serves from this cluster
([HM7-CUTOVER.md](HM7-CUTOVER.md)); the retained AWS services, ownership and availability limits
are in [HM8-RETAINED-SERVICES.md](HM8-RETAINED-SERVICES.md); day-to-day operation is
[HM5-OPERATIONS.md](HM5-OPERATIONS.md). The sections below are the build record in slice order.

## HM5 sustainable public operation (complete September 18)

Runbooks: [HM5-OPERATIONS.md](HM5-OPERATIONS.md) (what runs where, checks, how to enable the
gated pieces), [HM5-MAINTENANCE.md](HM5-MAINTENANCE.md) (calendar, upgrades, Mac runtime copy),
[HM5-LOST-HOST-RECOVERY.md](HM5-LOST-HOST-RECOVERY.md) (rebuild from Git + S3 + Mac custody).
Public route: the Cloudflare root [`../cloudflare/`](../cloudflare/README.md) (both zones with
DNS-only twins of every Route 53 record, the named tunnel, staging hosts only; applied September 18,
zones pending delegation, which is a separate approval) with
[`dns/scripts/route53-cloudflare-sync.py`](../dns/scripts/route53-cloudflare-sync.py) for the
export/diff; the gitops `cloudflared` child (v0.28.0) runs one connector with the sealed token.
The [quick-tunnel witness](hm5-quick-tunnel-witness.yaml) proved the connector image, the mounted
`home-server-ca` pool and the fail-closed origin TLS path ([evidence](hm5-quick-tunnel-evidence.json)).

HM5 (sustainable public operation, from September 17): the home instance is backed up hourly by
a Mac LaunchAgent (`dev.driftplain.home-server-backup.plist` → `home-server-backup-schedule.py`
→ `home-server-database.py export --source home` / `upload`), the first export of each UTC day
carrying the credential bundle; a second independent restore from an automated object matched in
all 16 categories inside a disposable CNPG target (`disposable-target create|delete`); the reviewed
S3 retention lifecycle is planned in `bootstrap/` pending approval. See
[S3-BACKUPS.md](S3-BACKUPS.md) and [hm5-backup-evidence.json](hm5-backup-evidence.json).
A second daily LaunchAgent (`dev.driftplain.home-server-maintenance.plist` →
`home-server-maintenance.py`) grades the identity deadlines from the public ledger, re-backs up a
renewed sealing key (`home-server-sealing-keys.py list|backup`), refreshes the CRL when due and
runs the (still disabled) leaf renewal; see [IDENTITY.md](IDENTITY.md#certificate-custody-renewal-and-recovery)
and [hm5-maintenance-evidence.json](hm5-maintenance-evidence.json). The gitops repo (v0.22.0)
carries the home monitoring, heartbeat and gated backup children; the helper image for the
in-cluster backup CronJob is prepared in [backup-image/](backup-image/Dockerfile) (not built).

[HM4-HOME-APP.md](HM4-HOME-APP.md) is the runbook and result record for putting the application
on the restored home database under the same ArgoCD root: platform children, sealing-key custody
(`home-server-sealing-keys.py`: fetch-cert / backup / seal / verify-recovery / adopt), the reviewed
GHCR image copy, the migration policy, the ingestion grant plan and isolated validation; sanitized
results in [hm4-home-app-evidence.json](hm4-home-app-evidence.json). HM3 is complete
([HM3-RESTORE.md](HM3-RESTORE.md), [evidence](hm3-restore-evidence.json)); HM2 is complete
([acceptance](HM2-ACCEPTANCE.md)); the section below is its historical summary.

## HM2 host recovery and operating design (complete September 15)

HM1 and the recovery source are approved for publication. HM2 includes
[the recovery drill](RECOVERY.md), [operating choices and costs](OPERATING-DESIGN.md)
and `recovery-check.py`. The script never initiates a reboot or service interruption;
it preserves a disposable witness across separately coordinated actions. Runtime evidence
is recorded in [RESULTS.md](RESULTS.md). The [budget safeguard](BUDGET-SAFEGUARD.md) was
approved and applied September 13: automatic teardown is now dry-run, with budget alerts
retained. Steve selected S3 for hourly backups; [the protected bucket and recovery design](S3-BACKUPS.md)
were approved and applied September 13; the bucket now holds the issuer recovery archive,
while the first production export remains pending.
[Recovery-key custody](RECOVERY-KEY.md) now uses AWS Secrets Manager plus a local-only Mac
Keychain copy. Roles Anywhere and automatic certificate renewal are selected, with source
merged in PR #18; [issuer tooling](ISSUER.md) is merged in PR #21. Separately approved
[CA enrollment and independent empty-ledger recovery](ISSUER-ENROLLMENT.md) now pass;
AWS identity/leaf deployment and scheduling remain pending. Image, app-secret, public-route
and rollback choices remain open. Key-custody proof does not complete the production restore gate.

## Completed private cluster foundation

Source: infra `ef638ceff176915c9c3280ba032e5fda02c4b685` (pre-HM1 source baseline), branch
`feature/home-k3s-bootstrap`, now in the canonical `driftplain-infra/` directory after
workspace cleanup. The product repos are current; unrelated runtime data is preserved.

**Current administrator access:** after bootstrap, Steve enabled passwordless sudo with
`steve ALL=(ALL:ALL) NOPASSWD: ALL` in `/etc/sudoers.d/99-steve-nopasswd`.
He validated sudoers; Codex independently verified `sudo -n whoami` returns `root`
over a fresh strict-key SSH connection. Use `sudo -n` for authorized home-server work;
no password retrieval or interactive authentication is needed. The interactive commands
below describe the original bootstrap. Commit review, public cutover and AWS teardown
approval requirements remain in effect.

- Single-node K3s **v1.36.4+k3s1**, the September 12 stable channel. Matches the live
  EKS 1.36 minor; later chart/operator compatibility must be verified during deployment.
- Embedded etcd stores Kubernetes state and makes local snapshots twice daily (retain
  three). One member provides **no physical HA**. These snapshots do **not** back up PVCs.
- Bundled containerd, CoreDNS, metrics-server and local-path storage. Traefik and
  ServiceLB disabled; app ingress/ArgoCD belong to the next slice.
- Samsung root disk only; no partitioning, formatting, second-SSD use, or power changes.
- Existing host swap retained. Kubelet explicitly uses `failSwapOn: false` + `NoSwap`;
  reserve 2 GiB for the OS and 1 GiB for Kubernetes; cap container log rotation.
- UFW enabled with default deny incoming/routed, LAN SSH and scoped pod traffic/egress.
  No external API, kubelet, VXLAN, ingress or database ports are allowed. Enabling UFW
  also limits inbound desktop discovery/services; outbound desktop traffic remains allowed.
- API operations use the home-only kubeconfig. Existing Mac AWS context stays unchanged.

`bootstrap.sh` defaults to read-only `--check`. `--apply` requires interactive sudo,
rechecks host/filesystem/address and empty effective firewall, refuses existing cluster
paths, backs up UFW, installs checksummed upstream artifacts, loads network modules,
enables forwarding, excludes virtual interfaces from NetworkManager management, and
starts K3s. It never changes SSH authentication or creates sudo exemptions.
Downloads happen **unprivileged**; the root installer uses a verified root-owned copy.
Bootstrap is deliberately fresh-host-only: after installation use `verify.sh`, not a
second installation. On partial failure inspect the reported backup/state before retrying.
`finish-bootstrap.sh` completes readiness and user kubeconfig creation if installation
finished but node registration had not yet completed; it performs no install/restart.

## Run

Files are uploaded to `/home/steve/home-server-setup/k3s/`. On Ubuntu as steve:

```bash
bash /home/steve/home-server-setup/k3s/prepare.sh
bash /home/steve/home-server-setup/k3s/bootstrap.sh --check
```

Administrator step, run on the **Mac**, entering the password only in the terminal:

```bash
ssh -t steve@192.168.1.93 'sudo bash /home/steve/home-server-setup/k3s/bootstrap.sh --apply'
```

Then Codex runs the smoke as the normal SSH user:

```bash
bash /home/steve/home-server-setup/k3s/verify.sh
```

Do not overwrite a running shell script during an upload. If editing while a smoke
runs, execute a separate immutable copy and preserve its result until completion.

The smoke checks exact node/version, effective swap config and container swap limit,
CoreDNS, external DNS + HTTP egress, ClusterIP service routing, metrics and a PVC's
content after deleting/recreating its pod. It uses a digest-pinned BusyBox image,
restricted pods with resource limits, and a unique labelled namespace. Successful
tests delete only their own namespace/PVC; failures retain it for diagnosis.
This tests disposable local-path storage, not production backup/restore durability.

Home kubeconfig: `/home/steve/.kube/driftplain-home.yaml` (0600, admin credential,
context `driftplain-home`, loopback API). Do not print it or merge it into default config.
For eventual Mac access, securely copy it to a separate 0600 file and change only its
server to `https://127.0.0.1:16443`; use an SSH local forward bound to `127.0.0.1`.
Keep `--kubeconfig` and `--context` explicit. The copy needs renewal when certificates
are renewed; the root-managed source is authoritative.

## Active migration plan

[E21 / HM1–HM8](../../docs/planning/02-showcase-backlog.md) is the sole active migration
plan and decision record. It covers host recovery, off-machine backup/restore, the home
GitOps profile, sustainable external dependencies/public operation, maintained-service
wording, verified cutover and approved retirement of replaced AWS resources. P39 follows
only after E21 is accepted. AWS remains production today; no application/data cutover
was performed by this bootstrap. The service will remain online after the post.

## Inventory — September 12, 2026

- Home: Ubuntu Desktop 26.04.1, Linux 7.0.0-31, x86_64 Ryzen 5600H, 22 GiB usable RAM
  (~14 GiB available), 422 GiB root disk free; cgroup v2; `/swap.img` 8 GiB/unused.
  Ethernet eno1 `192.168.1.93/24`, DHCP via `192.168.1.1`. No existing runtimes/cluster.
  Root UUID `9ce972e8-f060-45fc-97fe-296664de6a7c`; at this baseline the Kingston
  still held its old, unmounted LUKS layout (superseded September 21 below).
- Privileged preflight: UFW **inactive**, despite its systemd unit being enabled/active;
  IPv4 filter/NAT and IPv6 filter policies ACCEPT, no rules. Modules available; no
  listener on cluster ports; IP forwarding initially 0. Original report remains on Ubuntu.
- AWS: explicit `saa` profile, account `957261948820`, `ap-south-1`, cluster `modelmatch`.
  Three Ready nodes on `v1.36.3-eks-cb19647`. All 14 ArgoCD apps Synced/Healthy;
  source revision `413b84b050d5b62b87324a9e6d9cbebc33ba1357`. FE and BE both **1.0.24**.
  `api.driftplain.dev/readyz` returns ready/db ok. App namespace is **app**, not modelmatch.
- Existing budget teardown is **armed**: `modelmatch-budget-killswitch` Active,
  `DRY_RUN=0`, with `modelmatch-platform-teardown` CodeBuild present. Budget reports
  $45.709 actual against $110; automatic teardown threshold is 90% ($99). This is an
  independent risk to the requested rollback window. Leave untouched in this slice;
  resolve with Steve before the threshold approaches, rather than promising AWS cannot
  be destroyed by the pre-existing automation.
- CNPG: two healthy instances, PostgreSQL image `16.10-system-trixie`, two 5 GiB gp3
  PVCs. Current chart uses reclaim Delete and has no backup policy. Home must override
  this explicitly; local-path capacity requests alone do not enforce disk quotas.
- Live read-only DB counts: users 9, projects 11, CI runs 209, findings 830, feedback 709,
  source documents 0, Jenkins connections 5, LLM calls 4. All five Jenkins connections
  have CI token hashes; zero have Jenkins-token refs or model-API-key refs.
  Schema `a4b5c6d7e8f9`.
  These counts are an inventory, **not a backup** or a full integrity comparison.
- Current seed flags are both **false**. The migrate PostSync hook is **unconditional**
  and its image still pins 1.0.22. Home chart must make restore/migration policy explicit
  before any sync; copying the AWS root would run that hook on the restored database.
- Actual backend `app/config.py`, `blob_store.py`, `secret_store.py` only implement
  in-memory blob/secret stores. S3/Secrets Manager adapters described in their docstrings
  are not implemented. ESO-managed app credentials are a separate, real AWS dependency.
  No source-document rows or stored Jenkins/BYOK references exist. The five CI token
  hashes must survive the restore unchanged. The in-memory adapters are still a product
  limitation for future uploads/credential writes, not missing migration payloads here.

## Current storage and boot — September 21, 2026

- Samsung `S4GLNX0T147074` remains the Ubuntu boot/root disk and holds K3s, CNPG and
  all current workloads.
- Kingston `50026B7382D6B8E9` was explicitly released for Ubuntu use. Its Omarchy
  EFI/LUKS layout was erased and replaced by one ext4 partition (UUID
  `bcce76c6-42a2-46ef-b35b-1327cbb6fbd9`), mounted from `/etc/fstab` at
  `/srv/home-server-storage` with `nofail` and a 10-second device timeout. It provides
  458 GiB usable auxiliary capacity; no application data has been moved there.
- Stale Windows, Limine and Kingston-disk UEFI entries were removed. `Boot0000 Ubuntu`
  is first, firmware timeout is zero, and GRUB is hidden with timeout zero. The remaining
  internal-disk fallback points to Ubuntu's Samsung EFI partition; USB is last.
- A coordinated reboot on September 21 changed the kernel boot ID and returned over SSH
  without a boot-menu intervention. The Kingston remounted read/write from `fstab`; K3s,
  all 14 Argo CD applications, CNPG and both staging URLs recovered healthy.
- The pre-change partition table, firmware entries and `fstab` are root-only under
  `/var/backups/home-server-storage-20260921T204600Z` on the Samsung disk, together with
  the post-reboot verification record.

## Verification record

Before installation: Bash syntax and ShellCheck 0.11.0 pass for the bootstrap/smoke scripts;
remote unprivileged checks pass; official binary and installer SHA256 match. A negative
checksum test exposed a relative-path issue with symlinked download directories; corrected
to use the explicit script-directory checksum file, then confirmed corrupted binaries fail
before host writes. Live installation exposed a systemd-ready/node-registration race;
the finish step now waits for node existence before waiting for Ready. Runtime results
are recorded separately in RESULTS.md.

## Recovery / removal

The installer saves original UFW files/rules and forwarding state in a root-only directory
under `/var/backups/driftplain-k3s.*`; its path is also saved in
`/var/backups/driftplain-k3s-last-backup`. No automatic rollback alters network state after
a partial failure. Inspect first; the files and error identify how far installation got.

To pause the empty cluster, use `sudo systemctl stop k3s` (not uninstall). Persistent files
remain, and boot enablement remains. Stopping K3s does not guarantee every container stops;
review the running state if maintenance requires all workloads quiescent.

**Do not run `k3s-uninstall.sh` after restoring data**: it deletes local cluster data,
including local-path volumes. Before any removal, verify off-machine backups and approve
the exact removal/reversal script. Restore only this slice's UFW/default files and drop-ins;
do not reset the firewall or touch the completed lid/power configuration.

## Sources and current release

- [K3s release](https://github.com/k3s-io/k3s/releases/tag/v1.36.4%2Bk3s1)
- [K3s requirements/firewall](https://docs.k3s.io/installation/requirements)
- [Kubelet drop-in configuration](https://docs.k3s.io/installation/configuration#kubelet-configuration-files)
- [Kubernetes swap behavior](https://kubernetes.io/docs/concepts/cluster-administration/swap-memory-management/)
- [Embedded etcd](https://docs.k3s.io/datastore/ha-embedded)
- Current release/HLD/backlog: umbrella `docs/showcase/p38r/README.md`,
  `docs/planning/hld.md`, `docs/planning/02-showcase-backlog.md` (canonical workspace paths).

## Workspace and planning update

The active epic is E21 in the umbrella backlog; its HM1–HM8 acceptance gates replace
the preliminary migration stages. Completing the migration includes sustainable
backups/monitoring/maintenance, accurate maintained-service wording, and verified public
operation before P39. Replaced AWS resources are retired only after approval; the application
itself is not retired. Completed power setup is documented in [POWER.md](POWER.md).

## Explicit server naming — September 13, 2026

Steve requested `home_server` identifiers and `home-server` names to avoid ambiguous `home`.
The canonical source directory is now `driftplain-infra/home-server/`; the backup
resources use `home_server_backups`, and input/output names use `home_server_backup_bucket_*`.
The applied bucket is `modelmatch-home-server-backups-957261948820`. Updated source smokes
use `home-server-smoke-*` / `home-server-recovery-*` for new disposable namespaces.

The installed node/context `driftplain-home`, `/home/steve/.kube/driftplain-home.yaml` and
`10-home.conf` remain runtime compatibility values. No host or cluster reconfiguration was
performed. Historical evidence and previously uploaded immutable scripts describe the original
namespace names and hashes. Upload a new immutable copy of the current source before a future
drill; do not present the old live-run hashes as a verification of the renamed source.
## Catalog refresh preparation (September 24, 2026)

[B16 operations](CATALOG-REFRESH.md) documents the disabled per-source scheduler,
health, report review and reversible snapshot selection. This is prepared source;
installation, production imports/migrations and scheduler enablement remain gated.
