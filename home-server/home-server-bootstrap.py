#!/usr/bin/env python3
"""Explicit Mac leaf bootstrap; separate approval from renewal/CRL/authentication."""

import argparse
from datetime import timedelta
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from cryptography import x509

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("home_server_renew", ROOT / "home-server-renew.py")
renew = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renew)
issuer_tools = renew.issuer_tools


def bootstrap_identity(identity, ledger, save, remote, backup, key, issuer, instant):
    if identity not in ("backup", "bedrock"):
        raise ValueError("unexpected bootstrap identity")
    issuer_tools.validate_ledger(ledger, issuer)
    current = remote("status", identity)["certificate"]
    history = [entry for entry in ledger["issued"] if entry["identity"] == identity]
    pending = ledger["pending"].get(identity)
    if history:
        if len(history) != 1 or history[0].get("purpose") != "initial-bootstrap" or history[0]["previous_serial"] is not None:
            raise ValueError("existing history requires renewal or reconciliation")
        entry = history[0]
        certificate = entry["certificate"]
        if pending is None and current != certificate:
            raise ValueError("completed bootstrap differs from home state")
        if pending and {"identity": identity, **pending} != entry:
            raise ValueError("bootstrap journal differs from issued history")
    else:
        if current or pending:
            raise ValueError("untracked home identity; do not replace or invent history")
        csr = remote("prepare", identity)["csr"]
        certificate = renew.issue(csr, identity, key, issuer, instant)
        pending = {"certificate": certificate, "previous_serial": None,
                   "created_at": instant.isoformat(), "purpose": "initial-bootstrap"}
        if any(item["certificate"] == certificate for item in ledger["issued"]):
            raise ValueError("duplicate bootstrap certificate")
        ledger["issued"].append({"identity": identity, **pending})
        ledger["pending"][identity] = pending
        # Validate serial uniqueness and the complete history before saving/delivery.
        issuer_tools.validate_ledger(ledger, issuer)
        save(ledger)
    cert = x509.load_pem_x509_certificate(certificate.encode())
    renew.validate_leaf(cert, identity, issuer)
    if (str(cert.serial_number) in {item["serial"] for item in ledger["revoked"]}
            or cert.not_valid_before_utc > instant
            or cert.not_valid_after_utc <= instant + timedelta(days=1)):
        raise ValueError("revoked or stale bootstrap certificate")
    if current and current != certificate:
        raise ValueError("home identity conflicts with bootstrap journal")
    backup(ledger)  # S3 exact-version verification before creating the Secret.
    remote("install", identity, certificate)  # idempotent cleanup after a lost response
    if remote("status", identity)["certificate"] != certificate:
        raise ValueError("bootstrap not observed")
    if pending:
        del ledger["pending"][identity]
        ledger["backup_required"] = True
        save(ledger)
    # Leave the conservative flag true: this receipt matches the exact final ledger.
    # The renewer already handles the flag. Do not mutate history after its backup.
    receipt = backup(ledger)
    return {"identity": identity, "serial": str(cert.serial_number),
            "not_after": cert.not_valid_after_utc.isoformat(), "receipt": receipt}


class HomeServerBootstrapOperations(renew.HomeServerOperations):
    def remote(self, operation, identity, certificate=None):
        if operation not in ("status", "prepare", "install") or identity not in ("backup", "bedrock"):
            raise ValueError("unexpected bootstrap operation")
        # Use the already-published alias/host-key binding. Renewal transport is unchanged.
        command = ["/usr/bin/ssh", "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes",
                   "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=5", "-i", self.config["ssh_key"],
                   "home-server", "sudo -n /usr/bin/python3 /opt/home-server/home-server-bootstrap-leaf.py "
                   + operation + " " + identity]
        result = subprocess.run(command, input=certificate.encode() if certificate else None,
                                capture_output=True, timeout=240)
        if result.returncode:
            raise RuntimeError("home-server bootstrap transport failed")
        return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--identity", choices=("backup", "bedrock"), required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        config = json.loads(args.config.read_text())
        if (config.get("enabled") is not False or config["backup_bucket"] != issuer_tools.BUCKET
                or config["recovery_recipient"] != (ROOT / "recovery-key-v1.recipient").read_text().strip()):
            raise ValueError("bootstrap requires disabled renewal and the enrolled recovery destination")
        state = Path(config["ledger"])
        with state.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            ledger = json.loads(state.read_text())
            key, issuer = renew.read_issuer(config)
            operations = HomeServerBootstrapOperations(config, key, issuer)
            result = bootstrap_identity(args.identity, ledger, lambda value: issuer_tools.save_ledger(state, value),
                                        operations.remote, operations.backup, key, issuer, renew.now())
            print(json.dumps(result, indent=2))
    except Exception:
        print("home-server bootstrap failed; preserve custody and journal; private diagnostics suppressed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
