#!/usr/bin/env python3
"""E21/HM4 — Sealed Secrets key custody and sealing for the HOME cluster (operator, Mac only).

The home Sealed Secrets controller (gitops argocd/home-server/apps/sealed-secrets.yaml) is
the only thing that can unseal the committed SealedSecret manifests. Its sealing keys are
Kubernetes Secrets in its namespace (label sealedsecrets.bitnami.com/sealed-secrets-key);
losing them means re-sealing every manifest from the recovery source. This tool:

  fetch-cert       writes the ACTIVE sealing public certificate (public; safe to commit)
  backup           every sealing-key Secret -> one age-encrypted bundle (Mac, 0600) -> the
                   versioned backup bucket (recovery/sealing-keys/<stamp>/), receipt with versions
  verify-recovery  an INDEPENDENT download by key+version -> decrypt in memory -> prove the
                   recovered private key(s) unseal a committed SealedSecret without the cluster
                   (kubeseal --recovery-unseal) and that the result equals the original value
                   from the HM3 credential bundle. Booleans only are printed.
  seal             the two original app Secrets from the HM3 bundle -> SealedSecret manifests
                   (strict scope: bound to exact name + namespace) for the gitops repo
  adopt            annotate an existing operator-created Secret so the controller takes
                   ownership of it when its SealedSecret syncs (same value; no rotation)

Reuses home-server-database.py (transports, S3 upload/verify, age custody) so custody rules
are identical: the private recovery identity feeds age's stdin from ONE selected store and
never touches disk; nothing decrypted is printed, logged or written except the private-key
temp files kubeseal needs for --recovery-unseal (a fresh 0700 directory, overwritten and
removed afterwards). Every subprocess is bounded.
"""
import argparse
import base64
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def load_database_module():
    spec = importlib.util.spec_from_file_location("home_server_database", ROOT / "home-server-database.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


db = load_database_module()

CONTROLLER_NAMESPACE = "sealed-secrets"
CONTROLLER_NAME = "sealed-secrets-controller"
KEY_LABEL = "sealedsecrets.bitnami.com/sealed-secrets-key"
MANAGED_ANNOTATION = "sealedsecrets.bitnami.com/managed"
S3_PREFIX = "recovery/sealing-keys"
BUNDLE_NAME = "sealing-keys.json.age"
MANIFEST_NAME = "manifest.json"
KUBESEAL_TIMEOUT = 60

# The exact app Secrets HM4 seals (HM2-DECISIONS: preserve these, exactly these keys).
SEALED_ITEMS = {
    db.APP_SECRET: ("Opaque", {"JWT_SECRET", "POSTGRES_PASSWORD", "CHAT_READONLY_DB_PASSWORD", "DEMO_SEED_PASSWORD"}),
    db.OWNER_SECRET: ("kubernetes.io/basic-auth", {"username", "password"}),
}


class SealingError(Exception):
    pass


def fingerprint(pem_b64):
    """SHA-256 of the PEM bytes of a certificate (public metadata)."""
    return hashlib.sha256(base64.b64decode(pem_b64)).hexdigest()


# ── bundle model (pure; unit-tested) ─────────────────────────────────────────────

def bundle_from_items(items, created_at):
    """Sealing-key Secrets (kubectl JSON items) -> (encryptable bundle bytes, public manifest)."""
    keys, manifest_keys = [], []
    for item in items:
        meta = item["metadata"]
        data = item.get("data") or {}
        if item.get("type") != "kubernetes.io/tls" or set(data) != {"tls.crt", "tls.key"}:
            raise SealingError("sealing-key Secret has an unexpected type or key set; refusing")
        status = (meta.get("labels") or {}).get(KEY_LABEL)
        if status not in ("active", "compromised"):
            raise SealingError("sealing-key Secret carries an unexpected label value; refusing")
        keys.append({"apiVersion": "v1", "kind": "Secret", "type": item["type"],
                     "metadata": {"name": meta["name"], "namespace": meta["namespace"],
                                  "labels": {KEY_LABEL: status},
                                  "creationTimestamp": meta.get("creationTimestamp")},
                     "data": {"tls.crt": data["tls.crt"], "tls.key": data["tls.key"]}})
        manifest_keys.append({"name": meta["name"], "status": status,
                              "created": meta.get("creationTimestamp"),
                              "cert_sha256": fingerprint(data["tls.crt"])})
    if not any(k["status"] == "active" for k in manifest_keys):
        raise SealingError("no ACTIVE sealing key found; refusing to back up an empty custody set")
    bundle = json.dumps({"created_at": created_at.isoformat(), "controller_namespace": CONTROLLER_NAMESPACE,
                         "items": sorted(keys, key=lambda k: k["metadata"]["name"])}, sort_keys=True).encode()
    manifest = {"created_at": created_at.isoformat(), "controller_namespace": CONTROLLER_NAMESPACE,
                "keys": sorted(manifest_keys, key=lambda k: k["name"])}
    return bundle, manifest


def check_bundle_against_manifest(bundle, manifest):
    """The decrypted bundle must describe exactly the keys the public manifest lists."""
    got = sorted((k["metadata"]["name"], k["metadata"]["labels"][KEY_LABEL], fingerprint(k["data"]["tls.crt"]))
                 for k in bundle["items"])
    want = sorted((k["name"], k["status"], k["cert_sha256"]) for k in manifest["keys"])
    if got != want:
        raise SealingError("recovered sealing keys do not match the manifest; refusing")
    return len(got)


def secret_manifest_from_bundle_item(item):
    """An HM3 credential-bundle item -> the exact Secret to seal (name/namespace/type/labels/data)."""
    name = item["metadata"]["name"]
    if name not in SEALED_ITEMS:
        raise SealingError("bundle item is not one of the two sealed app Secrets; refusing")
    expected_type, expected_keys = SEALED_ITEMS[name]
    if item.get("type") != expected_type or set(item.get("data") or {}) != expected_keys:
        raise SealingError(f"{name}: unexpected type or key set; refusing")
    if item["metadata"].get("namespace") != db.NAMESPACE:
        raise SealingError(f"{name}: unexpected namespace; refusing")
    metadata = {"name": name, "namespace": db.NAMESPACE}
    labels = item["metadata"].get("labels") or {}
    if labels:
        metadata["labels"] = dict(labels)
    return {"apiVersion": "v1", "kind": "Secret", "type": expected_type, "metadata": metadata,
            "data": dict(item["data"])}


# ── kubeseal (bounded subprocesses) ───────────────────────────────────────────────

def run_kubeseal(argv, payload, timeout=KUBESEAL_TIMEOUT):
    try:
        result = subprocess.run(["kubeseal", *argv], input=payload, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise SealingError("kubeseal is not installed on this Mac") from None
    except subprocess.TimeoutExpired:
        raise SealingError("TIMEOUT: kubeseal did not finish") from None
    if result.returncode:
        raise SealingError(f"kubeseal {argv[0]} failed (exit {result.returncode}); output suppressed")
    return result.stdout


def seal_secret(secret, cert_path):
    """Secret JSON -> SealedSecret YAML (strict scope) using the public cert, offline."""
    return run_kubeseal(["--cert", str(cert_path), "--scope", "strict", "--format", "yaml",
                         "--controller-namespace", CONTROLLER_NAMESPACE, "--controller-name", CONTROLLER_NAME],
                        json.dumps(secret).encode())


def recovery_unseal(private_key_pems, sealed_path):
    """Unseal WITHOUT the cluster: kubeseal --recovery-unseal with the recovered private keys.

    kubeseal takes key files, so each PEM is written to a fresh 0700 directory (private umask),
    overwritten with zeros and removed afterwards. The unsealed Secret is returned in memory only.
    """
    directory = Path(tempfile.mkdtemp(prefix="sealing-recovery-", dir=str(db.BACKUPS_ROOT)))
    os.chmod(directory, 0o700)
    try:
        argv = ["--recovery-unseal", "-f", str(sealed_path), "-o", "json"]
        for index, pem in enumerate(private_key_pems):
            path = directory / f"key-{index}.pem"
            db.write_private(path, pem)
            argv += ["--recovery-private-key", str(path)]
        return json.loads(run_kubeseal(argv, b""))
    finally:
        for path in directory.glob("*.pem"):
            with path.open("r+b") as handle:
                handle.write(b"\0" * path.stat().st_size)
            path.unlink()
        shutil.rmtree(directory, ignore_errors=True)


# ── cluster access (home only) ───────────────────────────────────────────────────

def home_prefix():
    _, remote_prefix, target = db.target_shell_without_cluster()
    return remote_prefix, target


def sealing_key_items(remote_prefix):
    listing = db.kube_json(remote_prefix(["-n", CONTROLLER_NAMESPACE, "get", "secret", "-l", KEY_LABEL]), [])
    return listing.get("items") or []


# ── subcommands ──────────────────────────────────────────────────────────────────

def fetch_cert(args):
    remote_prefix, target = home_prefix()
    active = [i for i in sealing_key_items(remote_prefix) if (i["metadata"].get("labels") or {}).get(KEY_LABEL) == "active"]
    if len(active) != 1:
        raise SealingError(f"expected exactly one ACTIVE sealing key, found {len(active)}")
    pem = base64.b64decode(active[0]["data"]["tls.crt"])
    Path(args.out).write_bytes(pem)
    print(json.dumps({"target": target, "key": active[0]["metadata"]["name"], "out": args.out,
                      "cert_sha256": hashlib.sha256(pem).hexdigest()}, indent=2))


def backup(args):
    remote_prefix, target = home_prefix()
    created = db.now()
    bundle, manifest = bundle_from_items(sealing_key_items(remote_prefix), created)
    directory = db.private_dir(db.BACKUPS_ROOT / f"sealing-keys-{db.stamp(created)}")
    recipient = db.RECIPIENT_FILE.read_text().strip()
    db.encrypt_bytes(recipient, bundle, directory / BUNDLE_NAME)
    manifest["bundle"] = {"name": BUNDLE_NAME, "size": (directory / BUNDLE_NAME).stat().st_size,
                          "sha256": db.sha256_file(directory / BUNDLE_NAME), "recipient": recipient}
    manifest["target"] = target
    db.write_private(directory / MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True).encode())
    receipt = {"bucket": db.BUCKET, "region": db.REGION, "backup_dir": str(directory), "objects": {}}
    if not args.no_upload:
        client = db.s3_session()
        base = f"{S3_PREFIX}/{db.stamp(created)}/"
        receipt["objects"][BUNDLE_NAME] = db.put_object(client, directory / BUNDLE_NAME, base + BUNDLE_NAME,
                                                        manifest["bundle"]["sha256"])
        receipt["objects"][MANIFEST_NAME] = db.put_object(client, directory / MANIFEST_NAME, base + MANIFEST_NAME)
        db.write_private(directory / "upload-receipt.json", json.dumps(receipt, indent=2, sort_keys=True).encode())
    print(json.dumps({"backup_dir": str(directory), "keys": manifest["keys"], "bundle": manifest["bundle"],
                      "uploaded": receipt["objects"]}, indent=2))


def download_by_receipt(client, receipt, directory):
    downloaded = {}
    for name, entry in receipt["objects"].items():
        response = client.get_object(Bucket=db.BUCKET, Key=entry["key"], VersionId=entry["version_id"], ChecksumMode="ENABLED")
        body = response["Body"].read()
        if response.get("VersionId") != entry["version_id"]:
            raise SealingError("downloaded version differs from the receipt")
        if hashlib.sha256(body).hexdigest() != entry["sha256"]:
            raise SealingError("downloaded bytes differ from the upload receipt")
        db.write_private(directory / name, body)
        downloaded[name] = {"key": entry["key"], "version_id": entry["version_id"], "size": len(body)}
    return downloaded


def verify_recovery(args):
    """Independent proof: S3 copy -> recovery identity -> private keys -> offline unseal == original."""
    receipt = json.loads(Path(args.receipt).read_text())
    started = db.now()
    directory = db.private_dir(db.BACKUPS_ROOT / f"sealing-keys-restore-{db.stamp(started)}")
    downloaded = download_by_receipt(db.s3_session(), receipt, directory)
    manifest = json.loads((directory / MANIFEST_NAME).read_text())
    identity = db.recovery_identity(args.identity_source)
    bundle = json.loads(db.decrypt_to_bytes(identity, directory / BUNDLE_NAME))
    key_count = check_bundle_against_manifest(bundle, manifest)
    pems = [base64.b64decode(k["data"]["tls.key"]) for k in bundle["items"]]
    originals = {i["metadata"]["name"]: i for i in
                 json.loads(db.decrypt_to_bytes(identity, Path(args.restore_dir) / "app-credentials.json.age"))["items"]}
    results = []
    for sealed_path in args.sealed:
        recovered = recovery_unseal(pems, Path(sealed_path))
        name = recovered["metadata"]["name"]
        original = originals.get(name)
        results.append({"sealed": str(sealed_path), "name": name, "namespace": recovered["metadata"].get("namespace"),
                        "type_match": bool(original) and recovered.get("type") == original.get("type"),
                        "keys_match": bool(original) and set(recovered.get("data") or {}) == set(original["data"]),
                        "values_match": bool(original) and (recovered.get("data") or {}) == original["data"]})
    finished = db.now()
    summary = {"restore_dir": str(directory), "downloaded": downloaded, "recovered_keys": key_count,
               "manifest_match": True, "unseal": results,
               "all_match": all(r["type_match"] and r["keys_match"] and r["values_match"] for r in results),
               "seconds": round((finished - started).total_seconds(), 3)}
    db.write_private(directory / "verify-result.json", json.dumps(summary, indent=2, sort_keys=True).encode())
    print(json.dumps(summary, indent=2))
    if not summary["all_match"] or not results:
        raise SealingError("recovery proof FAILED")


def seal(args):
    identity = db.recovery_identity(args.identity_source)
    bundle = json.loads(db.decrypt_to_bytes(identity, Path(args.restore_dir) / "app-credentials.json.age"))
    items = {i["metadata"]["name"]: i for i in bundle["items"]}
    if set(items) != set(SEALED_ITEMS):
        raise SealingError("HM3 bundle does not hold exactly the two app Secrets; refusing")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in sorted(items):
        secret = secret_manifest_from_bundle_item(items[name])
        sealed = seal_secret(secret, args.cert)
        header = (f"# E21/HM4 — SealedSecret for app/{name} (strict scope: name + namespace bound).\n"
                  f"# Sealed on the Mac from the original HM3 credential bundle with the home controller's\n"
                  f"# public certificate; only the home Sealed Secrets controller can unseal it. Regenerate\n"
                  f"# with driftplain-infra/home-server/home-server-sealing-keys.py seal. Never edit by hand.\n")
        path = out_dir / f"{name}.yaml"
        path.write_bytes(header.encode() + sealed)
        written.append({"name": name, "type": secret["type"], "keys": sorted(secret["data"]), "file": str(path)})
    print(json.dumps({"cert": str(args.cert), "written": written}, indent=2))


def adopt(args):
    remote_prefix, target = home_prefix()
    if args.name not in SEALED_ITEMS:
        raise SealingError("only the two sealed app Secrets can be adopted; refusing")
    result = subprocess.run(remote_prefix(["-n", db.NAMESPACE, "annotate", "secret", args.name,
                                          f"{MANAGED_ANNOTATION}=true", "--overwrite"]),
                            capture_output=True, timeout=90)
    if result.returncode:
        raise SealingError("annotate failed; output suppressed")
    print(json.dumps({"target": target, "name": args.name, "annotation": MANAGED_ANNOTATION,
                      "result": result.stdout.decode().strip()}, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("fetch-cert"); p.add_argument("--out", required=True)
    p = sub.add_parser("backup"); p.add_argument("--no-upload", action="store_true")
    p = sub.add_parser("verify-recovery")
    p.add_argument("--receipt", required=True); p.add_argument("--restore-dir", required=True)
    p.add_argument("--identity-source", choices=["aws", "keychain"], required=True)
    p.add_argument("--sealed", action="append", required=True, help="a committed SealedSecret manifest (repeatable)")
    p = sub.add_parser("seal")
    p.add_argument("--restore-dir", required=True); p.add_argument("--identity-source", choices=["aws", "keychain"], required=True)
    p.add_argument("--cert", required=True); p.add_argument("--out-dir", required=True)
    p = sub.add_parser("adopt"); p.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    try:
        {"fetch-cert": fetch_cert, "backup": backup, "verify-recovery": verify_recovery,
         "seal": seal, "adopt": adopt}[args.command](args)
    except (SealingError, db.DatabaseError) as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
