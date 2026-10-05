#!/usr/bin/env python3
"""Operator issuer enrollment, recovery and local CRL preparation. No IAM writes.

Private key material is confined to native Keychain APIs, memory and age pipes.
All commands require explicit invocation; this file installs no scheduler.
"""

import argparse
import base64
import ctypes
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


custody = load("home_server_custody", "recovery-key.py")
ISSUER_SERVICE = "modelmatch/home-server/issuer-v1"
RECOVERY_SERVICE = "modelmatch/home-server/recovery-key-v1"
OPERATOR = "arn:aws:iam::957261948820:user/steve"
BUCKET = "modelmatch-home-server-backups-957261948820"
SIGNING = {"issuer_cn": "driftplain-home-server-issuer-v1", "rsa_bits": 3072,
           "hash": "SHA256", "ca_days": 730, "leaf_days": 90,
           "renew_before_days": 30, "crl_days": 35}


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def pem(certificate):
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


def private_pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def private_key(value):
    key = serialization.load_pem_private_key(value, password=None)
    if (not isinstance(key, rsa.RSAPrivateKey) or key.key_size != 3072
            or key.public_key().public_numbers().e != 65537 or private_pem(key) != value):
        raise ValueError("expected one canonical RSA-3072 PKCS8 issuer key")
    return key


class IssuerKeychain(custody.LocalKeychain):
    """Separate PEM validator; never use the age-specific get/add methods."""

    def __init__(self, path):
        super().__init__(Path(path), ISSUER_SERVICE, "steve")

    def get(self):
        length, content = ctypes.c_uint32(), ctypes.c_void_p()
        code = self.api.SecKeychainFindGenericPassword(
            self.ref, len(self.service), self.service, len(self.account), self.account,
            ctypes.byref(length), ctypes.byref(content), None)
        if code == -25300:
            return None
        self.check(code)
        try:
            value = ctypes.string_at(content, length.value)
            private_key(value)
            return value
        finally:
            self.api.SecKeychainItemFreeContent(None, content)

    def add(self, value):
        private_key(value)
        self.check(self.api.SecKeychainAddGenericPassword(
            self.ref, len(self.service), self.service, len(self.account), self.account,
            len(value), ctypes.c_char_p(value), None))


def validate_ca(key, certificate, expected, instant):
    public = certificate.public_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SIGNING["issuer_cn"])])
    if (not isinstance(public, rsa.RSAPublicKey) or public.key_size != 3072
            or public.public_numbers().e != 65537
            or key.public_key().public_numbers() != public.public_numbers()
            or certificate.fingerprint(hashes.SHA256()).hex() != expected
            or certificate.subject != name or certificate.issuer != name
            or certificate.version != x509.Version.v3
            or not isinstance(certificate.signature_hash_algorithm, hashes.SHA256)):
        raise ValueError("issuer identity/key/fingerprint mismatch")
    certificate.verify_directly_issued_by(certificate)
    bc = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
    ku = certificate.extensions.get_extension_for_class(x509.KeyUsage)
    expected_usage = x509.KeyUsage(False, False, False, False, False, True, True, False, False)
    if not bc.critical or bc.value != x509.BasicConstraints(True, 0) or not ku.critical or ku.value != expected_usage:
        raise ValueError("issuer constraints mismatch")
    if (not certificate.not_valid_before_utc <= instant < certificate.not_valid_after_utc
            or certificate.not_valid_after_utc - certificate.not_valid_before_utc > timedelta(days=730, minutes=5)):
        raise ValueError("issuer validity mismatch")


def validate_ledger(ledger, issuer):
    """Validate every retained certificate, including expired/superseded history."""
    if (ledger["version"] != 2 or ledger["signing"] != SIGNING
            or ledger["ca_sha256"] != issuer.fingerprint(hashes.SHA256()).hex()
            or ledger["issuer_certificate"] != pem(issuer)):
        raise ValueError("ledger issuer/configuration mismatch")
    issued = {}
    for entry in ledger["issued"]:
        identity = entry["identity"]
        cert = x509.load_pem_x509_certificate(entry["certificate"].encode())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "driftplain-home-server-" + identity)])
        public = cert.public_key()
        if (identity not in ("backup", "bedrock") or cert.subject != name
                or not isinstance(public, rsa.RSAPublicKey) or public.key_size != 3072 or public.public_numbers().e != 65537
                or cert.extensions.get_extension_for_class(x509.BasicConstraints).value != x509.BasicConstraints(False, None)
                or cert.extensions.get_extension_for_class(x509.KeyUsage).value != x509.KeyUsage(True, False, False, False, False, False, False, False, False)
                or not isinstance(cert.signature_hash_algorithm, hashes.SHA256)
                or cert.not_valid_after_utc > issuer.not_valid_after_utc
                or cert.not_valid_before_utc < issuer.not_valid_before_utc
                or cert.not_valid_after_utc - cert.not_valid_before_utc > timedelta(days=90, minutes=5)):
            raise ValueError("invalid issued certificate")
        cert.verify_directly_issued_by(issuer)
        serial = str(cert.serial_number)
        if serial in issued:
            raise ValueError("duplicate issued serial")
        issued[serial] = entry
    revoked = set()
    for entry in ledger["revoked"]:
        serial = entry["serial"]
        if serial not in issued or serial in revoked or entry["reason"] != "key_compromise":
            raise ValueError("unknown/duplicate revocation")
        if datetime.fromisoformat(entry["revoked_at"]).tzinfo is None:
            raise ValueError("revocation needs timezone")
        revoked.add(serial)
    for identity, entry in ledger["pending"].items():
        serial = str(x509.load_pem_x509_certificate(entry["certificate"].encode()).serial_number)
        if serial in revoked or issued.get(serial) != {"identity": identity, **entry}:
            raise ValueError("pending certificate missing from history or revoked")
    if type(ledger["crl_number"]) is not int or ledger["crl_number"] < 0:
        raise ValueError("invalid CRL sequence")
    if ledger.get("crl_pem"):
        crl = x509.load_pem_x509_crl(ledger["crl_pem"].encode())
        validate_crl(ledger["crl_pem"].encode(), issuer, ledger, crl.last_update_utc)
    elif ledger["crl_number"] or ledger["revoked"]:
        raise ValueError("missing historical CRL")
    return issued


def save_public(path, value):
    """Durable atomic write; only public records/ciphertext may use this helper."""
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".home-server-", delete=False) as stream:
        pending = Path(stream.name)
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        pending.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        pending.unlink(missing_ok=True)


def save_ledger(path, ledger):
    save_public(path, canonical(ledger))


def ensure_public(path, value):
    path = Path(path)
    if path.exists():
        if path.read_bytes() != value:
            raise ValueError("existing public file differs; refusing overwrite")
    else:
        save_public(path, value)


def enroll(config, keychain, instant):
    state, public = Path(config["ledger"]), Path(config["ca_certificate"])
    fingerprint = public.with_suffix(".sha256")
    value = keychain.get()  # locked/denied is never treated as missing
    if value is None:
        if (config["ca_sha256"] != "REPLACE_WITH_VERIFIED_PUBLIC_CA_FINGERPRINT"
                or any(path.exists() for path in (state, public, fingerprint))
                or (Path(config["encrypted_backup_directory"]).exists()
                    and any(Path(config["encrypted_backup_directory"]).iterdir()))):
            raise ValueError("issuer key missing with surviving records; recover, never regenerate")
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SIGNING["issuer_cn"])])
        issuer = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                  .public_key(key.public_key()).serial_number(x509.random_serial_number())
                  .not_valid_before(instant - timedelta(minutes=5)).not_valid_after(instant + timedelta(days=730))
                  .add_extension(x509.BasicConstraints(True, 0), True)
                  .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
                  .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
                  .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), False)
                  .sign(key, hashes.SHA256()))
        ledger = {"version": 2, "ca_sha256": issuer.fingerprint(hashes.SHA256()).hex(),
                  "issuer_certificate": pem(issuer), "signing": dict(SIGNING),
                  "pending": {}, "issued": [], "revoked": [], "crl_number": 0,
                  "backup_required": True}
        # Journal the exact public CA before custody: interruption cannot silently
        # generate another issuer. Failed add without a stored key requires review.
        save_ledger(state, ledger)
        value = private_pem(key)
        keychain.add(value)
        if keychain.get() != value:
            raise ValueError("issuer custody readback failed")
    else:
        # Never rebuild a missing ledger as empty; it may have issued/revoked history.
        ledger = json.loads(state.read_text())
        key = private_key(value)
        issuer = x509.load_pem_x509_certificate(ledger["issuer_certificate"].encode())
    validate_ca(key, issuer, ledger["ca_sha256"], instant)
    validate_ledger(ledger, issuer)
    pin = config["ca_sha256"]
    if pin != "REPLACE_WITH_VERIFIED_PUBLIC_CA_FINGERPRINT" and pin != ledger["ca_sha256"]:
        raise ValueError("configured issuer fingerprint differs")
    ensure_public(public, pem(issuer).encode())
    ensure_public(fingerprint, (ledger["ca_sha256"] + "\n").encode())
    return key, issuer, ledger


def read_issuer(config):
    value = IssuerKeychain(config["keychain"]).get()
    if value is None:
        raise ValueError("issuer missing; recovery required")
    key = private_key(value)
    issuer = x509.load_pem_x509_certificate(Path(config["ca_certificate"]).read_bytes())
    validate_ca(key, issuer, config["ca_sha256"], now())
    return key, issuer


def make_crl(ledger, key, issuer, instant, serial=None):
    validate_ca(key, issuer, ledger["ca_sha256"], instant)
    issued = validate_ledger(ledger, issuer)
    if serial is not None:
        if serial not in issued:
            raise ValueError("serial not in issuer history")
        if serial not in {entry["serial"] for entry in ledger["revoked"]}:
            ledger["revoked"].append({"serial": serial, "reason": "key_compromise", "revoked_at": instant.isoformat()})
        # Preserve issued history while preventing an interrupted delivery of this leaf.
        for identity, entry in list(ledger["pending"].items()):
            if str(x509.load_pem_x509_certificate(entry["certificate"].encode()).serial_number) == serial:
                del ledger["pending"][identity]
    number = ledger["crl_number"] + 1
    next_update = min(instant + timedelta(days=35), issuer.not_valid_after_utc)
    if next_update <= instant + timedelta(days=1):
        raise ValueError("issuer too close to expiry for a usable CRL")
    builder = (x509.CertificateRevocationListBuilder().issuer_name(issuer.subject)
               .last_update(instant).next_update(next_update)
               .add_extension(x509.CRLNumber(number), False)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer.public_key()), False))
    for entry in sorted(ledger["revoked"], key=lambda item: int(item["serial"])):
        builder = builder.add_revoked_certificate(
            x509.RevokedCertificateBuilder().serial_number(int(entry["serial"]))
            .revocation_date(datetime.fromisoformat(entry["revoked_at"]))
            .add_extension(x509.CRLReason(x509.ReasonFlags.key_compromise), False).build())
    crl = builder.sign(key, hashes.SHA256())
    ledger["crl_number"] = number
    ledger["crl_pem"] = crl.public_bytes(serialization.Encoding.PEM).decode()
    ledger["backup_required"] = True
    validate_crl(ledger["crl_pem"].encode(), issuer, ledger, instant)
    return ledger["crl_pem"].encode()


def validate_crl(data, issuer, ledger, instant):
    crl = x509.load_pem_x509_crl(data)
    if (crl.issuer != issuer.subject or not crl.is_signature_valid(issuer.public_key())
            or not isinstance(crl.signature_hash_algorithm, hashes.SHA256)
            or not crl.last_update_utc <= instant < crl.next_update_utc
            or crl.next_update_utc > issuer.not_valid_after_utc
            or crl.next_update_utc - crl.last_update_utc > timedelta(days=35)
            or crl.extensions.get_extension_for_class(x509.CRLNumber).value.crl_number != ledger["crl_number"]):
        raise ValueError("CRL signature, sequence or validity mismatch")
    expected = {int(entry["serial"]): entry for entry in ledger["revoked"]}
    if len(crl) != len(expected) or {entry.serial_number for entry in crl} != set(expected):
        raise ValueError("CRL does not contain the complete revocation ledger")
    for entry in crl:
        if (entry.revocation_date_utc != datetime.fromisoformat(expected[entry.serial_number]["revoked_at"])
                or entry.revocation_date_utc > instant
                or entry.extensions.get_extension_for_class(x509.CRLReason).value.reason != x509.ReasonFlags.key_compromise):
            raise ValueError("CRL revocation details mismatch")
    return crl


def recover_bundle(data, recovery_identity, config, expected_ledger_sha256, instant):
    if custody.recipient(recovery_identity) != config["recovery_recipient"]:
        raise ValueError("wrong recovery identity")
    with tempfile.TemporaryDirectory(prefix="home-server-issuer-recovery-") as folder:
        archive = Path(folder) / "issuer.age"
        archive.write_bytes(data)  # ciphertext only; decrypted JSON never touches disk
        plain = custody.run([config["age_binary"], "--decrypt", "-i", "-", str(archive)], recovery_identity)
    bundle = json.loads(plain)
    if bundle["version"] != 2 or bundle["recovery_recipient"] != config["recovery_recipient"]:
        raise ValueError("unexpected recovery bundle")
    key = private_key(bundle["issuer_private_key"].encode())
    issuer = x509.load_pem_x509_certificate(bundle["issuer_certificate"].encode())
    validate_ca(key, issuer, config["ca_sha256"], instant)
    ledger = bundle["ledger"]
    validate_ledger(ledger, issuer)
    if sha(canonical(ledger)) != expected_ledger_sha256:
        raise ValueError("recovered ledger differs from independent checkpoint")
    return key, issuer, ledger


def restore(config, keychain, key, issuer, ledger):
    """Only fill missing custody/files or accept exact matches. Never roll back state."""
    paths = {Path(config["ledger"]): canonical(ledger),
             Path(config["ca_certificate"]): pem(issuer).encode(),
             Path(config["ca_certificate"]).with_suffix(".sha256"): (ledger["ca_sha256"] + "\n").encode()}
    for path, value in paths.items():
        if path.exists() and path.read_bytes() != value:
            raise ValueError("surviving state differs; reconcile before restore")
    value = keychain.get()
    if value is not None and value != private_pem(key):
        raise ValueError("surviving issuer key differs")
    # Public recovery journal precedes custody, with the same retry rules as enrollment.
    ensure_public(Path(config["ledger"]), canonical(ledger))
    if value is None:
        keychain.add(private_pem(key))
    if keychain.get() != private_pem(key):
        raise ValueError("restored custody readback failed")
    for path, value in paths.items():
        ensure_public(path, value)


def fetch_bundle(s3, receipt, config):
    if (receipt["bucket"] != config["backup_bucket"] or not receipt["version_id"]
            or receipt["version_id"] == "null" or not receipt["object_key"].startswith("recovery/issuer-v1/")
            or receipt["ca_sha256"] != config["ca_sha256"]
            or receipt["recovery_key_id"] != RECOVERY_SERVICE
            or receipt["recovery_recipient"] != config["recovery_recipient"]):
        raise ValueError("recovery receipt binding mismatch")
    data = s3.get_object(Bucket=receipt["bucket"], Key=receipt["object_key"],
                         VersionId=receipt["version_id"])["Body"].read()
    if sha(data) != receipt["ciphertext_sha256"]:
        raise ValueError("recovery ciphertext differs")
    return data


def verify_cloud_crl(anchor_record, crl_record, anchor_arn, crl_id, ledger, instant):
    """Check public AWS CLI readback files. No cloud write or credential exchange."""
    if not re.fullmatch(r"arn:aws:rolesanywhere:ap-south-1:957261948820:trust-anchor/[0-9a-f-]{36}", anchor_arn):
        raise ValueError("unexpected trust-anchor ARN")
    anchor, record = anchor_record["trustAnchor"], crl_record["crl"]
    if (anchor["trustAnchorArn"] != anchor_arn or anchor["source"]["sourceType"] != "CERTIFICATE_BUNDLE"
            or record["trustAnchorArn"] != anchor_arn or record["crlId"] != crl_id
            or record["enabled"] is not True):
        raise ValueError("wrong anchor/CRL binding or disabled CRL")
    certificate = x509.load_pem_x509_certificate(anchor["source"]["sourceData"]["x509CertificateData"].encode())
    if certificate.fingerprint(hashes.SHA256()).hex() != ledger["ca_sha256"]:
        raise ValueError("cloud anchor differs from enrolled CA")
    validate_ledger(ledger, certificate)
    data = base64.b64decode(record["crlData"], validate=True)  # AWS CLI JSON blob representation
    if data != ledger["crl_pem"].encode():
        raise ValueError("cloud CRL differs from latest ledger")
    validate_crl(data, certificate, ledger, instant)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("enroll", "backup", "verify-recovery", "restore", "crl", "revoke", "verify-crl"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--expected-ledger-sha256")
    parser.add_argument("--recovery-source", choices=("aws", "keychain"))
    parser.add_argument("--serial", help="decimal serial from the retained issuer ledger")
    parser.add_argument("--anchor-record", type=Path)
    parser.add_argument("--crl-record", type=Path)
    parser.add_argument("--anchor-arn")
    parser.add_argument("--crl-id")
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(args.config.read_text())
    if (config["backup_bucket"] != BUCKET
            or config["recovery_recipient"] != (ROOT / "recovery-key-v1.recipient").read_text().strip()):
        raise ValueError("unexpected recovery destination/recipient")
    if args.command == "revoke" and (not args.serial or not args.serial.isdecimal()):
        parser.error("revoke requires a decimal --serial")
    recovery = args.command in ("verify-recovery", "restore")
    if recovery and not (args.receipt and args.expected_ledger_sha256 and args.recovery_source):
        parser.error("recovery requires --receipt, --expected-ledger-sha256 and --recovery-source")
    if args.command == "verify-crl" and not (args.anchor_record and args.crl_record and args.anchor_arn and args.crl_id):
        parser.error("verify-crl requires --anchor-record, --crl-record, --anchor-arn and --crl-id")
    state = Path(config["ledger"])
    state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with state.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == "verify-crl":
            ledger = json.loads(state.read_text())
            if ledger["ca_sha256"] != config["ca_sha256"]:
                raise ValueError("configured fingerprint differs")
            verify_cloud_crl(json.loads(args.anchor_record.read_text()), json.loads(args.crl_record.read_text()),
                             args.anchor_arn, args.crl_id, ledger, now())
        elif recovery:
            import boto3
            session = boto3.Session(profile_name=config["aws_profile"], region_name="ap-south-1")
            if session.client("sts").get_caller_identity()["Arn"] != OPERATOR:
                raise ValueError("unexpected recovery operator")
            receipt = json.loads(args.receipt.read_text())
            data = fetch_bundle(session.client("s3"), receipt, config)
            if args.recovery_source == "aws":
                recovery_identity = custody.AWSSecret(config["aws_profile"], "ap-south-1", RECOVERY_SERVICE, OPERATOR).get()
            else:
                recovery_identity = custody.LocalKeychain(Path(config["keychain"]), RECOVERY_SERVICE, "steve").get()
            key, issuer, ledger = recover_bundle(data, recovery_identity, config, args.expected_ledger_sha256, now())
            if receipt["ledger_sha256"] != args.expected_ledger_sha256:
                raise ValueError("receipt differs from independent ledger checkpoint")
            if args.command == "restore":
                restore(config, IssuerKeychain(config["keychain"]), key, issuer, ledger)
        elif args.command == "enroll":
            key, issuer, ledger = enroll(config, IssuerKeychain(config["keychain"]), now())
        else:
            key, issuer = read_issuer(config)
            ledger = json.loads(state.read_text())
            validate_ledger(ledger, issuer)
            if args.command in ("crl", "revoke"):
                make_crl(ledger, key, issuer, now(), args.serial if args.command == "revoke" else None)
                save_ledger(state, ledger)  # retain revocation even if S3 is unavailable
            renew = load("home_server_renew", "home-server-renew.py")
            receipt = renew.HomeServerOperations(config, key, issuer).backup(ledger)
            if args.command in ("crl", "revoke"):
                # Publish only after backup; an interrupted command can use `crl` to retry.
                ensure_public(state.parent / ("issuer-crl-" + str(ledger["crl_number"]) + ".pem"), ledger["crl_pem"].encode())
        result = {"command": args.command, "ca_sha256": ledger["ca_sha256"],
                  "ledger_sha256": sha(canonical(ledger)), "issued_count": len(ledger["issued"]),
                  "revoked_count": len(ledger["revoked"]), "crl_number": ledger["crl_number"]}
        if args.command in ("backup", "crl", "revoke"):
            result["receipt"] = receipt
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("Issuer operation failed; private diagnostics suppressed. Preserve custody and records; see docs/ISSUER.md.", file=sys.stderr)
        sys.exit(1)
