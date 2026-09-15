"""Bootstrap failure boundaries with disposable crypto and fake remote/AWS calls."""

import base64
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from unittest.mock import patch

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


bootstrap = load("home_server_bootstrap", "home-server-bootstrap.py")
host = load("home_server_bootstrap_leaf", "home-server-bootstrap-leaf.py")


class BootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instant = datetime.now(timezone.utc).replace(microsecond=0)
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        cls.leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "driftplain-home-server-issuer-v1")])
        cls.issuer = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                      .public_key(cls.key.public_key()).serial_number(x509.random_serial_number())
                      .not_valid_before(cls.instant - timedelta(minutes=5))
                      .not_valid_after(cls.instant + timedelta(days=730))
                      .add_extension(x509.BasicConstraints(True, 0), True)
                      .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
                      .sign(cls.key, hashes.SHA256()))

    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")).start()
        patch.object(bootstrap.issuer_tools.custody.LocalKeychain, "__init__",
                     side_effect=AssertionError("real Keychain forbidden")).start()
        self.ledger = {"version": 2, "signing": dict(bootstrap.issuer_tools.SIGNING),
                       "ca_sha256": self.issuer.fingerprint(hashes.SHA256()).hex(),
                       "issuer_certificate": bootstrap.issuer_tools.pem(self.issuer),
                       "crl_number": 0, "pending": {}, "issued": [], "revoked": []}
        self.current = {"backup": None, "bedrock": None}
        self.events = []
        self.fail_upload = False
        self.lose_install_response = False

    def remote(self, operation, identity, certificate=None):
        self.events.append(operation)
        if operation == "status":
            return {"certificate": self.current[identity]}
        if operation == "prepare":
            csr = (x509.CertificateSigningRequestBuilder().subject_name(x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, "driftplain-home-server-" + identity)]))
                .sign(self.leaf_key, hashes.SHA256()))
            return {"csr": csr.public_bytes(serialization.Encoding.PEM).decode()}
        self.current[identity] = certificate
        if self.lose_install_response:
            self.lose_install_response = False
            raise RuntimeError("lost response")
        return {"installed": True}

    def save(self, ledger):
        self.events.append("save")

    def backup(self, ledger):
        self.events.append("backup")
        if self.fail_upload:
            raise RuntimeError("S3 unavailable")
        return {"ledger_sha256": bootstrap.issuer_tools.sha(bootstrap.issuer_tools.canonical(ledger))}

    def run_bootstrap(self, identity="backup", backup=None):
        return bootstrap.bootstrap_identity(identity, self.ledger, self.save, self.remote,
                                             backup or self.backup, self.key, self.issuer, self.instant)

    def test_two_identities_full_history_and_replay(self):
        for identity in self.current:
            result = self.run_bootstrap(identity)
            self.assertEqual(result["receipt"]["ledger_sha256"],
                             bootstrap.issuer_tools.sha(bootstrap.issuer_tools.canonical(self.ledger)))
        self.assertEqual(len(self.ledger["issued"]), 2)
        self.assertFalse(self.ledger["pending"])
        self.assertLess(self.events.index("save"), self.events.index("backup"))
        self.assertLess(self.events.index("backup"), self.events.index("install"))
        self.run_bootstrap()
        self.assertEqual(len(self.ledger["issued"]), 2)
        self.assertEqual(self.events.count("prepare"), 2)

    def test_upload_failure_prevents_install_and_reuses_serial(self):
        self.fail_upload = True
        with self.assertRaises(RuntimeError):
            self.run_bootstrap()
        serial = self.ledger["pending"]["backup"]["certificate"]
        self.assertIsNone(self.current["backup"])
        self.assertNotIn("install", self.events)
        self.fail_upload = False
        self.run_bootstrap()
        self.assertEqual(self.current["backup"], serial)
        self.assertEqual(self.events.count("prepare"), 1)

    def test_lost_install_response_reconciles_same_certificate(self):
        self.lose_install_response = True
        with self.assertRaises(RuntimeError):
            self.run_bootstrap()
        self.assertIn("backup", self.ledger["pending"])
        self.run_bootstrap()
        self.assertFalse(self.ledger["pending"])
        self.assertEqual(len(self.ledger["issued"]), 1)

    def test_final_backup_failure_is_retryable_without_new_issuance(self):
        count = 0
        def fail_final(ledger):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("final upload failed")
            return self.backup(ledger)
        with self.assertRaises(RuntimeError):
            self.run_bootstrap(backup=fail_final)
        self.assertTrue(self.ledger["backup_required"])
        self.assertFalse(self.ledger["pending"])
        self.run_bootstrap()
        self.assertEqual(len(self.ledger["issued"]), 1)

    def test_untracked_secret_and_missing_completed_secret_stop(self):
        self.current["backup"] = "untracked"
        with self.assertRaises(ValueError):
            self.run_bootstrap()
        self.assertFalse(self.ledger["issued"])
        self.current["backup"] = None
        self.run_bootstrap()
        self.current["backup"] = None
        with self.assertRaises(ValueError):
            self.run_bootstrap()
        self.assertEqual(len(self.ledger["issued"]), 1)

    def test_wrong_csr_and_revoked_leaf_stop_before_delivery(self):
        original = self.remote
        def wrong_csr(operation, identity, certificate=None):
            return original(operation, "bedrock" if operation == "prepare" else identity, certificate)
        with self.assertRaises(ValueError):
            bootstrap.bootstrap_identity("backup", self.ledger, self.save, wrong_csr, self.backup,
                                         self.key, self.issuer, self.instant)
        self.assertFalse(self.ledger["issued"])
        result = self.run_bootstrap()
        bootstrap.issuer_tools.make_crl(self.ledger, self.key, self.issuer, self.instant, result["serial"])
        self.events.clear()
        with self.assertRaises(ValueError):
            self.run_bootstrap()
        self.assertNotIn("install", self.events)

    def test_transport_uses_strict_alias_and_redacts_errors(self):
        ops = bootstrap.HomeServerBootstrapOperations({"ssh_key": "/key"}, self.key, self.issuer)
        with patch.object(bootstrap.subprocess, "run") as run:
            run.return_value.returncode = 1
            run.return_value.stderr = b"PRIVATE"
            with self.assertRaisesRegex(RuntimeError, "^home-server bootstrap transport failed$"):
                ops.remote("install", "backup", "public cert")
            command = run.call_args.args[0]
            self.assertIn("home-server", command)
            self.assertIn("StrictHostKeyChecking=yes", command)
            self.assertNotIn("-F", command)
            self.assertEqual(run.call_args.kwargs["input"], b"public cert")

    def test_host_create_readback_and_lost_response_never_replace_or_rollout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "issuer.crt").write_text(bootstrap.issuer_tools.pem(self.issuer))
            state = {"secret": None, "lose_response": True}
            calls = []
            def kubectl(namespace, *args, payload=None):
                calls.append(args)
                if args[:2] == ("get", "namespace"):
                    return b"namespace/" + namespace.encode()
                if args[:2] == ("get", "secret"):
                    return json.dumps(state["secret"]).encode() if state["secret"] else b""
                if args[0] == "create":
                    if state["secret"]:
                        raise RuntimeError("AlreadyExists")
                    state["secret"] = json.loads(payload)
                    if state["lose_response"]:
                        state["lose_response"] = False
                        raise RuntimeError("lost response")
                    return b"created"
                raise AssertionError(args)
            with patch.object(host.leaf, "HOME_SERVER_ROOT", root), patch.object(host.leaf, "kubectl", kubectl):
                csr = host.bootstrap_leaf("prepare", "backup")["csr"]
                key_path = root / "backup/pending.key"
                self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
                cert = bootstrap.renew.issue(csr, "backup", self.key, self.issuer, self.instant).encode()
                with self.assertRaises(RuntimeError):
                    host.bootstrap_leaf("install", "backup", cert)
                self.assertTrue(key_path.exists())
                host.bootstrap_leaf("install", "backup", cert)
                self.assertFalse(key_path.exists())
                self.assertEqual(host.bootstrap_leaf("status", "backup")["certificate"], cert.decode())
                with self.assertRaises(ValueError):
                    host.bootstrap_leaf("install", "backup", b"different")
                self.assertEqual(sum(args[0] == "create" for args in calls), 1)
                self.assertFalse(any(args[0] in ("patch", "replace", "apply", "rollout") for args in calls))
                # Readback verifies the actual pair, not just the public cert.
                state["secret"]["data"]["tls.key"] = base64.b64encode(bootstrap.issuer_tools.private_pem(self.key)).decode()
                with self.assertRaises(ValueError):
                    host.bootstrap_leaf("status", "backup")

    def test_host_rbac_failure_is_not_absence(self):
        with patch.object(host.leaf, "kubectl", side_effect=RuntimeError("Forbidden")):
            with self.assertRaises(RuntimeError):
                host.current_secret("app", "home-server-bedrock-identity")

    def test_bootstrap_history_age_recovery_preserves_pending_and_revoked_entries(self):
        tools = bootstrap.issuer_tools
        self.run_bootstrap()
        self.fail_upload = True
        with self.assertRaises(RuntimeError):
            self.run_bootstrap("bedrock")
        serial = str(x509.load_pem_x509_certificate(self.current["backup"].encode()).serial_number)
        tools.make_crl(self.ledger, self.key, self.issuer, self.instant, serial)
        age_identity = tools.custody.run([shutil.which("age-keygen")])
        recipient = tools.custody.recipient(age_identity)
        config = {"age_binary": shutil.which("age"), "recovery_recipient": recipient,
                  "ca_sha256": self.ledger["ca_sha256"]}
        bundle = tools.canonical({"version": 2, "issuer_private_key": tools.private_pem(self.key).decode(),
                                  "issuer_certificate": tools.pem(self.issuer), "ledger": self.ledger,
                                  "recovery_recipient": recipient})
        ciphertext = tools.custody.run([config["age_binary"], "-r", recipient], bundle)
        digest = tools.sha(tools.canonical(self.ledger))
        _, _, recovered = tools.recover_bundle(ciphertext, age_identity, config, digest, self.instant)
        self.assertEqual(recovered, self.ledger)
        self.assertEqual(len(recovered["issued"]), 2)
        self.assertIn("bedrock", recovered["pending"])
        self.assertEqual(recovered["revoked"][0]["serial"], serial)
        with self.assertRaises(ValueError):
            tools.recover_bundle(ciphertext, age_identity, config, "0" * 64, self.instant)


if __name__ == "__main__":
    unittest.main()
