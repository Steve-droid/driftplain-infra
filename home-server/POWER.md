# Home server preparation — September 12, 2026

Target: `steve@192.168.1.93`, hostname `home-server`, Ubuntu Desktop 26.04.1.
Hardware: Ryzen 5 5600H (6 cores / 12 threads), 24 GB installed RAM.
Ubuntu uses the Samsung 512 GB SSD. Steve confirmed Windows was intentionally removed.
On September 21 Steve directed that the Kingston 500 GB SSD also be dedicated to Ubuntu.
Its old Omarchy EFI/LUKS layout was removed and it is now one ext4 filesystem, mounted
persistently at `/srv/home-server-storage`. K3s and CNPG remain on the Samsung root disk.
A coordinated reboot verified direct Ubuntu boot and automatic remount on September 21.

SSH key login from Steve's Mac was verified. Steve subsequently enabled passwordless `sudo -n`; see [RESULTS.md](RESULTS.md).

## Power settings

Already applied over SSH to Steve's desktop account:

- `org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type = 'nothing'`
- `org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type = 'nothing'`

Original values were both `suspend`. A backup is on the laptop at
`/home/steve/home-server-setup/power-settings-before.json`.

Steve ran `configure-power.sh` successfully with sudo. It ignores lid
closure on AC/battery/docked operation, disables logind's idle action, and masks
the sleep/suspend/hibernate targets. This also blocks intentional manual sleep;
normal shutdown and reboot remain available. It reloads logind without restarting
the desktop session. Original system state is saved under
`/var/backups/modicum-power` for reversal.

The script does not install Kubernetes or applications, change partitioning,
modify SSH permissions, or alter the firewall. Keep the laptop connected to
power; this cannot keep a machine running after its battery is exhausted.

Verified on September 12, 2026: Steve closed the lid and a new key-authenticated
SSH connection succeeded. The kernel reported `state: closed`, AC power was
connected, all five sleep targets were masked, and the lid configuration matched
the intended settings. The laptop remained awake (uptime: 40 minutes).

K3s foundation is now installed and verified; see [RESULTS.md](RESULTS.md).
Application/data migration remains under E21. The historical setup below did not change AWS.

## Reversal

With administrator access, restore the prior logind drop-in if one was backed up;
otherwise remove only `/etc/systemd/logind.conf.d/90-modicum-home-server.conf`.
Consult `targets.before` before unmasking: unmask only targets that were not
already masked. Reload `systemd-logind.service`. Restore the two GNOME values
from `power-settings-before.json` in Steve's user session.
