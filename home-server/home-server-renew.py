#!/usr/bin/env python3
"""Mac renewal coordinator. Disabled until reviewed issuer/host enrollment.

Private CA bytes remain in Keychain/process memory or age ciphertext. Ordinary
renewal performs no IAM/CRL administration and never invokes Bedrock.
"""

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("home_server_custody", ROOT / "recovery-key.py")
custody = importlib.util.module_from_spec(spec)
spec.loader.exec_module(custody)


def now():
    return datetime.now(timezone.utc)


def write_json(path, value):
    issuer_tools.save_ledger(path, value)


issuer_tools_spec = importlib.util.spec_from_file_location("home_server_issuer", ROOT / "home-server-issuer.py")
issuer_tools = importlib.util.module_from_spec(issuer_tools_spec)
issuer_tools_spec.loader.exec_module(issuer_tools)


def read_issuer(config):
    return issuer_tools.read_issuer(config)


def validate_leaf(certificate, identity, issuer):
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "driftplain-home-server-" + identity)])
    if certificate.subject != subject:
        raise ValueError("unexpected leaf subject")
    certificate.verify_directly_issued_by(issuer)
    if certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise ValueError("workload cannot be a CA")
    if not certificate.extensions.get_extension_for_class(x509.KeyUsage).value.digital_signature:
        raise ValueError("workload cannot sign requests")


def issue(csr_pem, identity, key, issuer, instant):
    if identity not in ("backup", "bedrock"):
        raise ValueError("unexpected workload identity")
    issuer_tools.validate_ca(key, issuer, issuer.fingerprint(hashes.SHA256()).hex(), instant)
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "driftplain-home-server-" + identity)])
    if not csr.is_signature_valid or csr.subject != subject:
        raise ValueError("CSR does not assert the selected identity")
    public = csr.public_key()
    if not isinstance(public, rsa.RSAPublicKey) or public.key_size != 3072 or public.public_numbers().e != 65537:
        raise ValueError("unsupported workload key")
    if issuer.not_valid_before_utc > instant or issuer.not_valid_after_utc <= instant + timedelta(days=90):
        raise ValueError("issuer renewal required before leaf issuance")
    constraints = issuer.extensions.get_extension_for_class(x509.BasicConstraints).value
    usage = issuer.extensions.get_extension_for_class(x509.KeyUsage).value
    if not constraints.ca or not usage.key_cert_sign:
        raise ValueError("invalid issuer constraints")
    # Never copy requested CSR extensions (including CA privileges).
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer.subject)
                   .public_key(public).serial_number(x509.random_serial_number())
                   .not_valid_before(instant - timedelta(minutes=5)).not_valid_after(instant + timedelta(days=90))
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                   .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True)
                   .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer.public_key()), critical=False)
                   .sign(key, hashes.SHA256()))
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


def renew_identity(identity, ledger, save, remote, backup, key, issuer, instant):
    """Journal before publication/delivery; retry the same certificate after failure."""
    current_pem = remote("status", identity)["certificate"]
    current = x509.load_pem_x509_certificate(current_pem.encode())
    validate_leaf(current, identity, issuer)
    if not any(entry["identity"] == identity and entry["certificate"] == current_pem for entry in ledger["issued"]):
        raise ValueError("current certificate absent from issuer history; reconcile before renewal")
    revoked = {entry["serial"] for entry in ledger["revoked"]}
    if str(current.serial_number) in revoked:
        raise ValueError("current leaf revoked; incident recovery required")
    pending = ledger["pending"].get(identity)
    if pending and str(x509.load_pem_x509_certificate(pending["certificate"].encode()).serial_number) in revoked:
        raise ValueError("pending leaf revoked; refuse delivery")
    if pending is None and current.not_valid_after_utc - instant > timedelta(days=30):
        return "not-due"
    if pending is None:
        csr = remote("prepare", identity)["csr"]
        pending = {"certificate": issue(csr, identity, key, issuer, instant),
                   "previous_serial": str(current.serial_number), "created_at": instant.isoformat()}
        new_serial = x509.load_pem_x509_certificate(pending["certificate"].encode()).serial_number
        if any(x509.load_pem_x509_certificate(entry["certificate"].encode()).serial_number == new_serial
               for entry in ledger["issued"]):
            raise ValueError("duplicate issuance serial")
        ledger["pending"][identity] = pending
        ledger["issued"].append({"identity": identity, **pending})
        save(ledger)  # never generate another serial on a retry after this point
    certificate = x509.load_pem_x509_certificate(pending["certificate"].encode())
    validate_leaf(certificate, identity, issuer)
    if current_pem != pending["certificate"] and str(current.serial_number) != pending["previous_serial"]:
        raise ValueError("current leaf differs from renewal journal; refuse stale replacement")
    if certificate.not_valid_after_utc <= instant + timedelta(days=1):
        raise ValueError("stale pending renewal requires reconciliation")
    backup(ledger)  # recoverability before deployment; failure leaves current key intact
    remote("install", identity, pending["certificate"])
    if remote("status", identity)["certificate"] != pending["certificate"]:
        raise ValueError("installed certificate not observed")
    del ledger["pending"][identity]
    ledger["backup_required"] = True
    save(ledger)
    backup(ledger)  # latest ledger with delivery completion recorded
    ledger["backup_required"] = False
    save(ledger)
    return "renewed"


class HomeServerOperations:
    def __init__(self, config, key, issuer):
        self.config, self.key, self.issuer = config, key, issuer

    def remote(self, operation, identity, certificate=None):
        if operation not in ("status", "prepare", "install") or identity not in ("backup", "bedrock"):
            raise ValueError("unexpected remote operation")
        command = ["/usr/bin/ssh", "-o", "StrictHostKeyChecking=yes",
                   "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=5",
                   "-i", self.config["ssh_key"], "home-server",
                   "sudo -n /usr/bin/python3 /opt/home-server/home-server-leaf.py " + operation + " " + identity]
        result = subprocess.run(command, input=certificate.encode() if certificate else None,
                                capture_output=True, timeout=240)
        if result.returncode:
            raise RuntimeError("home-server transport failed")
        return json.loads(result.stdout)

    def backup(self, ledger):
        issuer_tools.validate_ledger(ledger, self.issuer)
        # Public ledger is local; the combined private bundle exists only in memory
        # and age ciphertext. Never use SecretString/PEM process arguments.
        private = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()).decode()
        bundle = issuer_tools.canonical({"version": 2, "issuer_private_key": private,
                             "issuer_certificate": self.issuer.public_bytes(serialization.Encoding.PEM).decode(),
                             "ledger": ledger, "recovery_recipient": self.config["recovery_recipient"]})
        digest = hashlib.sha256(bundle).hexdigest()
        folder = Path(self.config["encrypted_backup_directory"])
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        archive = folder / (digest + ".age")
        if not archive.exists():
            ciphertext = custody.run([self.config["age_binary"], "--encrypt", "-r", self.config["recovery_recipient"]], bundle)
            pending_archive = archive.with_suffix(".age.pending")
            with pending_archive.open("wb") as stream:
                stream.write(ciphertext)
                stream.flush()
                os.fsync(stream.fileno())
            pending_archive.replace(archive)
        # AWS access stays on the operator's Mac. Ordinary renewal touches only S3.
        import boto3
        session = boto3.Session(profile_name=self.config["aws_profile"], region_name="ap-south-1")
        if session.client("sts").get_caller_identity()["Arn"] != "arn:aws:iam::957261948820:user/steve":
            raise ValueError("unexpected operator")
        s3 = session.client("s3")
        object_key = "recovery/issuer-v1/" + archive.name
        receipts = folder / (digest + ".receipt.json")
        if receipts.exists():
            receipt = json.loads(receipts.read_text())
            if receipt["object_key"] != object_key or receipt["ledger_sha256"] != issuer_tools.sha(issuer_tools.canonical(ledger)):
                raise ValueError("issuer receipt differs from journal")
            observed = issuer_tools.fetch_bundle(s3, receipt, {
                **self.config, "ca_sha256": self.issuer.fingerprint(hashes.SHA256()).hex()})
            if observed != archive.read_bytes():
                raise ValueError("issuer backup verification failed")
            return receipt
        result = s3.put_object(Bucket=self.config["backup_bucket"], Key=object_key,
                               Body=archive.read_bytes(), ServerSideEncryption="AES256")
        version = result["VersionId"]
        if not version or version == "null":
            raise ValueError("issuer backup needs a versioned bucket")
        observed = s3.get_object(Bucket=self.config["backup_bucket"], Key=object_key, VersionId=version)["Body"].read()
        if observed != archive.read_bytes():
            raise ValueError("issuer backup verification failed")
        receipt = {"version_id": version, "ciphertext_sha256": hashlib.sha256(observed).hexdigest(),
                   "object_key": object_key, "bucket": self.config["backup_bucket"],
                   "ca_sha256": self.issuer.fingerprint(hashes.SHA256()).hex(),
                   "ledger_sha256": issuer_tools.sha(issuer_tools.canonical(ledger)),
                   "recovery_key_id": issuer_tools.RECOVERY_SERVICE,
                   "recovery_recipient": self.config["recovery_recipient"],
                   "verified_at": now().isoformat()}
        write_json(receipts, receipt)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(args.config.read_text())
    if config.get("enabled") is not True:
        print("home-server renewal disabled; enrollment review required")
        return 0
    state = Path(config["ledger"])
    try:
        if config["backup_bucket"] != "modelmatch-home-server-backups-957261948820":
            raise ValueError("unexpected issuer recovery destination")
        if config["recovery_recipient"] != (ROOT / "recovery-key-v1.recipient").read_text().strip():
            raise ValueError("unexpected issuer recovery recipient")
        with state.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            ledger = json.loads(state.read_text())  # never silently recreate a missing ledger
            key, issuer = read_issuer(config)
            issuer_tools.validate_ledger(ledger, issuer)
            if not ledger["pending"] and not ledger.get("backup_required") and ledger.get("last_checked_at"):
                if now() - datetime.fromisoformat(ledger["last_checked_at"]) < timedelta(hours=20):
                    return 0
            if issuer.not_valid_after_utc - now() <= timedelta(days=180):
                # CA rotation is a different trust-anchor change and must be reviewed.
                subprocess.run(["/usr/bin/osascript", "-e",
                                'display notification "CA replacement is due for review within 180 days." with title "Driftplain home-server"'],
                               capture_output=True, timeout=10, check=False)
            operations = HomeServerOperations(config, key, issuer)
            if ledger.get("backup_required"):
                operations.backup(ledger)
                ledger["backup_required"] = False
                write_json(state, ledger)
            for identity in ("backup", "bedrock"):
                renew_identity(identity, ledger, lambda value: write_json(state, value),
                               operations.remote, operations.backup, key, issuer, now())
            ledger["last_checked_at"] = now().isoformat()
            write_json(state, ledger)
            write_json(state.with_suffix(".status.json"), {"status": "ok", "checked_at": now().isoformat()})
    except BlockingIOError:
        return 0  # another renewal owns the journal
    except Exception:
        # Fixed message: notification/logs never include private subprocess output.
        message = "home-server certificate renewal failed; inspect renewal status and certificate expiry"
        print(message, file=sys.stderr)
        write_json(state.with_suffix(".status.json"), {"status": "failed", "checked_at": now().isoformat()})
        subprocess.run(["/usr/bin/osascript", "-e",
                        'display notification "Automatic certificate renewal failed. Check Driftplain identity status." with title "Driftplain home-server"'],
                       capture_output=True, timeout=10, check=False)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
