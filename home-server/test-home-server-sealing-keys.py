"""HM4 sealing-key custody: pure bundle/manifest logic with fakes; a real offline seal ->
recovery-unseal round trip with a throwaway RSA key (skipped without kubeseal/openssl).

Unit tests never touch the network, Keychain, AWS or the home cluster."""

import base64
import datetime
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sk = load("home_server_sealing_keys", "home-server-sealing-keys.py")
NOW = datetime.datetime(2026, 9, 15, 20, 0, tzinfo=datetime.timezone.utc)


def key_item(name, status, crt=b"CERT-A", key=b"KEY-A", created="2026-09-15T19:00:00Z"):
    return {"apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/tls",
            "metadata": {"name": name, "namespace": "sealed-secrets", "creationTimestamp": created,
                         "labels": {sk.KEY_LABEL: status}, "managedFields": [{"noise": True}]},
            "data": {"tls.crt": base64.b64encode(crt).decode(), "tls.key": base64.b64encode(key).decode()}}


def app_item(name, kind, data, labels=None):
    item = {"apiVersion": "v1", "kind": "Secret", "type": kind,
            "metadata": {"name": name, "namespace": "app", "labels": labels or {}},
            "data": {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}}
    return item


APP = app_item("modelmatch-app-secrets", "Opaque",
               {"JWT_SECRET": "j", "POSTGRES_PASSWORD": "p", "CHAT_READONLY_DB_PASSWORD": "c", "DEMO_SEED_PASSWORD": "d"})
OWNER = app_item("modelmatch-db-app", "kubernetes.io/basic-auth", {"username": "modelmatch", "password": "p"},
                 labels={"cnpg.io/reload": "true"})


class BundleTests(unittest.TestCase):
    def test_bundle_keeps_every_key_and_manifest_only_public_facts(self):
        items = [key_item("sealed-secrets-keyb", "compromised", b"CERT-B", b"KEY-B"),
                 key_item("sealed-secrets-keya", "active")]
        bundle, manifest = sk.bundle_from_items(items, NOW)
        decoded = json.loads(bundle)
        self.assertEqual([k["metadata"]["name"] for k in decoded["items"]], ["sealed-secrets-keya", "sealed-secrets-keyb"])
        self.assertEqual(decoded["items"][0]["data"]["tls.key"], base64.b64encode(b"KEY-A").decode())
        self.assertNotIn("managedFields", json.dumps(decoded))
        self.assertEqual([(k["name"], k["status"]) for k in manifest["keys"]],
                         [("sealed-secrets-keya", "active"), ("sealed-secrets-keyb", "compromised")])
        self.assertEqual(manifest["keys"][0]["cert_sha256"], sk.hashlib.sha256(b"CERT-A").hexdigest())
        self.assertNotIn("KEY-", json.dumps(manifest))
        self.assertNotIn("tls.key", json.dumps(manifest))
        self.assertEqual(sk.check_bundle_against_manifest(decoded, manifest), 2)

    def test_refuses_no_active_key_or_odd_shapes(self):
        with self.assertRaises(sk.SealingError):
            sk.bundle_from_items([key_item("k", "compromised")], NOW)
        with self.assertRaises(sk.SealingError):
            sk.bundle_from_items([], NOW)
        odd = key_item("k", "active"); odd["type"] = "Opaque"
        with self.assertRaises(sk.SealingError):
            sk.bundle_from_items([odd], NOW)
        odd = key_item("k", "active"); odd["metadata"]["labels"][sk.KEY_LABEL] = "unknown"
        with self.assertRaises(sk.SealingError):
            sk.bundle_from_items([odd], NOW)

    def test_list_prints_public_facts_only(self):
        items = [key_item("sealed-secrets-keya", "active"), key_item("sealed-secrets-keyb", "compromised", b"CERT-B", b"KEY-B")]
        out = io.StringIO()
        with patch.object(sk, "home_prefix", return_value=(lambda argv: argv, {"context": "driftplain-home"})), \
                patch.object(sk, "sealing_key_items", return_value=items), patch("sys.stdout", out):
            sk.main(["list"])
        printed = json.loads(out.getvalue())
        self.assertEqual([k["name"] for k in printed["keys"]], ["sealed-secrets-keya", "sealed-secrets-keyb"])
        self.assertEqual(set(printed["keys"][0]), {"name", "status", "created", "cert_sha256"})
        self.assertNotIn(base64.b64encode(b"KEY-A").decode(), out.getvalue())

    def test_manifest_mismatch_is_refused(self):
        bundle, manifest = sk.bundle_from_items([key_item("k", "active")], NOW)
        other, _ = sk.bundle_from_items([key_item("k", "active", crt=b"OTHER")], NOW)
        with self.assertRaises(sk.SealingError):
            sk.check_bundle_against_manifest(json.loads(other), manifest)


class SealInputTests(unittest.TestCase):
    def test_exact_secret_manifests_from_the_hm3_bundle(self):
        app = sk.secret_manifest_from_bundle_item(APP)
        self.assertEqual((app["type"], app["metadata"]), ("Opaque", {"name": "modelmatch-app-secrets", "namespace": "app"}))
        self.assertEqual(set(app["data"]), {"JWT_SECRET", "POSTGRES_PASSWORD", "CHAT_READONLY_DB_PASSWORD", "DEMO_SEED_PASSWORD"})
        owner = sk.secret_manifest_from_bundle_item(OWNER)
        self.assertEqual(owner["type"], "kubernetes.io/basic-auth")
        self.assertEqual(owner["metadata"]["labels"], {"cnpg.io/reload": "true"})
        self.assertEqual(owner["data"], OWNER["data"])

    def test_refuses_unexpected_items_keys_or_namespace(self):
        with self.assertRaises(sk.SealingError):
            sk.secret_manifest_from_bundle_item(app_item("home-server-bedrock-identity", "kubernetes.io/tls", {"tls.key": "x"}))
        extra = app_item("modelmatch-app-secrets", "Opaque", {"JWT_SECRET": "j"})
        with self.assertRaises(sk.SealingError):
            sk.secret_manifest_from_bundle_item(extra)
        moved = json.loads(json.dumps(OWNER)); moved["metadata"]["namespace"] = "default"
        with self.assertRaises(sk.SealingError):
            sk.secret_manifest_from_bundle_item(moved)


class DownloadTests(unittest.TestCase):
    class FakeClient:
        def __init__(self, objects):
            self.objects = objects

        def get_object(self, Bucket, Key, VersionId, ChecksumMode):
            body = self.objects[(Key, VersionId)]
            return {"VersionId": VersionId, "Body": type("B", (), {"read": lambda s: body})()}

    def test_download_verifies_version_and_digest(self):
        body = b"ciphertext"
        receipt = {"objects": {"x.age": {"key": "k", "version_id": "v1", "sha256": sk.hashlib.sha256(body).hexdigest()}}}
        with tempfile.TemporaryDirectory() as tmp:
            got = sk.download_by_receipt(self.FakeClient({("k", "v1"): body}), receipt, Path(tmp))
            self.assertEqual(got["x.age"]["size"], len(body))
            self.assertEqual((Path(tmp) / "x.age").read_bytes(), body)
            self.assertEqual(oct((Path(tmp) / "x.age").stat().st_mode & 0o777), "0o600")
        receipt["objects"]["x.age"]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(sk.SealingError):
            sk.download_by_receipt(self.FakeClient({("k", "v1"): body}), receipt, Path(tmp))


@unittest.skipUnless(shutil.which("kubeseal") and shutil.which("openssl"), "kubeseal/openssl required")
class OfflineRoundTripTests(unittest.TestCase):
    """seal with a throwaway public cert -> recovery-unseal with its private key == original."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sealing-test-"))
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                        "-subj", "/CN=throwaway-sealing-test", "-keyout", str(cls.tmp / "key.pem"),
                        "-out", str(cls.tmp / "cert.pem")], check=True, capture_output=True, timeout=60)
        cls.pem = (cls.tmp / "key.pem").read_bytes()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def sealed_file(self, secret, name="sealed.yaml"):
        sealed = sk.seal_secret(secret, self.tmp / "cert.pem")
        path = self.tmp / name
        path.write_bytes(sealed)
        return path, sealed.decode()

    def test_round_trip_and_strict_scope(self):
        secret = sk.secret_manifest_from_bundle_item(OWNER)
        path, text = self.sealed_file(secret)
        self.assertIn("kind: SealedSecret", text)
        self.assertNotIn(base64.b64encode(b"modelmatch").decode(), text)  # values are encrypted, never plain
        self.assertIn("cnpg.io/reload", text)  # template keeps labels + type
        self.assertIn("kubernetes.io/basic-auth", text)
        with patch.object(sk.db, "BACKUPS_ROOT", self.tmp):
            recovered = sk.recovery_unseal([self.pem], path)
        self.assertEqual(recovered["data"], secret["data"])
        self.assertEqual(recovered["type"], "kubernetes.io/basic-auth")
        self.assertEqual(recovered["metadata"]["labels"], {"cnpg.io/reload": "true"})
        self.assertFalse(list(self.tmp.glob("sealing-recovery-*")))  # key temp dir removed
        # strict scope: the same ciphertext under another name/namespace must not unseal
        moved = text.replace("name: modelmatch-db-app", "name: modelmatch-db-other")
        (self.tmp / "moved.yaml").write_text(moved)
        with patch.object(sk.db, "BACKUPS_ROOT", self.tmp), self.assertRaises(sk.SealingError):
            sk.recovery_unseal([self.pem], self.tmp / "moved.yaml")

    def test_wrong_key_cannot_unseal(self):
        secret = sk.secret_manifest_from_bundle_item(APP)
        path, _ = self.sealed_file(secret, "app.yaml")
        subprocess.run(["openssl", "genrsa", "-out", str(self.tmp / "wrong.pem"), "2048"], check=True,
                       capture_output=True, timeout=60)
        with patch.object(sk.db, "BACKUPS_ROOT", self.tmp), self.assertRaises(sk.SealingError):
            sk.recovery_unseal([(self.tmp / "wrong.pem").read_bytes()], path)
        # several candidate keys are accepted; the right one wins
        with patch.object(sk.db, "BACKUPS_ROOT", self.tmp):
            recovered = sk.recovery_unseal([(self.tmp / "wrong.pem").read_bytes(), self.pem], path)
        self.assertEqual(recovered["data"], secret["data"])


if __name__ == "__main__":
    unittest.main()
