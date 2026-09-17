"""HM5 maintenance coordinator: disposable CA/leaf/CRL fixtures, fake tools, fake clock.
Never touches the Keychain, SSH, AWS, the home cluster or the real status file."""

import datetime
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("home_server_maintenance", ROOT / "home-server-maintenance.py")
mt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mt)

NOW = datetime.datetime(2026, 9, 17, 9, 15, tzinfo=datetime.timezone.utc)
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CA_NAME = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "driftplain-home-server-issuer-v1")])


def cert(cn, not_after, serial):
    return (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
            .issuer_name(CA_NAME).public_key(KEY.public_key()).serial_number(serial)
            .not_valid_before(NOW - datetime.timedelta(days=1)).not_valid_after(not_after)
            .sign(KEY, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode())


def crl_pem(number, next_update, revoked=()):
    builder = (x509.CertificateRevocationListBuilder().issuer_name(CA_NAME).last_update(NOW - datetime.timedelta(days=1))
               .next_update(next_update).add_extension(x509.CRLNumber(number), critical=False))
    for serial in revoked:
        builder = builder.add_revoked_certificate(x509.RevokedCertificateBuilder().serial_number(serial)
                                                  .revocation_date(NOW - datetime.timedelta(days=2)).build())
    return builder.sign(KEY, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()


def ledger(leaf_days=88, ca_days=700, crl_days=33, crl_number=3, pending=None, backup_required=False):
    return {"version": 2, "crl_number": crl_number, "revoked": [{"serial": "99", "reason": "key_compromise"}],
            "pending": pending or {}, "backup_required": backup_required,
            "issuer_certificate": cert("driftplain-home-server-issuer-v1", NOW + datetime.timedelta(days=ca_days), 1),
            "issued": [{"identity": "backup", "certificate": cert("driftplain-home-server-backup", NOW + datetime.timedelta(days=leaf_days), 10)},
                       {"identity": "bedrock", "certificate": cert("driftplain-home-server-bedrock", NOW + datetime.timedelta(days=leaf_days), 11)},
                       {"identity": "backup", "certificate": cert("driftplain-home-server-backup", NOW + datetime.timedelta(days=200), 99)}],
            "crl_pem": crl_pem(crl_number, NOW + datetime.timedelta(days=crl_days), revoked=[99])}


KEYS = [{"name": "sealed-secrets-keyh7gb7", "status": "active", "created": "2026-09-15T16:54:53Z", "cert_sha256": "aa" * 32}]


class FakeTools:
    def __init__(self, keys=KEYS, fail=None, root=None):
        self.calls, self.keys, self.fail, self.root = [], keys, fail, root

    def sealing(self, argv):
        self.calls.append(("sealing", *argv))
        if self.fail == ("sealing", argv[0]):
            raise mt.MaintenanceError("home-server-sealing-keys.py failed (exit 1): ERROR: transport")
        if argv[0] == "list":
            return {"target": {"context": "driftplain-home"}, "keys": self.keys}
        directory = self.root / "sealing-keys-20260917T091600Z"
        directory.mkdir()
        (directory / "manifest.json").write_text(json.dumps({"keys": self.keys}))
        (directory / "upload-receipt.json").write_text("{}")
        return {"backup_dir": str(directory), "keys": self.keys, "uploaded": {"sealing-keys.json.age": {}, "manifest.json": {}}}

    def issuer(self, argv):
        self.calls.append(("issuer", *argv))
        if argv[0] == "crl":
            new = self.ledger_path and json.loads(self.ledger_path.read_text())
            new["crl_number"] += 1
            new["crl_pem"] = crl_pem(new["crl_number"], NOW + datetime.timedelta(days=35), revoked=[99])
            self.ledger_path.write_text(json.dumps(new))
            (self.ledger_path.parent / f"issuer-crl-{new['crl_number']}.pem").write_text(new["crl_pem"])
            return {"command": "crl", "crl_number": new["crl_number"], "receipt": {"version_id": "v-new"}}
        return {"command": argv[0]}

    def renew(self):
        self.calls.append(("renew",))
        return "home-server renewal disabled; enrollment review required\n"

    def aws(self, argv):
        self.calls.append(("aws", *argv))
        return {"trustAnchor": {}} if argv[1] == "get-trust-anchor" else {"crl": {}}


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "backups"
        self.root.mkdir()
        backed = self.root / "sealing-keys-20260915T165752Z"
        backed.mkdir()
        (backed / "manifest.json").write_text(json.dumps({"keys": KEYS}))
        (backed / "upload-receipt.json").write_text("{}")
        self.ledger_path = self.tmp / "ledger.json"
        self.renewal = self.tmp / "renewal.json"
        self.renewal.write_text(json.dumps({"enabled": False, "ledger": str(self.ledger_path)}))
        self.status = self.tmp / "status.json"
        self.patches = [patch.object(mt, "STATUS_FILE", self.status), patch.object(mt, "RECORDS_DIR", self.tmp / "records")]
        for p in self.patches:
            p.start()
        self.notifications = []
        self.pings = []

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def config(self, **overrides):
        return mt.load_config(self.write_config(**overrides))

    def write_config(self, **overrides):
        path = self.tmp / "maintenance.json"
        path.write_text(json.dumps({"enabled": True, "renewal_config": str(self.renewal), "heartbeat_url": "https://hb.invalid/x", **overrides}))
        return path

    def execute(self, config, tools):
        tools.ledger_path = self.ledger_path
        tools.root = self.root
        out = io.StringIO()
        with patch("sys.stdout", out):
            code = mt.run_once(config, tools=tools, clock=lambda: NOW, heartbeat=lambda url: self.pings.append(url) or True,
                               notifier=lambda c: self.notifications.append(True), root=self.root)
        return code, json.loads(out.getvalue()), json.loads(self.status.read_text())

    def test_config_validation(self):
        with self.assertRaises(mt.MaintenanceError):
            self.config(crl_publish=True)
        with self.assertRaises(mt.MaintenanceError):
            self.config(crl_publish=True, crl_id="abc", trust_anchor_arn="arn:aws:rolesanywhere:us-east-1:1:trust-anchor/x")
        with self.assertRaises(mt.MaintenanceError):
            self.config(heartbeat_url="http://plain")
        with self.assertRaises(mt.MaintenanceError):
            self.config(bogus=1)
        self.assertEqual(self.config(crl_publish=True, crl_id="abc", trust_anchor_arn=mt.ANCHOR_PREFIX + "x")["crl_id"], "abc")

    def test_facts_and_grading_thresholds(self):
        facts = mt.ledger_facts(ledger(), NOW)
        self.assertEqual(facts["leaves"]["backup"]["days_left"], 88)  # the revoked 200-day leaf is ignored
        self.assertEqual(facts["crl"]["revoked_count"], 1)
        self.assertEqual(mt.grade(facts, {"cluster": KEYS, "unbacked": []}, False), [])
        soon = mt.grade(mt.ledger_facts(ledger(leaf_days=10, ca_days=100, crl_days=5), NOW), {"cluster": KEYS, "unbacked": []}, False)
        levels = {(f["level"], f["subject"]) for f in soon}
        self.assertIn(("critical", "leaf/backup"), levels)
        self.assertIn(("warning", "leaf/bedrock"), levels)  # renewal disabled note
        self.assertIn(("warning", "ca"), levels)
        self.assertIn(("warning", "crl"), levels)
        expired = mt.grade(mt.ledger_facts(ledger(crl_days=-1, ca_days=30), NOW), {"cluster": [], "unbacked": ["k"]}, True)
        self.assertEqual({f["subject"] for f in expired if f["level"] == "critical"}, {"crl", "ca", "sealing-keys"})

    def test_healthy_run_pings_and_only_runs_the_renewal_noop(self):
        self.ledger_path.write_text(json.dumps(ledger()))
        tools = FakeTools()
        code, out, status = self.execute(self.config(), tools)
        self.assertEqual((code, out["critical"], out["warnings"]), (0, 0, 0))
        self.assertEqual([c[0] for c in tools.calls], ["sealing", "renew"])
        self.assertEqual(self.pings, ["https://hb.invalid/x"])
        self.assertEqual(self.notifications, [])
        self.assertEqual(status["actions"], [{"action": "renewal", "enabled": False, "result": "home-server renewal disabled; enrollment review required"}])
        self.assertEqual(status["sealing_keys"]["backed_up_manifest"], "sealing-keys-20260915T165752Z")
        self.assertNotIn("BEGIN", json.dumps(status))

    def test_crl_due_refreshes_and_waits_for_the_manual_aws_update(self):
        self.ledger_path.write_text(json.dumps(ledger(crl_days=9)))
        tools = FakeTools()
        code, out, status = self.execute(self.config(), tools)
        self.assertEqual(code, 0)
        self.assertIn(("issuer", "crl"), tools.calls)
        self.assertNotIn("aws", [c[0] for c in tools.calls])
        self.assertEqual(status["facts"]["crl"]["number"], 4)
        self.assertEqual(status["actions"][0], {"action": "crl-refresh", "crl_number": 4, "receipt_version_id": "v-new", "published": False})
        self.assertEqual([f["subject"] for f in status["findings"]], ["crl"])
        self.assertEqual(status["findings"][0]["level"], "warning")
        self.assertEqual(self.pings, ["https://hb.invalid/x"])

    def test_crl_publish_updates_reads_back_and_verifies(self):
        self.ledger_path.write_text(json.dumps(ledger(crl_days=2)))
        tools = FakeTools()
        config = self.config(crl_publish=True, crl_id="crl-1", trust_anchor_arn=mt.ANCHOR_PREFIX + "anchor-1")
        code, out, status = self.execute(config, tools)
        self.assertEqual(code, 0)
        aws = [c for c in tools.calls if c[0] == "aws"]
        self.assertEqual([c[2] for c in aws], ["update-crl", "get-trust-anchor", "get-crl"])
        self.assertIn("fileb://" + str(self.tmp / "issuer-crl-4.pem"), aws[0])
        self.assertEqual(aws[1][4], "anchor-1")
        verify = [c for c in tools.calls if c[:2] == ("issuer", "verify-crl")][0]
        self.assertIn("--anchor-arn", verify)
        self.assertTrue(status["actions"][0]["published"])
        self.assertEqual(status["findings"], [])
        self.assertTrue((self.tmp / "records").is_dir())

    def test_new_sealing_key_triggers_a_backup(self):
        self.ledger_path.write_text(json.dumps(ledger()))
        new_key = {"name": "sealed-secrets-keyzzz", "status": "active", "created": "2026-10-15T17:00:00Z", "cert_sha256": "bb" * 32}
        tools = FakeTools(keys=KEYS + [new_key])
        code, out, status = self.execute(self.config(), tools)
        self.assertEqual(code, 0)
        self.assertEqual([c for c in tools.calls if c[0] == "sealing"], [("sealing", "list"), ("sealing", "backup")])
        self.assertEqual(status["actions"][0]["action"], "sealing-key-backup")
        self.assertEqual(status["sealing_keys"]["unbacked"], [])
        self.assertEqual(status["sealing_keys"]["backed_up_manifest"], "sealing-keys-20260917T091600Z")
        self.assertEqual(status["findings"], [])
        tools = FakeTools(keys=KEYS + [{**new_key, "cert_sha256": "cc" * 32}])
        code, out, status = self.execute(self.config(sealing_key_backup=False), tools)
        self.assertEqual(code, 0)
        self.assertEqual(out["critical"], 1)
        self.assertEqual(self.notifications, [True])
        self.assertEqual(self.pings, ["https://hb.invalid/x"])  # withheld on the second run
        self.assertFalse(status["last_heartbeat_ok"])
        self.assertTrue(status["last_heartbeat_withheld"])

    def test_tool_failure_records_notifies_and_withholds(self):
        self.ledger_path.write_text(json.dumps(ledger()))
        code, out, status = self.execute(self.config(), FakeTools(fail=("sealing", "list")))
        self.assertEqual((code, out["ok"]), (1, False))
        self.assertEqual(status["consecutive_failures"], 1)
        self.assertIn("transport", status["last_error"])
        self.assertEqual(self.notifications, [True])
        self.assertEqual(self.pings, [])
        problems = io.StringIO()
        with patch("sys.stdout", problems):
            self.assertEqual(mt.check(self.config(), clock=lambda: NOW), 1)
        self.assertIn("last run failed", json.loads(problems.getvalue())["problems"][1])

    def test_check_flags_stale_runs_and_standing_critical_findings(self):
        self.ledger_path.write_text(json.dumps(ledger(leaf_days=5)))
        code, out, status = self.execute(self.config(), FakeTools())
        self.assertEqual((code, out["critical"]), (0, 2))
        problems = io.StringIO()
        with patch("sys.stdout", problems):
            self.assertEqual(mt.check(self.config(), clock=lambda: NOW + datetime.timedelta(hours=1)), 1)
            self.assertEqual(mt.check(self.config(), clock=lambda: NOW + datetime.timedelta(hours=40)), 1)
        report = json.loads(problems.getvalue().split("}\n{")[0] + "}")
        self.assertTrue(any(p.startswith("critical leaf/backup") for p in report["problems"]))

    def test_disabled_config_fails_closed(self):
        self.ledger_path.write_text(json.dumps(ledger()))
        code, out, status = self.execute(self.config(enabled=False), FakeTools())
        self.assertEqual(code, 1)
        self.assertIn("disabled", status["last_error"])


if __name__ == "__main__":
    unittest.main()
