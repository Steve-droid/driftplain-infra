#!/usr/bin/env python3
"""Journal and back up disposable HM2 test leaves, never replace workload Secrets."""

import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sys

from cryptography import x509
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("home_server_renew", ROOT / "home-server-renew.py")
renew = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renew)
issuer_tools = renew.issuer_tools
PROBES = ("crl-bootstrap", "revocation")


def issue_probe(probe, csr_pem, ledger, save, backup, key, issuer, instant):
    if probe not in PROBES:
        raise ValueError("unexpected acceptance probe")
    issuer_tools.validate_ledger(ledger, issuer)
    if ledger["pending"]:
        raise ValueError("reconcile pending workload deliveries first")
    existing = [entry for entry in ledger["issued"] if entry.get("acceptance_probe") == probe]
    if len(existing) > 1:
        raise ValueError("duplicate acceptance history")
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    if not csr.is_signature_valid:
        raise ValueError("invalid CSR signature")
    if existing:
        certificate = existing[0]["certificate"]
        cert = x509.load_pem_x509_certificate(certificate.encode())
        fmt = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if cert.subject != csr.subject or cert.public_key().public_bytes(*fmt) != csr.public_key().public_bytes(*fmt):
            raise ValueError("probe CSR differs from retained history")
    else:
        certificate = renew.issue(csr_pem, "backup", key, issuer, instant)
        ledger["issued"].append({"identity": "backup", "certificate": certificate,
                                 "previous_serial": None, "created_at": instant.isoformat(),
                                 "purpose": "disposable-HM2-acceptance", "acceptance_probe": probe})
        ledger["backup_required"] = True
        issuer_tools.validate_ledger(ledger, issuer)
        save(ledger)
    receipt = backup(ledger)
    return certificate, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--probe", required=True, choices=PROBES)
    parser.add_argument("--csr", required=True, type=Path)
    parser.add_argument("--certificate-output", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        config = json.loads(args.config.read_text())
        if (config.get("enabled") is not False or config["backup_bucket"] != issuer_tools.BUCKET
                or config["recovery_recipient"] != (ROOT / "recovery-key-v1.recipient").read_text().strip()):
            raise ValueError("unexpected config/recovery destination")
        state = Path(config["ledger"])
        with state.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            ledger = json.loads(state.read_text())
            key, issuer = renew.read_issuer(config)
            operations = renew.HomeServerOperations(config, key, issuer)
            certificate, receipt = issue_probe(args.probe, args.csr.read_text(), ledger,
                                               lambda value: issuer_tools.save_ledger(state, value),
                                               operations.backup, key, issuer, issuer_tools.now())
            issuer_tools.ensure_public(args.certificate_output, certificate.encode())
            cert = x509.load_pem_x509_certificate(certificate.encode())
            print(json.dumps({"probe": args.probe, "serial": str(cert.serial_number),
                              "receipt": receipt}, indent=2))
    except Exception:
        print("Acceptance issuance failed; preserve ledger; private diagnostics suppressed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
