#!/usr/bin/env python3
"""Explicit initial Secret creation on home; never renews or starts a workload."""

import argparse
import base64
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("home_server_leaf", ROOT / "home-server-leaf.py")
leaf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leaf)


def current_secret(namespace, name):
    # Only NotFound is absence. Transport/RBAC/namespace failures must stop.
    leaf.kubectl(namespace, "get", "namespace", namespace, "-o", "name")
    data = leaf.kubectl(namespace, "get", "secret", name, "--ignore-not-found", "-o", "json")
    return json.loads(data) if data.strip() else None


def check_pair(certificate, private):
    public_cert = leaf.run(["openssl", "x509", "-pubkey", "-noout"], certificate)
    public_key = leaf.run(["openssl", "pkey", "-pubout"], private)
    if public_cert != public_key:
        raise ValueError("certificate/key mismatch")


def bootstrap_leaf(operation, identity, certificate=None):
    namespace, secret, _ = leaf.HOME_SERVER_TARGETS[identity]
    directory = leaf.HOME_SERVER_ROOT / identity
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = current_secret(namespace, secret)
        current_cert = None
        if current:
            current_cert = base64.b64decode(current["data"]["tls.crt"], validate=True)
            check_pair(current_cert, base64.b64decode(current["data"]["tls.key"], validate=True))
        if operation == "status":
            return {"certificate": current_cert.decode() if current_cert else None}
        key_path, csr_path = directory / "pending.key", directory / "pending.csr"
        if operation == "prepare":
            if current:
                raise ValueError("bootstrap refuses an existing Secret")
            if not key_path.exists():
                if csr_path.exists():
                    raise ValueError("orphan CSR requires reconciliation")
                key = leaf.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072"])
                with key_path.open("xb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(key)
                    stream.flush()
                    os.fsync(stream.fileno())
            # Derive a fresh public CSR from the retained key after interrupted writes.
            csr = leaf.run(["openssl", "req", "-new", "-sha256", "-key", str(key_path),
                            "-subj", "/CN=driftplain-home-server-" + identity])
            csr_path.write_bytes(csr)
            return {"csr": csr.decode()}
        if operation != "install" or not certificate:
            raise ValueError("invalid bootstrap operation")
        if current and current_cert != certificate:
            raise ValueError("bootstrap never replaces an existing Secret")
        if not current:
            candidate = directory / "candidate.crt"
            candidate.write_bytes(certificate)
            leaf.run(["openssl", "verify", "-CAfile", str(leaf.HOME_SERVER_ROOT / "issuer.crt"), str(candidate)])
            subject = leaf.run(["openssl", "x509", "-in", str(candidate), "-noout", "-subject", "-nameopt", "RFC2253"])
            if subject.decode().strip().replace("subject= ", "subject=") != "subject=CN=driftplain-home-server-" + identity:
                raise ValueError("wrong certificate subject")
            private = key_path.read_bytes()
            check_pair(certificate, private)
            payload = {"apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/tls",
                       "metadata": {"name": secret, "namespace": namespace},
                       "data": {"tls.crt": base64.b64encode(certificate).decode(),
                                "tls.key": base64.b64encode(private).decode()}}
            # Create is atomic and fails on a concurrent creator. Never apply/replace.
            leaf.kubectl(namespace, "create", "-f", "-", payload=json.dumps(payload).encode())
        observed = current_secret(namespace, secret)
        if not observed or base64.b64decode(observed["data"]["tls.crt"], validate=True) != certificate:
            raise ValueError("bootstrap readback mismatch")
        check_pair(certificate, base64.b64decode(observed["data"]["tls.key"], validate=True))
        for path in (key_path, csr_path, directory / "candidate.crt"):
            path.unlink(missing_ok=True)
        return {"installed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("status", "prepare", "install"))
    parser.add_argument("identity", choices=leaf.HOME_SERVER_TARGETS)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("requires sudo -n")
    os.umask(0o077)
    try:
        certificate = sys.stdin.buffer.read(16384) if args.operation == "install" else None
        print(json.dumps(bootstrap_leaf(args.operation, args.identity, certificate)))
    except Exception:
        print("home-server bootstrap failed; private diagnostics suppressed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
