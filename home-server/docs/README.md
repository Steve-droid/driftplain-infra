# Home-server operations

Driftplain runs on the home K3s cluster and serves `driftplain.dev` and
`api.driftplain.dev` through Cloudflare Tunnel. AWS compute was retired on
September 21, 2026. Use these runbooks for the maintained service; inspect live
state before an operational change.

## Runbooks

| Task | Read |
|---|---|
| Routine checks, monitors and scheduled jobs | [Operations](HM5-OPERATIONS.md) |
| Upgrades, certificate deadlines and Mac runtime installation | [Maintenance](HM5-MAINTENANCE.md) |
| Lost host or Mac | [Lost-host recovery](HM5-LOST-HOST-RECOVERY.md) |
| SSH and Tailscale | [Remote access](REMOTE-ACCESS.md) |
| Power settings | [Power](POWER.md) |
| Coordinated reboot and recovery checks | [Recovery drill](RECOVERY.md) |
| Database backups and restore | [S3 backups](S3-BACKUPS.md), [restore procedure](HM3-RESTORE.md) |
| Application bootstrap and validation | [Home application](HM4-HOME-APP.md) |
| AWS workload identity and initial leaf installation | [Identity](IDENTITY.md), [identity bootstrap](IDENTITY-BOOTSTRAP.md) |
| Certificate issuer, renewal, revocation and custody | [Issuer](ISSUER.md), [recovery key](RECOVERY-KEY.md) |
| Retained services and costs | [AWS retirement](AWS-COMPUTE-RETIREMENT.md), [retained services](HM8-RETAINED-SERVICES.md), [costs](HOME-SERVER-COSTS.md) |
| Runtime routing and cutover decisions | [Cutover record](HM7-CUTOVER.md) |
| Prepared, disabled catalog refresh | [Catalog refresh](CATALOG-REFRESH.md) |

Some runbooks include dated implementation results. Those results are historical
evidence, not fresh health checks or authorization to repeat a migration. Current
operational scope and the operations runbook take precedence over old pending gates.

## Source layout

All Markdown lives here in `home-server/docs/`. Executable tools and their runtime
dependencies stay in the parent `home-server/` directory so installation paths and
imports remain stable:

- Backup, database, certificate, custody, maintenance and validation Python tools.
- Host bootstrap, power, ArgoCD and recovery scripts, K3s configuration and checksums.
- Public certificates, issuer fingerprint and recovery encryption recipient.
- Example configurations and three Mac LaunchAgent templates.
- [Identity Terraform](../identity/) and the [backup image Dockerfile](../backup-image/Dockerfile).

Commands in these runbooks keep their stated working directory. A path beginning
`home-server/` is relative to the infrastructure repository root, not this docs directory.
The Mac's installed tools and private state live under
`~/.local/share/driftplain/`; changing this checkout does not update scheduled jobs.
See the [runtime installation procedure](HM5-MAINTENANCE.md#mac-operator-runtime).

## Run

These steps are for a separately authorized **fresh host rebuild**. For the existing
cluster, use the operations runbook. The bootstrap installer refuses an existing cluster.

Upload the bootstrap scripts, `k3s.yaml`, `kubelet.conf` and `SHA256SUMS` to
`/home/steve/home-server-setup/k3s/` on Ubuntu. Preserve the files together.
Run as `steve` on Ubuntu:

```bash
bash /home/steve/home-server-setup/k3s/prepare.sh
bash /home/steve/home-server-setup/k3s/bootstrap.sh --check
```

After reviewing the preflight, the authorized installation from the Mac is:

```bash
ssh home-server 'sudo -n bash /home/steve/home-server-setup/k3s/bootstrap.sh --apply'
```

Then run on Ubuntu:

```bash
bash /home/steve/home-server-setup/k3s/verify.sh
```

`prepare.sh` downloads pinned K3s artifacts and checks their hashes. `bootstrap.sh`
backs up firewall state before installing; inspect its reported backup on failure.
`finish-bootstrap.sh` completes readiness and user kubeconfig creation after a partial
installation. Never overwrite an executing script.

`verify.sh` exercises DNS, egress, service routing, metrics and persistent storage in
a disposable namespace; it creates resources and is an operator check, not a unit test.
The recovery witness and application validator are also retained for authorized
operational verification.

## Storage and access

Use `ssh home-server` over Tailscale with strict host-key verification. The Samsung
SSD holds Ubuntu, K3s and application volumes. The Kingston SSD is a separate ext4
filesystem at `/srv/home-server-storage`, UUID `bcce76c6-42a2-46ef-b35b-1327cbb6fbd9`,
configured with `nofail` and a 10-second boot timeout. It is not a backup or redundancy.
Check the actual mount and capacity before use; do not rely on NVMe device numbering.

The September 21, 2026 boot/storage change was verified after a coordinated reboot.
Its root-only recovery files remain under
`/var/backups/home-server-storage-20260921T204600Z` on the server.

## Cleanup and historical material

On October 5, 2026, local cleanup removed the home-server Python/Terraform test
files, completed acceptance helpers, the old privileged inventory helper, the temporary
tunnel witness, superseded planning documents and dated JSON evidence snapshots.
The retained Python tools, Terraform configuration, runtime templates and public
trust material were preserved. No installed Mac job or live server was modified.

Historical documents and evidence remain available in the
[pre-cleanup Git revision](https://github.com/Steve-droid/driftplain-infra/blob/0d428cb5a8951272723ef79a4680b59a243aac65/home-server/).
Links to removed records use that immutable revision. Tests are no longer part of
this directory; restore the relevant historical suite in an isolated checkout when
future behavioral changes require regression coverage. Do not treat syntax checks
as equivalent to those tests.

## Renewal transport fix — October 5, 2026

The renewer now uses the configured `home-server` SSH alias instead of bypassing
SSH configuration and connecting directly to the LAN address. This keeps strict
host-key verification and allows checks over Tailscale while away from home.
The source and both installed Mac copies were updated and verified identical.
The manual check returned `status: ok` at 13:49:56 UTC on October 5, 2026;
no certificates were due for renewal. Keep Tailscale connected for scheduled checks.
