"""HM3 export/restore failure boundaries: fakes for transports, disposable DBs for the rest.

Unit tests never touch the network, Keychain or AWS. The rehearsal class runs a real
export -> encrypt -> decrypt -> restore -> compare between two throwaway postgres:16
containers with a throwaway age key; it is skipped when Docker is unavailable.
"""

import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


db = load("home_server_database", "home-server-database.py")

ROLES_DUMP = """--
-- Roles
--

CREATE ROLE cnpg_metrics_exporter;
ALTER ROLE cnpg_metrics_exporter WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS;
CREATE ROLE modelmatch;
ALTER ROLE modelmatch WITH NOSUPERUSER INHERIT CREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS PASSWORD 'SCRAM-SHA-256$4096:AAAA$BBBB:CCCC';
CREATE ROLE modelmatch_chat_ro;
ALTER ROLE modelmatch_chat_ro WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS PASSWORD 'SCRAM-SHA-256$4096:DDDD$EEEE:FFFF';
CREATE ROLE postgres;
ALTER ROLE postgres WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN REPLICATION BYPASSRLS;
CREATE ROLE streaming_replica;
ALTER ROLE streaming_replica WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN REPLICATION NOBYPASSRLS;

--
-- Role memberships
--

GRANT modelmatch_chat_ro TO modelmatch WITH ADMIN OPTION GRANTED BY postgres;
GRANT pg_monitor TO cnpg_metrics_exporter GRANTED BY postgres;
"""


def fake_fingerprint():
    return {"contract": {
        "server_version": "16.10", "database": {"name": "modelmatch", "owner": "modelmatch", "encoding": "UTF8",
                                                "collate": "C", "ctype": "C", "locale_provider": "c"},
        "alembic": ["a4b5c6d7e8f9"], "extensions": [["plpgsql", "1.0"]],
        "schema_public": {"owner": "modelmatch", "acl": ["modelmatch=UC/modelmatch"]},
        "relations": [["user", "r", "modelmatch", None]], "columns": [["user", 1, "id", "integer", "int4", "NO", None, None, 32, 0]],
        "constraints": [["user", "user_pkey", "p", "PRIMARY KEY (id)"]],
        "indexes": [["user", "user_pkey", "CREATE UNIQUE INDEX user_pkey ON public.user USING btree (id)"]],
        "views": [], "sequences": [["user_id_seq", "modelmatch", "integer", 1, 1, 2147483647, 1, False, 1, 9]],
        "enums": [], "default_acl": [],
        "roles": [{"name": "modelmatch", "super": False, "inherit": True, "createrole": True, "createdb": False,
                   "login": True, "replication": False, "bypassrls": False, "connlimit": -1, "validuntil": None,
                   "config": None, "password_kind": "scram-sha-256", "verifier_sha256": "aa" * 32},
                  {"name": "modelmatch_chat_ro", "super": False, "inherit": True, "createrole": False, "createdb": False,
                   "login": True, "replication": False, "bypassrls": False, "connlimit": -1, "validuntil": None,
                   "config": None, "password_kind": "scram-sha-256", "verifier_sha256": "bb" * 32}],
        "memberships": [["modelmatch_chat_ro", "modelmatch", "postgres", True, True, True]]},
        "tables": {"user": {"rows": 9, "digest": "d1"}, "project": {"rows": 11, "digest": "d2"}}}


class RolesFilterTests(unittest.TestCase):
    def test_keeps_only_application_roles_idempotently(self):
        out = db.filter_roles(ROLES_DUMP)
        self.assertIn("IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'modelmatch')", out)
        self.assertIn("PASSWORD 'SCRAM-SHA-256$4096:AAAA$BBBB:CCCC'", out)
        self.assertIn("GRANT modelmatch_chat_ro TO modelmatch WITH ADMIN OPTION GRANTED BY postgres;", out)
        for cluster_role in ("streaming_replica", "cnpg_metrics_exporter", "pg_monitor", " WITH SUPERUSER"):
            self.assertNotIn(cluster_role, out)
        self.assertNotIn("CREATE ROLE postgres", out)
        self.assertNotIn("\nCREATE ROLE ", out)  # every CREATE became create-if-missing

    def test_missing_application_role_is_refused(self):
        dump = ROLES_DUMP.replace("CREATE ROLE modelmatch_chat_ro;\n", "").replace(
            "ALTER ROLE modelmatch_chat_ro WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS PASSWORD 'SCRAM-SHA-256$4096:DDDD$EEEE:FFFF';\n", "")
        with self.assertRaisesRegex(db.DatabaseError, "lacks an application role: modelmatch_chat_ro"):
            db.filter_roles(dump)

    def test_missing_verifier_is_refused(self):
        dump = ROLES_DUMP.replace(" PASSWORD 'SCRAM-SHA-256$4096:AAAA$BBBB:CCCC'", "").replace(
            " PASSWORD 'SCRAM-SHA-256$4096:DDDD$EEEE:FFFF'", "")
        with self.assertRaisesRegex(db.DatabaseError, "no SCRAM verifier"):
            db.filter_roles(dump)

    def test_escalated_application_role_is_refused(self):
        dump = ROLES_DUMP.replace("ALTER ROLE modelmatch WITH NOSUPERUSER", "ALTER ROLE modelmatch WITH SUPERUSER")
        with self.assertRaisesRegex(db.DatabaseError, "forbidden attribute"):
            db.filter_roles(dump)
        dump = ROLES_DUMP + "GRANT pg_read_all_data TO modelmatch_chat_ro GRANTED BY postgres;\n"
        with self.assertRaisesRegex(db.DatabaseError, "crosses into a cluster role"):
            db.filter_roles(dump)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="hm3-manifest-"))
        self.addCleanup(shutil.rmtree, self.dir)
        (self.dir / "postgres.dump.age").write_bytes(b"cipher-a")
        (self.dir / "globals.sql.age").write_bytes(b"cipher-b")
        self.manifest = {"objects": {name: {"size": (self.dir / name).stat().st_size, "sha256": db.sha256_file(self.dir / name)}
                                     for name in ("postgres.dump.age", "globals.sql.age")}}

    def test_intact_copy_passes(self):
        self.assertTrue(db.verify_manifest(self.manifest, self.dir))

    def test_truncated_corrupt_or_missing_copy_is_refused(self):
        (self.dir / "postgres.dump.age").write_bytes(b"cipher")
        with self.assertRaisesRegex(db.DatabaseError, "incomplete copy"):
            db.verify_manifest(self.manifest, self.dir)
        (self.dir / "postgres.dump.age").write_bytes(b"cipher-X")
        with self.assertRaisesRegex(db.DatabaseError, "corrupt copy"):
            db.verify_manifest(self.manifest, self.dir)
        (self.dir / "postgres.dump.age").unlink()
        with self.assertRaisesRegex(db.DatabaseError, "missing object"):
            db.verify_manifest(self.manifest, self.dir)


class CompareTests(unittest.TestCase):
    def test_identical_fingerprints_match_and_publish_no_verifiers(self):
        result = db.compare_fingerprints(fake_fingerprint(), fake_fingerprint())
        self.assertTrue(result["match"])
        self.assertTrue(all(c["match"] for c in result["categories"].values()))
        self.assertEqual(result["tables"]["user"], {"source_rows": 9, "target_rows": 9, "rows_match": True, "content_match": True})
        self.assertNotIn("aa" * 32, json.dumps(result))
        self.assertNotIn("verifier_sha256", json.dumps(result))

    def test_each_contract_drift_is_named(self):
        cases = {
            "tables": lambda fp: fp["tables"]["user"].update(digest="other"),
            "sequences": lambda fp: fp["contract"]["sequences"][0].__setitem__(9, 8),
            "roles": lambda fp: fp["contract"]["roles"][1].update(verifier_sha256="cc" * 32),
            "constraints": lambda fp: fp["contract"]["constraints"].clear(),
            "alembic_version": lambda fp: fp["contract"].update(alembic=["ffffffffffff"]),
            "memberships": lambda fp: fp["contract"]["memberships"].clear(),
            "schema_public": lambda fp: fp["contract"]["schema_public"].update(owner="postgres"),
        }
        for category, mutate in cases.items():
            target = fake_fingerprint()
            mutate(target)
            result = db.compare_fingerprints(fake_fingerprint(), target)
            self.assertFalse(result["match"], category)
            self.assertFalse(result["categories"][category]["match"], category)
            others = [k for k, v in result["categories"].items() if not v["match"]]
            self.assertEqual(others, [category])

    def test_same_row_count_with_different_content_is_a_mismatch(self):
        target = fake_fingerprint()
        target["tables"]["project"]["digest"] = "changed"
        result = db.compare_fingerprints(fake_fingerprint(), target)
        self.assertTrue(result["tables"]["project"]["rows_match"])
        self.assertFalse(result["tables"]["project"]["content_match"])
        self.assertFalse(result["match"])

    def test_missing_role_or_table_is_a_mismatch(self):
        target = fake_fingerprint()
        target["contract"]["roles"].pop()
        del target["tables"]["project"]
        result = db.compare_fingerprints(fake_fingerprint(), target)
        self.assertEqual(result["categories"]["roles"]["detail"]["modelmatch_chat_ro"], {"present": False})
        self.assertFalse(result["tables"]["project"]["rows_match"])


class FakeShell:
    def __init__(self, argv):
        self.argv = argv

    def popen(self, argv, stdin, stdout, stderr):
        return subprocess.Popen(self.argv, stdin=stdin, stdout=stdout, stderr=stderr)

    def run(self, argv, payload=None, timeout=300, error_file=None):
        return b"5\n"


class SessionAndRefusalTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")).start()

    def test_stalled_session_times_out_instead_of_hanging(self):
        session = db.PsqlSession(FakeShell(["sleep", "30"]), "modelmatch", timeout=1)
        with self.assertRaisesRegex(db.DatabaseError, "TIMEOUT"):
            session.query("SELECT 1;")
        self.assertIsNotNone(session.process.poll())

    def test_session_that_exits_is_an_error_not_an_answer(self):
        session = db.PsqlSession(FakeShell(["true"]), "modelmatch", timeout=5)
        with self.assertRaisesRegex(db.DatabaseError, "ended before answering"):
            session.query("SELECT 1;")

    def test_session_returns_one_line_per_query(self):
        session = db.PsqlSession(FakeShell(["cat"]), "modelmatch", timeout=5)
        self.assertEqual(session.query("first"), "first")
        self.assertEqual(session.query("second"), "second")
        session.close(kill=True)

    def test_snapshot_and_identifier_validation(self):
        self.assertTrue(db.SNAPSHOT_RE.match("00000003-0000001B-1"))
        self.assertFalse(db.SNAPSHOT_RE.match("00000003-0000001B-1; DROP TABLE x"))
        with self.assertRaisesRegex(db.DatabaseError, "unexpected table identifier"):
            db.table_digest_sql(['user"; DROP TABLE project; --'])

    def test_restore_refuses_a_non_empty_target(self):
        with self.assertRaisesRegex(db.DatabaseError, "already contains relations"):
            db.run_restore(FakeShell(["true"]), b"AGE-SECRET-KEY-1" + b"Q" * 58 + b"\n", Path("/nonexistent"), 5)

    def test_target_pod_shell_quotes_sql_for_the_remote_shell(self):
        # ssh joins trailing arguments with spaces for the remote shell: SQL with quotes and
        # parentheses must arrive as ONE psql argument there, never as shell syntax (exit 2).
        import shlex
        prefix = db.ssh_prefix(["sudo", "-n", "kubectl", "exec", "-i", "pod", "--"])
        sql = "SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace;"
        remote = db.PodShell(prefix, "target", remote=True).command(["psql", "-At", "-c", sql])
        self.assertEqual(remote[:-1], prefix[:-1])
        self.assertEqual(shlex.split(remote[-1]),
                         ["sudo", "-n", "kubectl", "exec", "-i", "pod", "--", "psql", "-At", "-c", sql])
        local = db.PodShell(["kubectl", "exec", "--"], "source").command(["psql", "-c", sql])
        self.assertEqual(local, ["kubectl", "exec", "--", "psql", "-c", sql])

    def test_wrong_source_or_target_context_is_refused(self):
        wrong = {"contexts": [{"name": "orbstack"}], "clusters": [{"cluster": {"server": "https://127.0.0.1:26443"}}]}
        with patch.object(db, "kube_json", return_value=wrong):
            with self.assertRaisesRegex(db.DatabaseError, "not the expected one"):
                db.source_shell()
            with self.assertRaisesRegex(db.DatabaseError, "not driftplain-home"):
                db.target_shell()
        aws_as_home = {"contexts": [{"name": "driftplain-home"}],
                       "clusters": [{"cluster": {"server": "https://ABC.yl4.ap-south-1.eks.amazonaws.com"}}]}
        with patch.object(db, "kube_json", return_value=aws_as_home):
            with self.assertRaisesRegex(db.DatabaseError, "not the loopback endpoint"):
                db.target_shell()

    def test_upload_refuses_oversized_or_changed_objects(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "postgres.dump.age"
            path.write_bytes(b"cipher")
            with self.assertRaisesRegex(db.DatabaseError, "changed since the manifest"):
                db.put_object(None, path, "k", "0" * 64)
            with patch.object(Path, "stat") as stat:
                stat.return_value.st_size = db.MAX_SINGLE_PUT + 1
                with self.assertRaisesRegex(db.DatabaseError, "single-PUT limit"):
                    db.put_object(None, path, "k")


def docker_available():
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


SOURCE_SEED = r"""
CREATE ROLE modelmatch WITH LOGIN CREATEROLE PASSWORD 'owner-pw';
CREATE DATABASE modelmatch OWNER modelmatch;
\connect modelmatch
ALTER SCHEMA public OWNER TO modelmatch;
SET ROLE modelmatch;
CREATE ROLE modelmatch_chat_ro WITH LOGIN PASSWORD 'chat-pw';
CREATE TYPE task_kind AS ENUM ('review', 'security_analysis');
CREATE TABLE alembic_version (version_num varchar(32) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num));
INSERT INTO alembic_version VALUES ('a4b5c6d7e8f9');
CREATE TABLE "user" (id serial PRIMARY KEY, email varchar(320) UNIQUE NOT NULL, password_hash varchar(255),
                     google_subject varchar(255) UNIQUE, is_operator boolean NOT NULL DEFAULT false);
CREATE TABLE project (id serial PRIMARY KEY, user_id integer NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                      name varchar(200) NOT NULL, task task_kind NOT NULL DEFAULT 'review', created_at timestamptz NOT NULL DEFAULT now(),
                      budget numeric(14,6), CONSTRAINT uq_project_user_name UNIQUE (user_id, name));
CREATE TABLE jenkins_connection (id serial PRIMARY KEY, project_id integer UNIQUE REFERENCES project(id) ON DELETE CASCADE,
                                 ci_token_hash varchar(64), payload bytea);
CREATE INDEX ix_project_created ON project (created_at);
CREATE VIEW chat_catalog AS SELECT id, name FROM project;
GRANT USAGE ON SCHEMA public TO modelmatch_chat_ro;
GRANT SELECT ON chat_catalog TO modelmatch_chat_ro;
INSERT INTO "user" (email, password_hash, google_subject) VALUES
  ('a@example.test', '$argon2id$v=19$m=65536,t=3,p=4$fake1', NULL),
  ('b@example.test', NULL, '1002003004005006007'),
  ('c@example.test', '$argon2id$v=19$m=65536,t=3,p=4$fake3', NULL);
INSERT INTO project (user_id, name, task, budget) VALUES (1, 'demo-api', 'review', 12.5), (1, 'demo-sec', 'security_analysis', NULL), (2, 'other', 'review', 0.000001);
INSERT INTO jenkins_connection (project_id, ci_token_hash, payload) VALUES (1, repeat('ab', 32), '\x00ff10'), (2, repeat('cd', 32), NULL);
DELETE FROM project WHERE id = 3;
RESET ROLE;
"""

# What CNPG initdb + postInitApplicationSQL produce on the empty home target: the owner
# exists with the SAME password but a freshly salted verifier, CREATEROLE, owns public.
TARGET_SEED = r"""
CREATE ROLE modelmatch WITH LOGIN PASSWORD 'owner-pw';
CREATE DATABASE modelmatch OWNER modelmatch;
\connect modelmatch
ALTER ROLE modelmatch CREATEROLE;
ALTER SCHEMA public OWNER TO modelmatch;
"""


@unittest.skipUnless(docker_available(), "Docker is required for the disposable-database rehearsal")
class DockerRehearsalTests(unittest.TestCase):
    """A real export -> encrypt -> independent decrypt -> restore -> compare, on throwaway DBs."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="hm3-rehearsal-"))
        os.chmod(cls.dir, 0o700)
        identity = subprocess.run(["age-keygen"], capture_output=True, check=True).stdout
        cls.identity = b"".join(line + b"\n" for line in identity.splitlines() if line.startswith(b"AGE-SECRET-KEY-1"))
        cls.recipient = subprocess.run(["age-keygen", "-y"], input=cls.identity, capture_output=True, check=True).stdout.decode().strip()
        cls.containers = {}
        for side, seed in (("source", SOURCE_SEED), ("target", TARGET_SEED)):
            name = subprocess.run(["docker", "run", "-d", "--rm", "-e", "POSTGRES_PASSWORD=throwaway", "postgres:16"],
                                  capture_output=True, check=True, text=True).stdout.strip()
            cls.containers[side] = name
            for _ in range(60):
                if subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres"], capture_output=True).returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("TIMEOUT: disposable postgres did not become ready")
            time.sleep(2)  # the official image restarts once after initdb
            subprocess.run(["docker", "exec", "-i", name, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-q"],
                           input=seed.encode(), capture_output=True, check=True)
        cls.source = db.PodShell(["docker", "exec", "-i", cls.containers["source"]], "source")
        cls.target = db.PodShell(["docker", "exec", "-i", cls.containers["target"]], "target")

    @classmethod
    def tearDownClass(cls):
        for name in cls.containers.values():
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        shutil.rmtree(cls.dir)

    def test_export_restore_compare_then_drift_detection(self):
        export_dir = self.dir / "export"
        export_dir.mkdir(mode=0o700)
        outcome = db.run_export(self.source, self.recipient, export_dir, timeout=120)
        self.assertTrue(db.SNAPSHOT_RE.match(outcome["snapshot"]))
        counts = db.public_counts(outcome["fingerprint"])
        self.assertEqual(counts, {"alembic_version": 1, "jenkins_connection": 2, "project": 2, "user": 3})
        self.assertEqual(outcome["fingerprint"]["contract"]["alembic"], ["a4b5c6d7e8f9"])
        for name in ("postgres.dump.age", "globals.sql.age", "fingerprint.json.age"):
            self.assertTrue((export_dir / name).stat().st_size > 0)
            self.assertEqual(oct((export_dir / name).stat().st_mode & 0o777), "0o600")
            self.assertTrue((export_dir / name).read_bytes().startswith(b"age-encryption.org/v1"))

        # A copy of the encrypted objects stands in for the independent S3 download.
        restore_dir = self.dir / "restore"
        restore_dir.mkdir(mode=0o700)
        for name in ("postgres.dump.age", "globals.sql.age", "fingerprint.json.age"):
            shutil.copy2(export_dir / name, restore_dir / name)
        result = db.run_restore(self.target, self.identity, restore_dir, timeout=120)
        self.assertTrue(result["comparison"]["match"], json.dumps(result["comparison"], indent=1))
        roles = result["comparison"]["categories"]["roles"]["detail"]
        self.assertTrue(roles["modelmatch"]["verifier_match"], "original SCRAM verifier replaced initdb's")
        self.assertTrue(roles["modelmatch_chat_ro"]["verifier_match"])
        self.assertEqual({k: v["source_rows"] for k, v in result["comparison"]["tables"].items()}, counts)
        self.assertEqual(result["comparison"]["categories"]["sequences"]["detail"]["mismatched"], [])
        # Nothing decrypted was written to the restore directory; diagnostics files are private.
        self.assertEqual(sorted(p.name for p in restore_dir.iterdir()),
                         ["fingerprint.json.age", "globals.sql.age", "pg_restore.stderr", "postgres.dump.age", "psql.stderr"])
        for name in ("pg_restore.stderr", "psql.stderr"):
            self.assertEqual((restore_dir / name).stat().st_size, 0, name)
            self.assertEqual(oct((restore_dir / name).stat().st_mode & 0o777), "0o600")

        # The original passwords still authenticate on the restored target, with the same fence.
        auth = subprocess.run(["docker", "exec", "-e", "PGPASSWORD=owner-pw", self.containers["target"], "psql", "-h", "127.0.0.1",
                               "-U", "modelmatch", "-d", "modelmatch", "-At", "-c", "SELECT count(*) FROM chat_catalog"],
                              capture_output=True, timeout=30)
        self.assertEqual((auth.returncode, auth.stdout.strip()), (0, b"2"))
        denied = subprocess.run(["docker", "exec", "-e", "PGPASSWORD=chat-pw", self.containers["target"], "psql", "-h", "127.0.0.1",
                                 "-U", "modelmatch_chat_ro", "-d", "modelmatch", "-At", "-c", 'SELECT count(*) FROM "user"'],
                                capture_output=True, timeout=30)
        self.assertNotEqual(denied.returncode, 0, "chat role must still be fenced from base tables")

        # A second restore into the now-populated target is refused before touching anything.
        with self.assertRaisesRegex(db.DatabaseError, "already contains relations"):
            db.run_restore(self.target, self.identity, restore_dir, timeout=120)

        # Drift after the snapshot is detected, not hidden behind row counts.
        self.target.run(["psql", "-U", "postgres", "-d", "modelmatch", "-v", "ON_ERROR_STOP=1", "-c",
                         "UPDATE project SET budget = 99 WHERE id = 1; SELECT setval('user_id_seq', 50, true);"], timeout=30)
        session = db.PsqlSession(self.target, "modelmatch", timeout=60)
        try:
            session.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;")
            drifted = db.fingerprint(session)
        finally:
            session.close()
        comparison = db.compare_fingerprints(outcome["fingerprint"], drifted)
        self.assertFalse(comparison["match"])
        self.assertEqual(comparison["tables"]["project"], {"source_rows": 2, "target_rows": 2, "rows_match": True, "content_match": False})
        self.assertEqual(comparison["categories"]["sequences"]["detail"]["mismatched"], ["user_id_seq"])
        self.assertEqual([k for k, v in comparison["categories"].items() if not v["match"]], ["sequences", "tables"])

    def test_corrupted_ciphertext_never_reaches_the_target(self):
        broken = self.dir / "broken"
        broken.mkdir(mode=0o700)
        cipher = subprocess.run(["age", "--encrypt", "-r", self.recipient], input=b"not a dump", capture_output=True, check=True).stdout
        (broken / "postgres.dump.age").write_bytes(cipher[:-8] + b"XXXXXXXX")
        with self.assertRaisesRegex(db.DatabaseError, "streaming restore failed"):
            db.decrypt_into(self.identity, broken / "postgres.dump.age", self.target,
                            ["pg_restore", "-U", "postgres", "-d", "postgres", "--exit-on-error", "--single-transaction"],
                            60, broken / "pg_restore.stderr")


if __name__ == "__main__":
    unittest.main()
