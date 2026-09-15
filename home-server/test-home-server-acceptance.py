"""Focused disposable test-leaf journaling tests; no native custody or AWS."""
import copy
from datetime import datetime, timezone, timedelta
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from cryptography import x509

ROOT = Path(__file__).resolve().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = load("bootstrap_fixtures", "test-home-server-bootstrap.py")
acceptance = load("acceptance_issuer", "home-server-acceptance-issuer.py")
host = load("acceptance_host", "home-server-acceptance-host.py")


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.BootstrapTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.BootstrapTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.csr = self.fixture.remote("prepare", "backup")["csr"]

    def issue(self, probe="revocation", csr=None):
        f = self.fixture
        return acceptance.issue_probe(probe, csr or self.csr, f.ledger, f.save, f.backup,
                                      f.key, f.issuer, f.instant)

    def test_backup_failure_reuses_recorded_serial(self):
        self.fixture.fail_upload = True
        with self.assertRaises(RuntimeError):
            self.issue()
        before = copy.deepcopy(self.fixture.ledger)
        self.fixture.fail_upload = False
        cert, _ = self.issue()
        self.assertEqual(self.fixture.ledger, before)
        self.assertEqual(cert, before["issued"][0]["certificate"])
        self.assertFalse(before["pending"])

    def test_two_probes_preserve_workload_history_and_revocations(self):
        self.fixture.run_bootstrap("backup")
        self.fixture.run_bootstrap("bedrock")
        workloads = copy.deepcopy(self.fixture.ledger["issued"])
        cert, _ = self.issue("crl-bootstrap")
        serial = str(x509.load_pem_x509_certificate(cert.encode()).serial_number)
        acceptance.issuer_tools.make_crl(self.fixture.ledger, self.fixture.key, self.fixture.issuer,
                                         self.fixture.instant, serial)
        self.issue("revocation")
        self.assertEqual(self.fixture.ledger["issued"][:2], workloads)
        self.assertEqual(len(self.fixture.ledger["issued"]), 4)
        self.assertEqual(self.fixture.ledger["revoked"][0]["serial"], serial)
        self.assertFalse(self.fixture.ledger["pending"])

    def test_conflicting_csr_and_pending_delivery_stop(self):
        self.issue()
        wrong = self.fixture.remote("prepare", "bedrock")["csr"]
        with self.assertRaises(ValueError):
            self.issue(csr=wrong)
        self.fixture.ledger["pending"]["backup"] = {k: v for k, v in self.fixture.ledger["issued"][0].items() if k != "identity"}
        with self.assertRaises(ValueError):
            self.issue("crl-bootstrap")

    def helper_test(self, response, denied):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryFile() as backing:
            root = Path(folder)
            (root / 'aws_signing_helper').write_bytes(b'fixture-helper')
            metadata = {'backup': {'role_arn': 'role', 'profile_arn': 'profile', 'trust_anchor_arn': 'anchor'}}
            with patch.object(host, 'ROOT', root), patch.object(host, 'HELPER_SHA256', hashlib.sha256(b'fixture-helper').hexdigest()), \
                 patch.object(host, 'material', return_value=(b'PUBLIC', b'PRIVATE_SENTINEL')), \
                 patch.object(host.os, 'memfd_create', create=True, side_effect=lambda *a: os.dup(backing.fileno())), \
                 patch.object(host.os, 'MFD_CLOEXEC', 1, create=True), \
                 patch.object(host.subprocess, 'run', return_value=response) as execute:
                value = host.exchange('backup', metadata, expect_denied=denied)
                command = execute.call_args.args[0]
                self.assertNotIn('PRIVATE_SENTINEL', ' '.join(command))
                self.assertEqual(len(execute.call_args.kwargs['pass_fds']), 2)
                for fd in execute.call_args.kwargs['pass_fds']:
                    with self.assertRaises(OSError):
                        os.fstat(fd)
                return value

    def test_helper_denial_is_not_a_transport_failure(self):
        denied = SimpleNamespace(returncode=1, stdout=b'', stderr=b'AccessDeniedException: PRIVATE_SENTINEL')
        self.assertEqual(self.helper_test(denied, True), {'denied': True, 'aws_error': 'AccessDeniedException'})
        broken = SimpleNamespace(returncode=1, stdout=b'', stderr=b'network unavailable PRIVATE_SENTINEL')
        with self.assertRaises(RuntimeError):
            self.helper_test(broken, True)

    def test_unexpected_session_is_not_a_denial(self):
        credentials = {'Version': 1, 'AccessKeyId': 'fixture', 'SecretAccessKey': 'fixture', 'SessionToken': 'fixture',
                       'Expiration': (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()}
        response = SimpleNamespace(returncode=0, stdout=json.dumps(credentials).encode(), stderr=b'')
        self.assertEqual(self.helper_test(response, False), credentials)
        with self.assertRaises(ValueError):
            self.helper_test(response, True)
        with self.assertRaises(ValueError):
            host.client('bedrock-runtime', credentials)


if __name__ == "__main__":
    unittest.main()
