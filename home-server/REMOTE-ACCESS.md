# Home-server remote administrator access

Configured September 14, 2026. Tailscale is installed on the Ubuntu home server
from its official stable apt repository; `tailscaled` is enabled at boot.
Steve authorized both the server and Mac in his personal tailnet.

## Connect from the Mac

Keep Tailscale connected and run `ssh home-server`. The local Mac SSH config
maps this alias to `steve@100.78.82.22`, uses `~/.ssh/id_ed25519`, and checks
the existing trusted LAN host identity with `HostKeyAlias 192.168.1.93` and
`StrictHostKeyChecking yes`. The previous SSH config has a timestamped backup
beside it. LAN SSH remains available.

The Mac's Tailscale address is `100.126.210.90`. These Tailscale addresses remain
stable across Wi-Fi/hotspot changes while the devices remain registered.
The server must remain powered and online. Device removal/re-registration or
administrator address changes can require updating the rule and SSH alias.

## Access controls

Ordinary OpenSSH handles authentication; Tailscale SSH is disabled. The server
does not accept Tailscale DNS settings or advertised routes and does not act as
an exit node or subnet router.

UFW owns the host filtering. Tailscale's `netfilter-mode=off` prevents its
automatic interface-wide accepts from bypassing the existing UFW rules.
This mode requires maintaining the transport and interface rules ourselves:

```bash
sudo ufw allow in on tailscale0 from 100.126.210.90 to 100.78.82.22 port 22 proto tcp
sudo ufw allow in on eno1 to any port 41641 proto udp
```

The latter permits encrypted Tailscale transport (IPv4 and IPv6). No router
port forwarding was configured. Existing default-deny incoming/routed rules
remain. Only the Mac's IPv4 address has an SSH allow rule on `tailscale0`;
the SSH alias deliberately uses IPv4. This restriction is enforced on the host,
not by a custom tailnet access policy. Do not change netfilter mode to `on`
without reviewing access controls, because Tailscale can then bypass UFW.

## Verification and remaining check

- SSH through Tailscale passed: source `100.126.210.90`, destination `100.78.82.22:22`.
- Strict host-key verification and `sudo -n whoami` passed (`root`).
- K3s node `driftplain-home` remained Ready.
- Mac TCP checks: 22 reachable; 6443, 10250, 2379 and 2380 unreachable through
  the server's Tailscale IPv4 address.
- Both device keys currently expire March 13, 2027, beyond this trip. Plan
  reauthentication before expiry for ongoing access.
- A real mobile-hotspot test is still required before departure.

Scripts that explicitly use `192.168.1.93`, including the renewal transport in
`home-server-renew.py`, do not automatically use this SSH alias. Their transport
must be separately updated and validated if needed while away.

To temporarily block Tailscale inbound access while retaining LAN recovery:
`sudo tailscale set --shields-up=true`. Restore only after verifying the UFW
rules, with `sudo tailscale set --shields-up=false`.

References: [Tailscale netfilter modes](https://tailscale.com/docs/reference/netfilter-modes)
and [stable device addresses](https://tailscale.com/docs/concepts/ip-and-dns-addresses).
