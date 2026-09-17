#!/usr/bin/env python3
"""E21/HM3+HM5 — consistent encrypted PostgreSQL export and independent private restore.

Operator-only, run on the Mac. Sources: the AWS CNPG primary (explicit EKS context,
HM3/HM7) or the one home CNPG instance over strict-key `ssh home-server` + `sudo -n`
(HM5 hourly/daily schedule). Targets: the production home instance (must be EMPTY) or
a disposable one-instance Cluster in its own namespace for periodic restore checks.
Every step is bounded, refuses the wrong side, and keeps plaintext only in pipes:

  export   one REPEATABLE READ snapshot: pg_dump --snapshot + the same-snapshot
           fingerprint + global roles (+ the original K8s credential Secrets on the
           daily tier), each age-encrypted to the public recipient; a public manifest.
  upload   single PUT per object with SHA-256 checksums; exact version receipts under
           postgres/<tier>/<origin>-<stamp>/ (credentials under recovery/app-credentials/).
  download an INDEPENDENT copy by key+version into a separate directory; verified.
  restore  roles (app roles only, original verifiers) -> pg_restore into an EMPTY
           target -> fingerprint -> full comparison against the export's snapshot.
  disposable-target create|delete   the throwaway check Cluster (bounded waits).

No values, rows, hashes, dumps or keys reach stdout/argv/Git. Subprocess output is
suppressed; pg_restore diagnostics go to a 0600 file in the private restore directory.
"""

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent

SOURCE_CONTEXT = "arn:aws:eks:ap-south-1:957261948820:cluster/modelmatch"
SOURCE_SERVER_SUFFIX = ".eks.amazonaws.com"
TARGET_SSH_ALIAS = "home-server"
TARGET_KUBECONFIG = "/home/steve/.kube/driftplain-home.yaml"
TARGET_CONTEXT = "driftplain-home"
TARGET_SERVER = "https://127.0.0.1:6443"
TARGET_NODE = "driftplain-home"

NAMESPACE = "app"
CLUSTER = "modelmatch-postgres"
DATABASE = "modelmatch"
OWNER_ROLE = "modelmatch"
CHAT_ROLE = "modelmatch_chat_ro"
APP_ROLES = (OWNER_ROLE, CHAT_ROLE)
OWNER_SECRET = "modelmatch-db-app"
APP_SECRET = "modelmatch-app-secrets"
CREDENTIAL_SECRETS = (APP_SECRET, OWNER_SECRET)

BUCKET = "modelmatch-home-server-backups-957261948820"
REGION = "ap-south-1"
OPERATOR_ARN = "arn:aws:iam::957261948820:user/steve"
OPERATOR_PROFILE = "saa"
RECIPIENT_FILE = ROOT / "recovery-key-v1.recipient"
RECOVERY_SECRET_ID = "modelmatch/home-server/recovery-key-v1"
KEYCHAIN = Path("/Users/steve/Library/Keychains/login.keychain-db")
KEYCHAIN_ACCOUNT = "steve"
MAX_SINGLE_PUT = 5 * 1024 ** 3

# HM5: scheduled home exports land under postgres/<tier>/; a disposable CNPG target on the
# home node (throwaway namespace, Delete-class storage) hosts periodic restore checks.
TIERS = ("hourly", "daily")
DISPOSABLE_NAMESPACE = "home-server-restore-check"
DISPOSABLE_CLUSTER = "restore-check"
DISPOSABLE_STORAGE_CLASS = "local-path"  # reclaimPolicy Delete: the check leaves nothing behind
DISPOSABLE_WAIT_SECONDS = 600

BACKUPS_ROOT = Path.home() / ".local" / "share" / "driftplain" / "home-server-backups"

SNAPSHOT_RE = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{8}-[0-9]+$")
LSN_RE = re.compile(r"^[0-9A-F]+/[0-9A-F]+$")
IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

OBJECTS = ("postgres.dump.age", "globals.sql.age", "fingerprint.json.age", "app-credentials.json.age")
RESTORE_OBJECTS = OBJECTS[:3]  # a restore never needs the credential bundle

# Read/output settings that make `row::text` deterministic across the two servers.
SESSION_SETTINGS = ("SET LOCAL TimeZone = 'UTC';", "SET LOCAL DateStyle = 'ISO, YMD';",
                    "SET LOCAL IntervalStyle = 'postgres';", "SET LOCAL extra_float_digits = 3;",
                    "SET LOCAL bytea_output = 'hex';", "SET LOCAL search_path = pg_catalog;")

TABLE_LIST_SQL = ("SELECT coalesce(json_agg(relname ORDER BY relname), '[]'::json) FROM pg_class "
                  "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r';")

CONTRACT_SQL = """
SELECT json_build_object(
  'server_version', current_setting('server_version'),
  'database', (SELECT json_build_object('name', datname, 'owner', datdba::regrole::text,
       'encoding', pg_encoding_to_char(encoding), 'collate', datcollate, 'ctype', datctype,
       'locale_provider', datlocprovider::text)
     FROM pg_database WHERE datname = current_database()),
  'alembic', (SELECT coalesce(json_agg(version_num ORDER BY version_num), '[]'::json)
     FROM public.alembic_version),
  'extensions', (SELECT coalesce(json_agg(json_build_array(extname, extversion) ORDER BY extname), '[]'::json)
     FROM pg_extension),
  'schema_public', (SELECT json_build_object('owner', nspowner::regrole::text, 'acl', nspacl::text[])
     FROM pg_namespace WHERE nspname = 'public'),
  'relations', (SELECT coalesce(json_agg(json_build_array(relname, relkind::text, relowner::regrole::text, relacl::text[])
       ORDER BY relname), '[]'::json)
     FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r', 'v', 'S', 'm')),
  'columns', (SELECT coalesce(json_agg(json_build_array(table_name, ordinal_position, column_name, data_type, udt_name,
       is_nullable, column_default, character_maximum_length, numeric_precision, numeric_scale)
       ORDER BY table_name, ordinal_position), '[]'::json)
     FROM information_schema.columns WHERE table_schema = 'public'),
  'constraints', (SELECT coalesce(json_agg(json_build_array(conrelid::regclass::text, conname, contype::text,
       pg_get_constraintdef(oid)) ORDER BY conrelid::regclass::text, conname), '[]'::json)
     FROM pg_constraint WHERE connamespace = 'public'::regnamespace),
  'indexes', (SELECT coalesce(json_agg(json_build_array(tablename, indexname, indexdef) ORDER BY tablename, indexname), '[]'::json)
     FROM pg_indexes WHERE schemaname = 'public'),
  'views', (SELECT coalesce(json_agg(json_build_array(viewname, viewowner, definition) ORDER BY viewname), '[]'::json)
     FROM pg_views WHERE schemaname = 'public'),
  'sequences', (SELECT coalesce(json_agg(json_build_array(sequencename, sequenceowner, data_type::text, start_value,
       min_value, max_value, increment_by, cycle, cache_size, last_value) ORDER BY sequencename), '[]'::json)
     FROM pg_sequences WHERE schemaname = 'public'),
  'enums', (SELECT coalesce(json_agg(json_build_array(t.typname, e.enumsortorder, e.enumlabel)
       ORDER BY t.typname, e.enumsortorder), '[]'::json)
     FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typnamespace = 'public'::regnamespace),
  'default_acl', (SELECT coalesce(json_agg(json_build_array(defaclrole::regrole::text, defaclnamespace::regnamespace::text,
       defaclobjtype::text, defaclacl::text[]) ORDER BY defaclrole::regrole::text, defaclobjtype::text), '[]'::json)
     FROM pg_default_acl),
  'roles', (SELECT coalesce(json_agg(json_build_object('name', rolname, 'super', rolsuper, 'inherit', rolinherit,
       'createrole', rolcreaterole, 'createdb', rolcreatedb, 'login', rolcanlogin, 'replication', rolreplication,
       'bypassrls', rolbypassrls, 'connlimit', rolconnlimit, 'validuntil', rolvaliduntil,
       'config', (SELECT r.rolconfig FROM pg_roles r WHERE r.oid = a.oid),
       'password_kind', CASE WHEN rolpassword IS NULL THEN 'none'
                             WHEN rolpassword LIKE 'SCRAM-SHA-256$%' THEN 'scram-sha-256' ELSE 'other' END,
       'verifier_sha256', encode(sha256(convert_to(coalesce(rolpassword, ''), 'UTF8')), 'hex'))
       ORDER BY rolname), '[]'::json)
     FROM pg_authid a WHERE rolname IN ('modelmatch', 'modelmatch_chat_ro')),
  'memberships', (SELECT coalesce(json_agg(json_build_array(roleid::regrole::text, member::regrole::text, grantor::regrole::text,
       admin_option, inherit_option, set_option) ORDER BY roleid::regrole::text, member::regrole::text), '[]'::json)
     FROM pg_auth_members WHERE roleid::regrole::text IN ('modelmatch', 'modelmatch_chat_ro')
        OR member::regrole::text IN ('modelmatch', 'modelmatch_chat_ro'))
);
""".strip()

RELATION_COUNT_SQL = ("SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                      "AND relkind IN ('r', 'v', 'S', 'm');")


class DatabaseError(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def stamp(instant):
    return instant.strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def write_private(path, data):
    with open(path, "xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def load_custody():
    spec = importlib.util.spec_from_file_location("home_server_recovery_key", ROOT / "recovery-key.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── transports ────────────────────────────────────────────────────────────────

class PodShell:
    """Runs argv inside the `postgres` container of one CNPG pod through a fixed prefix.

    Output is captured, never relayed: it can contain rows or credential verifiers.
    """

    def __init__(self, prefix, label, remote=False):
        self.prefix = list(prefix)
        self.label = label
        # remote: the prefix ends with an ssh remote command string; ssh joins any further
        # arguments with spaces and hands the result to the remote shell, so they must be
        # shell-quoted there (SQL with quotes/parentheses would otherwise be parsed as shell).
        self.remote = remote

    def command(self, argv):
        if self.remote:
            return self.prefix[:-1] + [self.prefix[-1] + " " + shlex.join(argv)]
        return self.prefix + list(argv)

    def run(self, argv, payload=None, timeout=300, error_file=None):
        try:
            result = subprocess.run(self.command(argv), input=payload, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise DatabaseError(f"TIMEOUT: {self.label} {argv[0]} exceeded {timeout}s") from None
        if result.returncode:
            if error_file is not None:
                write_private(error_file, result.stderr)
            raise DatabaseError(f"{self.label} {argv[0]} failed (exit {result.returncode}); output suppressed")
        return result.stdout

    def popen(self, argv, stdin, stdout, stderr):
        return subprocess.Popen(self.command(argv), stdin=stdin, stdout=stdout, stderr=stderr)


def kubectl_prefix(context, kubeconfig=None):
    argv = ["kubectl"]
    if kubeconfig:
        argv += ["--kubeconfig", kubeconfig]
    argv += ["--context", context, "--request-timeout=60s"]
    return argv


def ssh_prefix(remote_argv):
    # ssh concatenates every argument after the host into one remote command line, so
    # trailing args appended by callers (e.g. `-o json`) still reach the remote kubectl.
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", TARGET_SSH_ALIAS, shlex.join(remote_argv)]


class PsqlSession:
    """One long-lived `psql` in a pod; each query returns exactly one line (JSON/scalar).

    Used to hold the exported snapshot open and to compute fingerprints inside it.
    Reads are bounded: a stall raises TIMEOUT instead of hanging.
    """

    def __init__(self, shell, database, timeout=120, error_file=None):
        self.timeout = timeout
        # Diagnostics never reach the terminal; they go to a 0600 file if one is given.
        self.errors = open(error_file, "ab", opener=lambda p, f: os.open(p, f, 0o600)) if error_file else None
        self.process = shell.popen(["psql", "-U", "postgres", "-d", database, "-At", "-q",
                                    "-v", "ON_ERROR_STOP=1", "-X"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=self.errors or subprocess.DEVNULL)
        self.buffer = b""

    def execute(self, sql):
        self.process.stdin.write(sql.encode() + b"\n")
        self.process.stdin.flush()

    def query(self, sql):
        self.execute(sql)
        deadline = time.monotonic() + self.timeout
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close(kill=True)
                raise DatabaseError("TIMEOUT: psql session produced no result line")
            ready, _, _ = select.select([self.process.stdout], [], [], min(remaining, 5))
            if ready:
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    self.close(kill=True)
                    raise DatabaseError("psql session ended before answering; output suppressed")
                self.buffer += chunk
        line, _, self.buffer = self.buffer.partition(b"\n")
        return line.decode()

    def close(self, kill=False):
        if self.process.poll() is None:
            try:
                if not kill:
                    self.process.stdin.write(b"COMMIT;\n\\q\n")
                    self.process.stdin.flush()
                    self.process.wait(timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                kill = True
            if kill or self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=30)
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except OSError:
                pass
        if self.errors:
            self.errors.close()
            self.errors = None


# ── source/target discovery and refusal ───────────────────────────────────────

def kube_json(prefix, args):
    try:
        out = subprocess.run(prefix + list(args) + ["-o", "json"], capture_output=True, timeout=90)
    except subprocess.TimeoutExpired:
        raise DatabaseError("TIMEOUT: kubectl did not answer") from None
    if out.returncode:
        raise DatabaseError("kubectl failed; output suppressed")
    return json.loads(out.stdout)


def verify_context(prefix, expected_context, expected_server_check):
    view = kube_json(prefix, ["config", "view", "--minify"])
    contexts = view.get("contexts") or []
    if len(contexts) != 1 or contexts[0]["name"] != expected_context:
        raise DatabaseError("kube context is not the expected one; refusing")
    server = view["clusters"][0]["cluster"]["server"]
    if not expected_server_check(server):
        raise DatabaseError("kube API server does not match the expected side; refusing")
    return server


def primary_pod(prefix):
    cluster = kube_json(prefix, ["-n", NAMESPACE, "get", "cluster.postgresql.cnpg.io", CLUSTER])
    status = cluster.get("status", {})
    primary = status.get("currentPrimary")
    if not primary or status.get("phase") != "Cluster in healthy state":
        raise DatabaseError("CNPG cluster is not healthy or has no primary; refusing")
    return primary, {"phase": status.get("phase"), "instances": status.get("instances"),
                     "ready": status.get("readyInstances"), "image": status.get("image"),
                     "primary": primary}


def source_shell():
    prefix = kubectl_prefix(SOURCE_CONTEXT)
    server = verify_context(prefix, SOURCE_CONTEXT, lambda s: s.endswith(SOURCE_SERVER_SUFFIX))
    primary, status = primary_pod(prefix)
    if status["instances"] != 2:
        raise DatabaseError("source cluster does not have the expected two instances; refusing")
    shell = PodShell(prefix + ["-n", NAMESPACE, "exec", "-i", primary, "-c", "postgres", "--"], "source")
    return shell, prefix, {"context": SOURCE_CONTEXT, "server": server, **status}


def target_shell(namespace=NAMESPACE, cluster=CLUSTER):
    remote = ["sudo", "-n"] + kubectl_prefix(TARGET_CONTEXT, TARGET_KUBECONFIG)
    prefix = ssh_prefix(remote)
    # kube_json appends args to the ssh prefix: the remote command must carry them.
    def remote_prefix(args):
        return ssh_prefix(remote + list(args))
    view = kube_json(remote_prefix(["config", "view", "--minify"]), [])
    contexts = view.get("contexts") or []
    if len(contexts) != 1 or contexts[0]["name"] != TARGET_CONTEXT:
        raise DatabaseError("home kube context is not driftplain-home; refusing")
    if view["clusters"][0]["cluster"]["server"] != TARGET_SERVER:
        raise DatabaseError("home kube API server is not the loopback endpoint; refusing")
    nodes = kube_json(remote_prefix(["get", "nodes"]), [])
    names = [item["metadata"]["name"] for item in nodes.get("items", [])]
    if names != [TARGET_NODE]:
        raise DatabaseError("home cluster does not consist of exactly the expected node; refusing")
    resource = kube_json(remote_prefix(["-n", namespace, "get", "cluster.postgresql.cnpg.io", cluster]), [])
    status = resource.get("status", {})
    primary = status.get("currentPrimary")
    if not primary or status.get("phase") != "Cluster in healthy state" or status.get("instances") != 1:
        raise DatabaseError("home CNPG cluster is not one healthy instance; refusing")
    shell = PodShell(remote_prefix(["-n", namespace, "exec", "-i", primary, "-c", "postgres", "--"]), "target", remote=True)
    return shell, remote_prefix, {"context": TARGET_CONTEXT, "server": TARGET_SERVER, "node": TARGET_NODE,
                                  "namespace": namespace, "cluster": cluster,
                                  "phase": status.get("phase"), "image": status.get("image"), "primary": primary}


# ── fingerprint ───────────────────────────────────────────────────────────────

def table_digest_sql(tables):
    for name in tables:
        if not IDENT_RE.match(name):
            raise DatabaseError("unexpected table identifier in schema public")
    if not tables:
        return "SELECT '{}'::json;"
    parts = []
    for name in tables:
        parts.append(
            f"SELECT '{name}' AS name, (SELECT json_build_object('rows', count(*), 'digest', "
            f"md5(coalesce(string_agg(md5(t::text), ',' ORDER BY md5(t::text)), ''))) "
            f"FROM \"public\".\"{name}\" AS t) AS obj")
    return "SELECT json_object_agg(name, obj) FROM (" + " UNION ALL ".join(parts) + ") AS s;"


def fingerprint(session):
    """Contract + per-table content digests, inside the session's current transaction."""
    for setting in SESSION_SETTINGS:
        session.execute(setting)
    tables = json.loads(session.query(TABLE_LIST_SQL))
    contract = json.loads(session.query(" ".join(CONTRACT_SQL.split("\n"))))
    digests = json.loads(session.query(table_digest_sql(tables)))
    return {"contract": contract, "tables": digests}


def public_counts(fp):
    return {name: value["rows"] for name, value in sorted(fp["tables"].items())}


def compare_fingerprints(source, target):
    """Category-by-category comparison. The result contains no digests or verifiers."""
    result = {"match": True, "categories": {}, "tables": {}}

    def category(name, ok, detail=None):
        result["categories"][name] = {"match": bool(ok), **({"detail": detail} if detail else {})}
        if not ok:
            result["match"] = False

    sc, tc = source["contract"], target["contract"]
    category("server_version", sc["server_version"] == tc["server_version"],
             {"source": sc["server_version"], "target": tc["server_version"]})
    category("database", sc["database"] == tc["database"], {"source": sc["database"], "target": tc["database"]})
    category("alembic_version", sc["alembic"] == tc["alembic"], {"source": sc["alembic"], "target": tc["alembic"]})
    for key in ("extensions", "schema_public", "relations", "columns", "constraints", "indexes", "views",
                "enums", "default_acl", "memberships"):
        category(key, sc[key] == tc[key], None if sc[key] == tc[key] else {"source_items": len(sc[key]) if isinstance(sc[key], list) else 1,
                                                                             "target_items": len(tc[key]) if isinstance(tc[key], list) else 1})
    # Sequences: definition and current value (last_value/is_called are carried by pg_dump setval).
    category("sequences", sc["sequences"] == tc["sequences"],
             {"count": len(sc["sequences"]),
              "mismatched": [row[0] for row, other in zip(sc["sequences"], tc["sequences"]) if row != other]
              if len(sc["sequences"]) == len(tc["sequences"]) else "different sequence sets"})
    # Roles: attributes and the exact SCRAM verifier digest must match; only booleans are reported.
    s_roles = {r["name"]: r for r in sc["roles"]}
    t_roles = {r["name"]: r for r in tc["roles"]}
    role_report = {}
    roles_ok = set(s_roles) == set(APP_ROLES) == set(t_roles)
    for name in APP_ROLES:
        s, t = s_roles.get(name), t_roles.get(name)
        if not s or not t:
            role_report[name] = {"present": False}
            roles_ok = False
            continue
        attrs = {k: v for k, v in s.items() if k != "verifier_sha256"} == {k: v for k, v in t.items() if k != "verifier_sha256"}
        verifier = s["verifier_sha256"] == t["verifier_sha256"] and s["password_kind"] == "scram-sha-256"
        role_report[name] = {"present": True, "attributes_match": attrs, "verifier_match": verifier,
                             "password_kind": s["password_kind"]}
        roles_ok = roles_ok and attrs and verifier
    category("roles", roles_ok, role_report)
    # Tables: row counts and content digests, per table.
    tables_ok = set(source["tables"]) == set(target["tables"])
    for name in sorted(set(source["tables"]) | set(target["tables"])):
        s, t = source["tables"].get(name), target["tables"].get(name)
        entry = {"source_rows": s and s["rows"], "target_rows": t and t["rows"],
                 "rows_match": bool(s and t and s["rows"] == t["rows"]),
                 "content_match": bool(s and t and s["digest"] == t["digest"])}
        tables_ok = tables_ok and entry["rows_match"] and entry["content_match"]
        result["tables"][name] = entry
    category("tables", tables_ok, {"count": len(source["tables"])})
    return result


# ── roles filtering ───────────────────────────────────────────────────────────

ROLE_TOKEN = r'("?)([A-Za-z_][A-Za-z0-9_]*)\1'
CREATE_ROLE_RE = re.compile(r"^CREATE ROLE " + ROLE_TOKEN + r";$")
ALTER_ROLE_RE = re.compile(r"^ALTER ROLE " + ROLE_TOKEN + r" (WITH|SET|RESET|IN DATABASE) ")
GRANT_RE = re.compile(r"^GRANT " + ROLE_TOKEN + r" TO " + ROLE_TOKEN + r"\b")
FORBIDDEN_ATTRIBUTES = re.compile(r"\b(SUPERUSER|REPLICATION|BYPASSRLS)\b")


def filter_roles(globals_sql):
    """Keep only the application roles from `pg_dumpall --roles-only`, made idempotent.

    Cluster-managed roles (postgres, streaming_replica, metrics exporter, pg_*) are
    NOT replayed into the new CNPG cluster. The owner already exists from initdb, so
    CREATE becomes create-if-missing while ALTER re-applies the ORIGINAL attributes
    and SCRAM verifier. Any privilege escalation in an app role is refused.
    """
    kept, seen = [], set()
    for raw in globals_sql.splitlines():
        line = raw.strip()
        if not line or line.startswith("--"):
            continue
        create = CREATE_ROLE_RE.match(line)
        alter = ALTER_ROLE_RE.match(line)
        grant = GRANT_RE.match(line)
        if create and create.group(2) in APP_ROLES:
            name = create.group(2)
            seen.add(name)
            kept.append(f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{name}') "
                        f"THEN CREATE ROLE \"{name}\"; END IF; END $$;")
        elif alter and alter.group(2) in APP_ROLES:
            # `\b` does not split NOSUPERUSER/NOREPLICATION/NOBYPASSRLS, so only the bare
            # (granting) attribute words match here.
            if alter.group(3) == "WITH" and FORBIDDEN_ATTRIBUTES.search(line[len(alter.group(0)):]):
                raise DatabaseError("application role carries a forbidden attribute; refusing")
            kept.append(line)
        elif grant and (grant.group(2) in APP_ROLES or grant.group(4) in APP_ROLES):
            if grant.group(2) not in APP_ROLES or grant.group(4) not in APP_ROLES:
                raise DatabaseError("application role membership crosses into a cluster role; refusing")
            kept.append(line)
    missing = [name for name in APP_ROLES if name not in seen]
    if missing:
        raise DatabaseError("global roles export lacks an application role: " + ", ".join(missing))
    if not any(line.startswith("ALTER ROLE") and "PASSWORD 'SCRAM-SHA-256$" in line for line in kept):
        raise DatabaseError("global roles export carries no SCRAM verifier for the application roles")
    return "\n".join(kept) + "\n"


# ── manifest ──────────────────────────────────────────────────────────────────

def verify_manifest(manifest, directory, names=None):
    """Every listed object must exist with the exact size and SHA-256 (corruption/truncation).

    `names` restricts the check to the objects an operation needs (a restore does not
    need the credential bundle, which hourly exports do not carry).
    """
    for name in (names or manifest["objects"]):
        entry = manifest["objects"].get(name)
        if entry is None:
            raise DatabaseError(f"manifest carries no object {name}")
        path = directory / name
        if not path.is_file():
            raise DatabaseError(f"missing object {name}")
        size = path.stat().st_size
        if size != entry["size"]:
            raise DatabaseError(f"object {name} size differs from manifest (incomplete copy)")
        if sha256_file(path) != entry["sha256"]:
            raise DatabaseError(f"object {name} SHA-256 differs from manifest (corrupt copy)")
    return True


# ── export ────────────────────────────────────────────────────────────────────

def encrypt_stream(recipient, producer, out_path, timeout):
    """producer: a Popen with stdout=PIPE; its bytes go straight into age, never to disk."""
    with open(out_path, "xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        age = subprocess.Popen(["age", "--encrypt", "-r", recipient], stdin=producer.stdout,
                               stdout=stream, stderr=subprocess.DEVNULL)
        producer.stdout.close()
        try:
            producer_rc = producer.wait(timeout=timeout)
            age_rc = age.wait(timeout=60)
        except subprocess.TimeoutExpired:
            producer.kill()
            age.kill()
            raise DatabaseError("TIMEOUT: streaming export exceeded its bound") from None
        stream.flush()
        os.fsync(stream.fileno())
    if producer_rc or age_rc:
        raise DatabaseError(f"streaming export failed (producer {producer_rc}, age {age_rc}); output suppressed")


def encrypt_bytes(recipient, payload, out_path):
    result = subprocess.run(["age", "--encrypt", "-r", recipient], input=payload, capture_output=True, timeout=120)
    if result.returncode:
        raise DatabaseError("age encryption failed; output suppressed")
    write_private(out_path, result.stdout)


def export_credentials(prefix):
    """The original K8s credential Secrets, stripped to name/namespace/type/data."""
    items = kube_json(prefix, ["-n", NAMESPACE, "get", "secret", *CREDENTIAL_SECRETS])["items"]
    bundle, keys = [], {}
    for item in items:
        name = item["metadata"]["name"]
        bundle.append({"apiVersion": "v1", "kind": "Secret", "type": item["type"],
                       "metadata": {"name": name, "namespace": NAMESPACE,
                                    "labels": {k: v for k, v in (item["metadata"].get("labels") or {}).items()
                                               if k == "cnpg.io/reload"}},
                       "data": item["data"]})
        keys[name] = sorted(item["data"])
    if set(keys) != set(CREDENTIAL_SECRETS):
        raise DatabaseError("credential Secrets are not the expected set; refusing")
    return json.dumps({"items": bundle}, sort_keys=True).encode(), keys


def run_export(shell, recipient, directory, timeout):
    """One snapshot: pg_dump --snapshot, same-snapshot fingerprint, then global roles.

    The holder session keeps the exported snapshot alive (bounded by the session
    timeouts it sets) while pg_dump and the fingerprint reader import it.
    """
    timings = {}
    holder = PsqlSession(shell, DATABASE, timeout=timeout, error_file=directory / "psql.stderr")
    try:
        holder.execute("SET idle_in_transaction_session_timeout = '20min';")
        holder.execute("SET statement_timeout = '10min';")
        holder.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;")
        snapshot = holder.query("SELECT pg_export_snapshot();")
        if not SNAPSHOT_RE.match(snapshot):
            raise DatabaseError("unexpected snapshot identifier")
        lsn = holder.query("SELECT pg_current_wal_lsn();")
        if not LSN_RE.match(lsn):
            raise DatabaseError("unexpected WAL position")
        snapshot_at = holder.query("SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"');")

        t0 = time.monotonic()
        dump = shell.popen(["pg_dump", "-U", "postgres", "-d", DATABASE, "-Fc", "--snapshot=" + snapshot,
                            "--no-password"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        encrypt_stream(recipient, dump, directory / "postgres.dump.age", timeout)
        timings["pg_dump_encrypt_seconds"] = round(time.monotonic() - t0, 3)

        t0 = time.monotonic()
        reader = PsqlSession(shell, DATABASE, timeout=timeout, error_file=directory / "psql.stderr")
        try:
            reader.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;")
            reader.execute(f"SET TRANSACTION SNAPSHOT '{snapshot}';")
            fp = fingerprint(reader)
        finally:
            reader.close()
        timings["fingerprint_seconds"] = round(time.monotonic() - t0, 3)
    finally:
        holder.close()

    t0 = time.monotonic()
    globals_sql = shell.run(["pg_dumpall", "-U", "postgres", "--roles-only", "--no-password"], timeout=timeout)
    filter_roles(globals_sql.decode())  # validate now: the restore must not discover a gap later
    encrypt_bytes(recipient, globals_sql, directory / "globals.sql.age")
    del globals_sql
    timings["roles_seconds"] = round(time.monotonic() - t0, 3)
    encrypt_bytes(recipient, json.dumps(fp, sort_keys=True).encode(), directory / "fingerprint.json.age")
    return {"snapshot": snapshot, "wal_lsn": lsn, "snapshot_at_utc": snapshot_at, "fingerprint": fp,
            "timings": timings}


def export(args):
    recipient = RECIPIENT_FILE.read_text().strip()
    if not re.fullmatch(r"age1[0-9a-z]{58}", recipient):
        raise DatabaseError("unexpected age recipient")
    if args.source == "home":
        shell, remote_prefix, source = target_shell()
        prefix = remote_prefix([])
    else:
        shell, prefix, source = source_shell()
    started = now()
    directory = private_dir(BACKUPS_ROOT / f"export-{stamp(started)}")
    outcome = run_export(shell, recipient, directory, args.timeout)
    fp, timings = outcome["fingerprint"], outcome["timings"]
    objects = list(RESTORE_OBJECTS)
    credential_keys = None
    if args.tier == "daily":
        # The credential bundle changes only with a deliberate rotation: daily is enough, and
        # hourly runs then never read Secrets at all.
        credentials, credential_keys = export_credentials(prefix)
        encrypt_bytes(recipient, credentials, directory / "app-credentials.json.age")
        del credentials
        objects.append("app-credentials.json.age")
    finished = now()

    manifest = {
        "slice": "E21/HM5", "kind": "driftplain-home-server-postgres-export", "version": 2,
        "origin": args.source, "tier": args.tier,
        "created_at": started.isoformat(), "finished_at": finished.isoformat(),
        "export_seconds": round((finished - started).total_seconds(), 3), "timings": timings,
        "source": source, "database": DATABASE, "snapshot": outcome["snapshot"],
        "snapshot_at_utc": outcome["snapshot_at_utc"], "wal_lsn": outcome["wal_lsn"],
        "server_version": fp["contract"]["server_version"],
        "alembic_version": fp["contract"]["alembic"], "recipient": recipient,
        "table_row_counts": public_counts(fp), "role_names": [r["name"] for r in fp["contract"]["roles"]],
        "credential_secret_keys": credential_keys,
        "objects": {name: {"size": (directory / name).stat().st_size, "sha256": sha256_file(directory / name)}
                    for name in objects},
        "limitations": [
            "pg_dumpall --roles-only runs outside the data snapshot (global catalog, separate transaction).",
            "Sequence values are the snapshot's setval state; rows written after the snapshot are not included.",
            "CNPG-managed cluster roles (postgres, streaming_replica, metrics) are not part of the restore contract.",
        ],
    }
    write_private(directory / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode())
    print(json.dumps({"export_dir": str(directory), "origin": args.source, "tier": args.tier,
                      "snapshot": outcome["snapshot"], "wal_lsn": outcome["wal_lsn"],
                      "snapshot_at_utc": outcome["snapshot_at_utc"], "finished_at": finished.isoformat(),
                      "export_seconds": manifest["export_seconds"], "timings": timings,
                      "objects": manifest["objects"], "table_row_counts": manifest["table_row_counts"],
                      "alembic_version": manifest["alembic_version"]}, indent=2))


# ── S3 ────────────────────────────────────────────────────────────────────────

def s3_session():
    import boto3
    from botocore.config import Config
    session = boto3.Session(profile_name=OPERATOR_PROFILE, region_name=REGION)
    config = Config(connect_timeout=10, read_timeout=120, retries={"total_max_attempts": 3})
    caller = session.client("sts", config=config).get_caller_identity()
    if caller.get("Arn") != OPERATOR_ARN:
        raise DatabaseError("AWS caller differs from the selected operator; refusing")
    return session.client("s3", config=config)


def object_keys(manifest):
    """S3 keys for one export. HM3 manifests (no tier) keep their historical layout."""
    manifest_stamp = stamp(datetime.datetime.fromisoformat(manifest["created_at"]))
    tier = manifest.get("tier")
    if tier is None:
        label, folder = f"hm3-{manifest_stamp}", "postgres/daily/"
    elif tier in TIERS and manifest.get("origin") in ("home", "aws"):
        label, folder = f"{manifest['origin']}-{manifest_stamp}", f"postgres/{tier}/"
    else:
        raise DatabaseError("manifest tier/origin is not a reviewed layout; refusing")
    keys = {name: f"{folder}{label}/{name}" for name in manifest["objects"] if name != "app-credentials.json.age"}
    if "app-credentials.json.age" in manifest["objects"]:
        keys["app-credentials.json.age"] = f"recovery/app-credentials/{label}.json.age"
    keys["manifest.json"] = f"{folder}{label}/manifest.json"
    return keys


def put_object(client, path, key, expected_sha256=None):
    size = path.stat().st_size
    if size > MAX_SINGLE_PUT:
        raise DatabaseError("archive exceeds the single-PUT limit; a multipart design is required")
    digest = bytes.fromhex(sha256_file(path))
    if expected_sha256 and digest.hex() != expected_sha256:
        raise DatabaseError("object changed since the manifest was written; refusing upload")
    import base64
    with path.open("rb") as stream:
        response = client.put_object(Bucket=BUCKET, Key=key, Body=stream, ContentType="application/octet-stream",
                                     ChecksumAlgorithm="SHA256", ChecksumSHA256=base64.b64encode(digest).decode())
    version = response.get("VersionId")
    if not version or response.get("ChecksumSHA256") != base64.b64encode(digest).decode():
        raise DatabaseError("upload receipt lacks a version or checksum; treat as failed")
    return {"key": key, "version_id": version, "etag": response.get("ETag"), "size": size, "sha256": digest.hex()}


def upload(args):
    directory = Path(args.export_dir)
    manifest = json.loads((directory / "manifest.json").read_text())
    verify_manifest(manifest, directory)
    keys = object_keys(manifest)
    client = s3_session()
    started = now()
    receipts = {}
    for name in manifest["objects"]:
        receipts[name] = put_object(client, directory / name, keys[name], manifest["objects"][name]["sha256"])
    receipts["manifest.json"] = put_object(client, directory / "manifest.json", keys["manifest.json"])
    finished = now()
    receipt = {"bucket": BUCKET, "region": REGION, "uploaded_at": started.isoformat(),
               "finished_at": finished.isoformat(), "tier": manifest.get("tier"), "origin": manifest.get("origin"),
               "snapshot_at_utc": manifest.get("snapshot_at_utc"),
               "upload_seconds": round((finished - started).total_seconds(), 3), "objects": receipts}
    write_private(directory / "upload-receipt.json", json.dumps(receipt, indent=2, sort_keys=True).encode())
    print(json.dumps(receipt, indent=2))


def download(args):
    """Fetch the exact uploaded versions into a NEW directory; the local export is not reused."""
    receipt = json.loads(Path(args.receipt).read_text())
    client = s3_session()
    started = now()
    directory = private_dir(BACKUPS_ROOT / f"restore-{stamp(started)}")
    import base64
    downloaded = {}
    for name, entry in receipt["objects"].items():
        response = client.get_object(Bucket=BUCKET, Key=entry["key"], VersionId=entry["version_id"], ChecksumMode="ENABLED")
        body = response["Body"].read()
        if response.get("VersionId") != entry["version_id"]:
            raise DatabaseError("downloaded version differs from the receipt")
        if base64.b64decode(response.get("ChecksumSHA256", "")) != hashlib.sha256(body).digest():
            raise DatabaseError("S3 checksum does not match the downloaded bytes")
        if hashlib.sha256(body).hexdigest() != entry["sha256"]:
            raise DatabaseError("downloaded bytes differ from the upload receipt")
        write_private(directory / name, body)
        downloaded[name] = {"key": entry["key"], "version_id": entry["version_id"], "size": len(body)}
    manifest = json.loads((directory / "manifest.json").read_text())
    verify_manifest(manifest, directory)
    finished = now()
    result = {"restore_dir": str(directory), "download_seconds": round((finished - started).total_seconds(), 3),
              "objects": downloaded, "manifest_verified": True}
    write_private(directory / "download-receipt.json", json.dumps(result, indent=2, sort_keys=True).encode())
    print(json.dumps(result, indent=2))


# ── restore ───────────────────────────────────────────────────────────────────

def recovery_identity(source):
    """The private age identity from ONE selected custody store, kept in memory only.

    `keychain` needs the local login keychain to authorize this interpreter without a
    prompt; `aws` reads the versioned Secrets Manager value as the selected operator.
    Neither prints, logs or writes the value; it only feeds age's stdin.
    """
    custody = load_custody()
    try:
        if source == "keychain":
            identity = custody.LocalKeychain(KEYCHAIN, RECOVERY_SECRET_ID, KEYCHAIN_ACCOUNT).get()
        else:
            identity = custody.AWSSecret(OPERATOR_PROFILE, REGION, RECOVERY_SECRET_ID, OPERATOR_ARN).get()
    except custody.CustodyError as error:
        raise DatabaseError(f"recovery identity unavailable from {source}: {error}") from None
    if identity is None or custody.recipient(identity) != RECIPIENT_FILE.read_text().strip():
        raise DatabaseError("recovery identity missing or not matching the published recipient")
    return identity


def decrypt_to_bytes(identity, path):
    result = subprocess.run(["age", "--decrypt", "-i", "-", str(path)], input=identity, capture_output=True, timeout=300)
    if result.returncode:
        raise DatabaseError("age decryption failed; output suppressed")
    return result.stdout


def decrypt_into(identity, path, consumer_shell, consumer_argv, timeout, error_file):
    """age plaintext -> pipe -> the pod command's stdin. Nothing decrypted touches a disk."""
    age = subprocess.Popen(["age", "--decrypt", "-i", "-", str(path)], stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    with open(error_file, "xb") as errors:
        os.fchmod(errors.fileno(), 0o600)
        consumer = consumer_shell.popen(consumer_argv, stdin=age.stdout, stdout=subprocess.DEVNULL, stderr=errors)
        age.stdout.close()
        age.stdin.write(identity)
        age.stdin.close()
        try:
            consumer_rc = consumer.wait(timeout=timeout)
            age_rc = age.wait(timeout=60)
        except subprocess.TimeoutExpired:
            consumer.kill()
            age.kill()
            raise DatabaseError("TIMEOUT: streaming restore exceeded its bound") from None
    if age_rc or consumer_rc:
        raise DatabaseError(f"streaming restore failed (age {age_rc}, {consumer_argv[0]} {consumer_rc}); "
                            f"diagnostics in {error_file}")


def install_owner_secret(args):
    """Create app/modelmatch-db-app on home from the encrypted bundle (the original value)."""
    directory = Path(args.restore_dir)
    manifest = json.loads((directory / "manifest.json").read_text())
    verify_manifest(manifest, directory)
    _, remote_prefix, target = target_shell_without_cluster()
    identity = recovery_identity(args.identity_source)
    bundle = json.loads(decrypt_to_bytes(identity, directory / "app-credentials.json.age"))
    owner = [item for item in bundle["items"] if item["metadata"]["name"] == OWNER_SECRET]
    if len(owner) != 1 or owner[0]["type"] != "kubernetes.io/basic-auth" or set(owner[0]["data"]) != {"username", "password"}:
        raise DatabaseError("bundle lacks the expected owner basic-auth item; refusing")
    manifest_bytes = json.dumps(owner[0], sort_keys=True).encode()
    existing = subprocess.run(remote_prefix(["-n", NAMESPACE, "get", "secret", OWNER_SECRET, "-o", "json"]),
                              capture_output=True, timeout=90)
    if existing.returncode == 0:
        current = json.loads(existing.stdout)
        if current.get("data") != owner[0]["data"] or current.get("type") != owner[0]["type"]:
            raise DatabaseError("a different owner Secret already exists on home; refusing to overwrite")
        action = "unchanged"
    else:
        result = subprocess.run(remote_prefix(["-n", NAMESPACE, "create", "-f", "-"]), input=manifest_bytes,
                                capture_output=True, timeout=90)
        if result.returncode:
            raise DatabaseError("creating the owner Secret failed; output suppressed")
        action = "created"
    print(json.dumps({"target": target, "owner_secret": OWNER_SECRET, "action": action}, indent=2))


def target_shell_without_cluster():
    remote = ["sudo", "-n"] + kubectl_prefix(TARGET_CONTEXT, TARGET_KUBECONFIG)

    def remote_prefix(args):
        return ssh_prefix(remote + list(args))
    view = kube_json(remote_prefix(["config", "view", "--minify"]), [])
    contexts = view.get("contexts") or []
    if len(contexts) != 1 or contexts[0]["name"] != TARGET_CONTEXT or view["clusters"][0]["cluster"]["server"] != TARGET_SERVER:
        raise DatabaseError("home kube context/server mismatch; refusing")
    return None, remote_prefix, {"context": TARGET_CONTEXT, "server": TARGET_SERVER}


def run_restore(shell, identity, directory, timeout):
    """Roles (app roles only) -> pg_restore into an EMPTY database -> fingerprint -> compare."""
    timings = {}
    # The target must be EMPTY: never restore over data, never restore twice.
    relations = shell.run(["psql", "-U", "postgres", "-d", DATABASE, "-At", "-X", "-c", RELATION_COUNT_SQL], timeout=60)
    if relations.strip() != b"0":
        raise DatabaseError("target database already contains relations; refusing")

    t0 = time.monotonic()
    globals_sql = decrypt_to_bytes(identity, directory / "globals.sql.age").decode()
    roles_sql = filter_roles(globals_sql)
    shell.run(["psql", "-U", "postgres", "-d", "postgres", "-X", "-q", "-v", "ON_ERROR_STOP=1", "--single-transaction"],
              payload=roles_sql.encode(), timeout=120, error_file=directory / "roles.stderr")
    del globals_sql, roles_sql
    timings["roles_seconds"] = round(time.monotonic() - t0, 3)

    t0 = time.monotonic()
    decrypt_into(identity, directory / "postgres.dump.age", shell,
                 ["pg_restore", "-U", "postgres", "-d", DATABASE, "--no-password", "--exit-on-error",
                  "--single-transaction"], timeout, directory / "pg_restore.stderr")
    timings["pg_restore_seconds"] = round(time.monotonic() - t0, 3)

    t0 = time.monotonic()
    session = PsqlSession(shell, DATABASE, timeout=timeout, error_file=directory / "psql.stderr")
    try:
        session.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;")
        target_fp = fingerprint(session)
    finally:
        session.close()
    timings["fingerprint_seconds"] = round(time.monotonic() - t0, 3)
    source_fp = json.loads(decrypt_to_bytes(identity, directory / "fingerprint.json.age"))
    return {"comparison": compare_fingerprints(source_fp, target_fp), "timings": timings,
            "target_fingerprint": target_fp}


def restore(args):
    directory = Path(args.restore_dir)
    manifest = json.loads((directory / "manifest.json").read_text())
    verify_manifest(manifest, directory, RESTORE_OBJECTS)
    namespace, cluster = restore_target(args.target)
    shell, remote_prefix, target = target_shell(namespace, cluster)
    identity = recovery_identity(args.identity_source)
    started = now()
    outcome = run_restore(shell, identity, directory, args.timeout)
    del identity
    comparison, timings = outcome["comparison"], outcome["timings"]
    finished = now()

    storage = kube_json(remote_prefix(["-n", namespace, "get", "pvc"]), [])
    volumes = kube_json(remote_prefix(["get", "pv"]), [])
    bindings = []
    for pvc in storage.get("items", []):
        pv = next((v for v in volumes.get("items", []) if v["metadata"]["name"] == pvc["spec"].get("volumeName")), {})
        bindings.append({"pvc": pvc["metadata"]["name"], "pvc_uid": pvc["metadata"]["uid"],
                         "pv": pv.get("metadata", {}).get("name"),
                         "reclaim_policy": pv.get("spec", {}).get("persistentVolumeReclaimPolicy"),
                         "storage_class": pvc["spec"].get("storageClassName"),
                         "path": ((pv.get("spec", {}).get("hostPath") or pv.get("spec", {}).get("local") or {}).get("path")),
                         "node_affinity": pv.get("spec", {}).get("nodeAffinity"),
                         "capacity": pv.get("spec", {}).get("capacity")})
    size_bytes = shell.run(["psql", "-U", "postgres", "-d", DATABASE, "-At", "-X", "-c",
                            "SELECT pg_database_size(current_database());"], timeout=60).strip().decode()
    result = {
        "restore_dir": str(directory), "target": target, "target_kind": args.target,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(), "restore_seconds": round((finished - started).total_seconds(), 3),
        "timings": timings, "source_snapshot": manifest["snapshot"], "source_wal_lsn": manifest["wal_lsn"],
        "target_database_bytes": int(size_bytes), "storage": bindings, "comparison": comparison,
        "encrypted_manifest_objects": manifest["objects"],
    }
    write_private(directory / "restore-result.json", json.dumps(result, indent=2, sort_keys=True).encode())
    print(json.dumps(result, indent=2))
    if not comparison["match"]:
        raise DatabaseError("restored database does not match the export snapshot; see restore-result.json")


def restore_target(kind):
    if kind == "production":
        return NAMESPACE, CLUSTER
    if kind == "disposable":
        return DISPOSABLE_NAMESPACE, DISPOSABLE_CLUSTER
    raise DatabaseError("unexpected restore target")


def disposable_manifest(image):
    """A throwaway one-instance CNPG Cluster with the production database contract.

    Same image (server version), database, owner, encoding/locale and post-init grants as
    the production Cluster, so the restore comparison is meaningful; Delete-class storage,
    small limits, its own namespace, and NOT under ArgoCD (nothing reconciles it back).
    """
    if not re.fullmatch(r"ghcr\.io/cloudnative-pg/postgresql:16\.[0-9]+(-[a-z-]+)?", image):
        raise DatabaseError("production Cluster image is not the reviewed PostgreSQL 16 image; refusing")
    labels = {"app.kubernetes.io/part-of": "driftplain-home-server", "driftplain.dev/disposable": "restore-check"}
    return {"apiVersion": "v1", "kind": "List", "items": [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": DISPOSABLE_NAMESPACE, "labels": labels}},
        {"apiVersion": "postgresql.cnpg.io/v1", "kind": "Cluster",
         "metadata": {"name": DISPOSABLE_CLUSTER, "namespace": DISPOSABLE_NAMESPACE, "labels": labels},
         "spec": {"instances": 1, "imageName": image, "enableSuperuserAccess": False,
                  "bootstrap": {"initdb": {"database": DATABASE, "owner": OWNER_ROLE, "encoding": "UTF8",
                                           "localeCollate": "C", "localeCType": "C",
                                           "postInitApplicationSQL": [f'ALTER ROLE "{OWNER_ROLE}" CREATEROLE',
                                                                      f'ALTER SCHEMA public OWNER TO "{OWNER_ROLE}"']}},
                  "storage": {"storageClass": DISPOSABLE_STORAGE_CLASS, "size": "5Gi"},
                  "resources": {"requests": {"cpu": "100m", "memory": "256Mi"}, "limits": {"cpu": "1", "memory": "512Mi"}},
                  "postgresql": {"parameters": {"shared_buffers": "128MB", "max_connections": "50"}},
                  "affinity": {"nodeSelector": {"kubernetes.io/hostname": TARGET_NODE}}}}]}


def disposable_target(args):
    """create: apply the throwaway Cluster and wait (bounded) until healthy; delete: remove its namespace."""
    _, remote_prefix, target = target_shell_without_cluster()
    deadline = time.monotonic() + DISPOSABLE_WAIT_SECONDS
    started = now()
    if args.action == "create":
        production = kube_json(remote_prefix(["-n", NAMESPACE, "get", "cluster.postgresql.cnpg.io", CLUSTER]), [])
        image = production.get("status", {}).get("image") or ""
        payload = json.dumps(disposable_manifest(image), sort_keys=True).encode()
        result = subprocess.run(remote_prefix(["apply", "-f", "-"]), input=payload, capture_output=True, timeout=120)
        if result.returncode:
            raise DatabaseError("applying the disposable target failed; output suppressed")
        while True:
            try:
                resource = kube_json(remote_prefix(["-n", DISPOSABLE_NAMESPACE, "get", "cluster.postgresql.cnpg.io",
                                                    DISPOSABLE_CLUSTER]), [])
            except DatabaseError:
                resource = {}
            status = resource.get("status", {})
            if status.get("phase") == "Cluster in healthy state" and status.get("readyInstances") == 1:
                break
            if time.monotonic() > deadline:
                raise DatabaseError(f"TIMEOUT: disposable target not healthy within {DISPOSABLE_WAIT_SECONDS}s "
                                    f"(phase: {status.get('phase')})")
            time.sleep(10)
        detail = {"phase": status.get("phase"), "image": status.get("image"), "primary": status.get("currentPrimary")}
    else:
        result = subprocess.run(remote_prefix(["delete", "namespace", DISPOSABLE_NAMESPACE, "--ignore-not-found",
                                              "--wait=false"]), capture_output=True, timeout=120)
        if result.returncode:
            raise DatabaseError("deleting the disposable namespace failed; output suppressed")
        while True:
            probe = subprocess.run(remote_prefix(["get", "namespace", DISPOSABLE_NAMESPACE]), capture_output=True, timeout=90)
            if probe.returncode and b"NotFound" in probe.stderr:
                break
            if time.monotonic() > deadline:
                raise DatabaseError(f"TIMEOUT: disposable namespace still present after {DISPOSABLE_WAIT_SECONDS}s")
            time.sleep(10)
        detail = {"namespace_deleted": True}
    print(json.dumps({"target": target, "action": args.action, "namespace": DISPOSABLE_NAMESPACE,
                      "cluster": DISPOSABLE_CLUSTER, "seconds": round((now() - started).total_seconds(), 3),
                      **detail}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("export")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--source", choices=("aws", "home"), default="aws",
                   help="aws = the EKS production cluster (HM3/HM7); home = the home CNPG instance over SSH (HM5 schedule)")
    p.add_argument("--tier", choices=TIERS, default="daily",
                   help="daily carries the credential bundle; hourly holds only the data/roles/fingerprint objects")
    p = sub.add_parser("upload"); p.add_argument("--export-dir", required=True)
    p = sub.add_parser("download"); p.add_argument("--receipt", required=True)
    for name in ("install-owner-secret", "restore"):
        p = sub.add_parser(name)
        p.add_argument("--restore-dir", required=True)
        p.add_argument("--identity-source", choices=("keychain", "aws"), required=True,
                       help="which custody store supplies the private age identity for this run")
        p.add_argument("--timeout", type=int, default=1800)
        if name == "restore":
            p.add_argument("--target", choices=("production", "disposable"), default="production",
                           help="production = app/modelmatch-postgres (must be empty); disposable = the throwaway check target")
    p = sub.add_parser("disposable-target"); p.add_argument("action", choices=("create", "delete"))
    args = parser.parse_args()
    {"export": export, "upload": upload, "download": download, "install-owner-secret": install_owner_secret,
     "restore": restore, "disposable-target": disposable_target}[args.command](args)


if __name__ == "__main__":
    try:
        main()
    except DatabaseError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print("home-server database operation failed; output suppressed. Preserve the export/restore "
              "directories and consult HM3-RESTORE.md.", file=sys.stderr)
        sys.exit(1)
