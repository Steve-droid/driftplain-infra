# HM2 host recovery drill

September 12, 2026. This drill uses the empty private home cluster and disposable data.
It does not prove PostgreSQL backup recovery or survival of a failed SSD.

## Witness and boundaries

`recovery-check.py` defaults to read-only `status`. Run it as steve on home-server.
It checks the Samsung root UUID, explicit loopback home kubeconfig, single node identity
and kubeconfig ownership/mode. `prepare` refuses an existing witness or application namespaces.
It creates one restricted, digest-pinned BusyBox Deployment, Service and 128 MiB local-path
PVC in a unique labelled namespace. A random marker is written and synced exactly once.
Container startup only serves the file; it cannot silently replace a lost marker.

State is mode 0600 at `/home/steve/home-server-setup/recovery-witness.json`. It records boot,
systemd invocation, node/namespace/PVC/PV UIDs and marker hash. It contains no production data
or credentials. Checks require the original objects/content, Ready node, boot-enabled active
K3s, system deployments, Service routing, cluster/external DNS and pod HTTP egress.
`check-service` additionally requires a changed K3s invocation within the original boot;
`check-reboot` requires a different kernel boot ID. Neither initiates disruption.

Use an immutable uploaded script, currently
`/home/steve/home-server-setup/recovery-check-hm2-v1.py`; never replace an executing script.
The source and uploaded copy must match before a drill. Commands below run **on home-server**:

```bash
python3 /home/steve/home-server-setup/recovery-check-hm2-v1.py prepare
python3 /home/steve/home-server-setup/recovery-check-hm2-v1.py status
```

## Interrupted K3s service

The live unit has `Restart=always`, `RestartUSec=5s`, `KillMode=process`. On the empty HM2
cluster only, the authorized drill kills just the service's main process; systemd must
restart it without a manual start. This does not interrupt Desktop or SSH. Once API readiness
returns, use `check-service`. Containers may continue running while K3s restarts; that is not
proof of recovery from a host reboot.

```bash
sudo -n systemctl kill --kill-whom=main --signal=SIGKILL k3s
python3 /home/steve/home-server-setup/recovery-check-hm2-v1.py check-service
```

## Coordinated reboot

Before reboot, save Desktop work, confirm AC power, retain the witness, note the current time
and verify strict-key SSH plus `status`. Steve must approve the reboot window because Ubuntu
Desktop has active sessions. The exact action is `sudo -n systemctl reboot` on home-server.
No AWS service or public route changes. SSH will disconnect; allow up to five minutes for
fresh SSH/API access, then run `check-reboot`. Measure elapsed time from request to successful
check separately from the script's verification duration.

After fresh SSH, check UFW remains active, secret encryption hashes match, NoSwap remains
effective, sleep targets remain masked, AC/lid settings survive, and management TCP ports
6443/10250/2379/2380 remain unreachable from the Mac. Confirm the public AWS API still works.
Reboot success does not prove recovery after complete battery discharge or router failure.

If SSH does not return, inspect the laptop locally before changing network/security config.
Check Ethernet/link and DHCP lease on the router. Do not bypass host-key checking. The K3s
`node-ip` is pinned to `.93`; another DHCP address requires restoring the reservation or a
reviewed configuration change, not blindly substituting an address in commands. If SSH works
but Kubernetes does not, inspect `sudo -n systemctl status k3s` and bounded journal output
locally (avoid copying credentials into shared evidence). Do not rerun bootstrap or uninstall.

## LAN reservation and router recovery

Router: `192.168.1.1`; interface `eno1`; permanent MAC `A8:B1:3B:73:F8:7A`;
desired reservation `192.168.1.93`. Open the router while on the LAN, then find LAN/DHCP
Address Reservation / Static Lease / DHCP Binding. Use the router's private admin login.
Do not enter credentials into Git or chat. Confirm `.93` belongs only to this MAC, reserve it,
and record the router model and confirmation. Keep host DHCP enabled.

Reservation is **saved and router power-cycle recovery verified on September 12, 2026**;
see [RESULTS.md](https://github.com/Steve-droid/driftplain-infra/blob/0d428cb5a8951272723ef79a4680b59a243aac65/home-server/RESULTS.md). Do not restart the whole router merely to apply changes without a
household outage window. After an approved router restart, verify the reservation remains,
the host regains `.93`, fresh SSH works, and pod DNS/egress return. WAN/CGNAT/public IPv4
status is unknown until router WAN details are inspected. The host also has global IPv6;
keep UFW inbound policy and do not introduce an IPv6 bypass.

## Cleanup

Keep the witness until service and reboot evidence are copied into the results record.
`cleanup` verifies namespace UID and purpose before deleting **only that disposable namespace**;
waits for its PV deletion and archives the local JSON evidence. It never deletes application
namespaces, host directories or AWS resources. A failed check retains evidence for diagnosis.

```bash
python3 /home/steve/home-server-setup/recovery-check-hm2-v1.py cleanup
```

After HM3 deploys real data, this empty-cluster preparation deliberately refuses to run.
Future maintenance checks need a reviewed application-aware procedure and current backups.
