#!/usr/bin/env python3
"""E21/HM5 — Mac daily maintenance coordinator: identity deadlines, CRL refresh, sealing keys.

launchd runs `run` once a day (dev.driftplain.home-server-maintenance.plist). Each run:
  1. reads the PUBLIC issuer ledger (no Keychain access) and computes the deadlines: the
     newest non-revoked leaf per workload identity, the CA, and the current CRL nextUpdate;
  2. lists the home Sealed Secrets controller keys (names + public cert fingerprints, over
     the SSH/kubectl transport of home-server-sealing-keys.py) and compares them with the
     newest UPLOADED sealing-key backup manifest; a key absent from a verified backup makes
     the job run `backup` (the controller renews its key every 30 days);
  3. when the CRL is within `crl_refresh_before_days` of nextUpdate, runs
     home-server-issuer.py `crl` (Keychain-authorized signing, S3 backup, public PEM). With
     `crl_publish` it then runs the AWS update-crl + get-trust-anchor/get-crl readback and
     home-server-issuer.py `verify-crl`; otherwise the refreshed PEM waits for the ISSUER.md
     update procedure and a warning stands until it is done;
  4. runs home-server-renew.py (a no-op while renewal.json keeps enabled=false), so enabling
     leaf renewal needs no second scheduler;
  5. grades findings against the IDENTITY.md thresholds (leaf warn 30 d, escalate 14/7; CA
     180/90; CRL 7/1), writes a sanitized status file (dates, day counts, names, public
     fingerprints, version IDs — never key material), posts a fixed macOS notification on
     any failure or critical finding, and pings the heartbeat URL only after a successful
     run with no critical finding — so a missed maintenance heartbeat means "look".

`check` exits non-zero when the last run is stale/failed or a critical finding stands;
`status` prints the status file.
"""

import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

from cryptography import x509

ROOT = Path(__file__).resolve().parent
BACKUPS_ROOT = Path.home() / ".local" / "share" / "driftplain" / "home-server-backups"
IDENTITY_ROOT = Path.home() / ".local" / "share" / "driftplain" / "home-server-identity"
STATUS_FILE = BACKUPS_ROOT / "maintenance-status.json"
LOCK_FILE = BACKUPS_ROOT / "maintenance.lock"
RECORDS_DIR = BACKUPS_ROOT / "crl-records"
ISSUER_TOOL = ROOT / "home-server-issuer.py"
RENEW_TOOL = ROOT / "home-server-renew.py"
SEALING_TOOL = ROOT / "home-server-sealing-keys.py"
IDENTITIES = ("backup", "bedrock")
REGION = "ap-south-1"
ANCHOR_PREFIX = "arn:aws:rolesanywhere:ap-south-1:957261948820:trust-anchor/"
NOTIFICATION = ('display notification "Home-server maintenance needs attention. Check maintenance-status.json." '
                'with title "Driftplain home-server"')
CONFIG_DEFAULTS = {
    "enabled": False, "heartbeat_url": None, "notify": True,
    "renewal_config": str(IDENTITY_ROOT / "renewal.json"),
    "crl_refresh_before_days": 10, "crl_publish": False, "crl_id": None, "trust_anchor_arn": None,
    "aws_profile": "saa", "sealing_key_backup": True, "tool_timeout": 600, "max_age_hours": 36,
}
THRESHOLDS = {"leaf_warning": 30, "leaf_critical": 14, "leaf_imminent": 7,
              "ca_warning": 180, "ca_critical": 90, "crl_warning": 7, "crl_critical": 1}


class MaintenanceError(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def load_config(path):
    config = {**CONFIG_DEFAULTS, **json.loads(Path(path).read_text())}
    unknown = set(config) - set(CONFIG_DEFAULTS)
    if unknown:
        raise MaintenanceError("unexpected configuration keys: " + ", ".join(sorted(unknown)))
    url = config["heartbeat_url"]
    if url is not None and not (isinstance(url, str) and url.startswith("https://") and len(url) < 512):
        raise MaintenanceError("heartbeat_url must be an https URL")
    for key in ("crl_refresh_before_days", "tool_timeout", "max_age_hours"):
        if not isinstance(config[key], int) or config[key] < 1:
            raise MaintenanceError(f"{key} must be a positive integer")
    if config["crl_publish"]:
        if not (isinstance(config["crl_id"], str) and config["crl_id"]):
            raise MaintenanceError("crl_publish requires the imported crl_id")
        if not (isinstance(config["trust_anchor_arn"], str) and config["trust_anchor_arn"].startswith(ANCHOR_PREFIX)):
            raise MaintenanceError("crl_publish requires the home trust_anchor_arn")
    return config


def load_status():
    try:
        return json.loads(STATUS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def write_private(path, payload):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def write_status(status):
    write_private(STATUS_FILE, json.dumps(status, indent=2, sort_keys=True))


def days_left(instant_after, instant):
    return round((instant_after - instant).total_seconds() / 86400, 1)


# ── facts ──────────────────────────────────────────────────────────────────────

def ledger_facts(ledger, instant):
    """Deadlines from the public ledger: newest non-revoked leaf per identity, CA, current CRL."""
    revoked = {entry["serial"] for entry in ledger["revoked"]}
    leaves = {}
    for entry in ledger["issued"]:
        certificate = x509.load_pem_x509_certificate(entry["certificate"].encode())
        if str(certificate.serial_number) in revoked:
            continue
        current = leaves.get(entry["identity"])
        if current is None or certificate.not_valid_after_utc > current:
            leaves[entry["identity"]] = certificate.not_valid_after_utc
    ca = x509.load_pem_x509_certificate(ledger["issuer_certificate"].encode())
    crl = None
    if ledger.get("crl_pem"):
        loaded = x509.load_pem_x509_crl(ledger["crl_pem"].encode())
        crl = {"number": ledger["crl_number"], "last_update": loaded.last_update_utc.isoformat(),
               "next_update": loaded.next_update_utc.isoformat(), "days_left": days_left(loaded.next_update_utc, instant),
               "revoked_count": len(loaded)}
    return {"leaves": {identity: {"not_after": expiry.isoformat(), "days_left": days_left(expiry, instant)}
                       for identity, expiry in sorted(leaves.items())},
            "ca": {"not_after": ca.not_valid_after_utc.isoformat(), "days_left": days_left(ca.not_valid_after_utc, instant)},
            "crl": crl, "pending": sorted(ledger.get("pending") or {}), "backup_required": bool(ledger.get("backup_required"))}


def latest_backed_up_keys(root):
    """(directory name, {(name, cert_sha256)}) of the newest sealing-key backup that carries an upload receipt."""
    for directory in sorted(root.glob("sealing-keys-*"), reverse=True):
        if directory.name.startswith("sealing-keys-restore-") or not (directory / "upload-receipt.json").is_file():
            continue
        try:
            manifest = json.loads((directory / "manifest.json").read_text())
            return directory.name, {(key["name"], key["cert_sha256"]) for key in manifest["keys"]}
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None, set()


def sealing_key_facts(listing, root):
    cluster = [{"name": key["name"], "status": key["status"], "created": key.get("created"), "cert_sha256": key["cert_sha256"]}
               for key in listing["keys"]]
    backed_dir, backed = latest_backed_up_keys(root)
    unbacked = sorted(key["name"] for key in cluster if (key["name"], key["cert_sha256"]) not in backed)
    return {"cluster": cluster, "backed_up_manifest": backed_dir, "unbacked": unbacked}


# ── grading ────────────────────────────────────────────────────────────────────

def grade(facts, sealing, renewal_enabled, thresholds=THRESHOLDS):
    findings = []

    def add(level, subject, message):
        findings.append({"level": level, "subject": subject, "message": message})

    for identity in IDENTITIES:
        leaf = facts["leaves"].get(identity)
        if leaf is None:
            add("critical", f"leaf/{identity}", "no valid (non-revoked) leaf recorded in the issuer ledger")
            continue
        days = leaf["days_left"]
        if days <= thresholds["leaf_imminent"]:
            add("critical", f"leaf/{identity}", f"expires in {days} days — renewal failed or is not running")
        elif days <= thresholds["leaf_critical"]:
            add("critical", f"leaf/{identity}", f"expires in {days} days — escalate: renewal has not happened")
        elif days <= thresholds["leaf_warning"]:
            add("warning", f"leaf/{identity}", f"expires in {days} days — renewal window open")
        if days <= thresholds["leaf_warning"] and not renewal_enabled:
            add("warning", f"leaf/{identity}", "renewal schedule is disabled (renewal.json enabled=false)")
    ca_days = facts["ca"]["days_left"]
    if ca_days <= thresholds["ca_critical"]:
        add("critical", "ca", f"issuer CA expires in {ca_days} days — replacement must be under way")
    elif ca_days <= thresholds["ca_warning"]:
        add("warning", "ca", f"issuer CA expires in {ca_days} days — plan the CA replacement")
    crl = facts["crl"]
    if crl is None:
        add("critical", "crl", "no CRL in the ledger")
    elif crl["days_left"] <= 0:
        add("critical", "crl", f"CRL {crl['number']} expired ({crl['next_update']})")
    elif crl["days_left"] <= thresholds["crl_critical"]:
        add("critical", "crl", f"CRL {crl['number']} nextUpdate in {crl['days_left']} days")
    elif crl["days_left"] <= thresholds["crl_warning"]:
        add("warning", "crl", f"CRL {crl['number']} nextUpdate in {crl['days_left']} days")
    if facts["pending"]:
        add("warning", "renewal", "pending leaf delivery in the ledger: " + ", ".join(facts["pending"]))
    if facts["backup_required"]:
        add("warning", "issuer-backup", "ledger marks an issuer backup as required (the first enabled renewal run or `issuer backup` clears it)")
    if sealing["unbacked"]:
        add("critical", "sealing-keys", "sealing key(s) not in a verified backup: " + ", ".join(sealing["unbacked"]))
    if not any(key["status"] == "active" for key in sealing["cluster"]):
        add("critical", "sealing-keys", "no ACTIVE sealing key on the home controller")
    return findings


# ── tools ──────────────────────────────────────────────────────────────────────

class Tools:
    """Subprocess boundary: the sibling operator tools and the AWS CLI. Stdout is sanitized JSON/text."""

    def __init__(self, config):
        self.config = config

    def run(self, argv, timeout, expect_json=True):
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise MaintenanceError(f"TIMEOUT: {Path(argv[1] if argv[0] == sys.executable else argv[0]).name} exceeded {timeout}s") from None
        name = Path(argv[1]).name if argv[0] == sys.executable else argv[0]
        if result.returncode:
            last = (result.stderr.strip().splitlines() or result.stdout.strip().splitlines() or ["no diagnostics"])[-1][:200]
            raise MaintenanceError(f"{name} failed (exit {result.returncode}): {last}")
        if not expect_json:
            return result.stdout
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise MaintenanceError(f"{name} produced no JSON result") from None

    def sealing(self, argv):
        return self.run([sys.executable, str(SEALING_TOOL), *argv], self.config["tool_timeout"])

    def issuer(self, argv):
        return self.run([sys.executable, str(ISSUER_TOOL), *argv, "--config", self.config["renewal_config"]], self.config["tool_timeout"])

    def renew(self):
        return self.run([sys.executable, str(RENEW_TOOL), "--config", self.config["renewal_config"]], max(self.config["tool_timeout"], 900), expect_json=False)

    def aws(self, argv):
        return self.run(["aws", "--profile", self.config["aws_profile"], "--region", REGION, "--output", "json", *argv], 120)


def refresh_crl(config, tools, ledger_path, stamp, records_dir=None):
    records_dir = records_dir or RECORDS_DIR
    result = tools.issuer(["crl"])
    number = result["crl_number"]
    action = {"action": "crl-refresh", "crl_number": number, "receipt_version_id": (result.get("receipt") or {}).get("version_id"),
              "published": False}
    if not config["crl_publish"]:
        return action, {"level": "warning", "subject": "crl", "message": f"CRL {number} refreshed locally; the AWS update-crl step is pending (ISSUER.md)"}
    pem = ledger_path.parent / f"issuer-crl-{number}.pem"
    if not pem.is_file():
        raise MaintenanceError(f"published CRL file missing: {pem.name}")
    crl_id, anchor_arn = config["crl_id"], config["trust_anchor_arn"]
    tools.aws(["rolesanywhere", "update-crl", "--crl-id", crl_id, "--crl-data", "fileb://" + str(pem)])
    anchor_record = records_dir / f"{stamp}-trust-anchor.json"
    crl_record = records_dir / f"{stamp}-crl.json"
    write_private(anchor_record, json.dumps(tools.aws(["rolesanywhere", "get-trust-anchor", "--trust-anchor-id", anchor_arn.rsplit("/", 1)[1]])))
    write_private(crl_record, json.dumps(tools.aws(["rolesanywhere", "get-crl", "--crl-id", crl_id])))
    tools.issuer(["verify-crl", "--anchor-record", str(anchor_record), "--crl-record", str(crl_record),
                  "--anchor-arn", anchor_arn, "--crl-id", crl_id])
    action.update({"published": True, "crl_id": crl_id, "verified_records": [anchor_record.name, crl_record.name]})
    return action, None


def ping_heartbeat(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 — https enforced in load_config
            return 200 <= response.status < 300
    except (OSError, ValueError):
        return False


def notify(config):
    if config["notify"]:
        subprocess.run(["/usr/bin/osascript", "-e", NOTIFICATION], capture_output=True, timeout=30)


# ── the run ────────────────────────────────────────────────────────────────────

def run_once(config, tools=None, clock=now, heartbeat=ping_heartbeat, notifier=notify, root=None):
    tools = tools or Tools(config)
    root = root or BACKUPS_ROOT
    status = load_status()
    started = clock()
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    status.update({"last_run_at": started.isoformat(), "runs": status.get("runs", 0) + 1})
    actions, extra_findings = [], []
    try:
        if not config["enabled"]:
            raise MaintenanceError("maintenance disabled in configuration")
        renewal = json.loads(Path(config["renewal_config"]).read_text())
        ledger_path = Path(renewal["ledger"])
        facts = ledger_facts(json.loads(ledger_path.read_text()), started)
        sealing = sealing_key_facts(tools.sealing(["list"]), root)
        if sealing["unbacked"] and config["sealing_key_backup"]:
            result = tools.sealing(["backup"])
            names = {key["name"] for key in result["keys"]}
            if not set(sealing["unbacked"]) <= names or not result.get("uploaded"):
                raise MaintenanceError("sealing-key backup did not cover the new key(s) with an upload")
            actions.append({"action": "sealing-key-backup", "keys": sorted(names), "uploaded": sorted(result["uploaded"]),
                            "backup_dir": Path(result["backup_dir"]).name})
            sealing = sealing_key_facts({"keys": sealing["cluster"]}, root)
        if facts["crl"] is None or facts["crl"]["days_left"] <= config["crl_refresh_before_days"]:
            action, finding = refresh_crl(config, tools, ledger_path, stamp)
            actions.append(action)
            if finding:
                extra_findings.append(finding)
            facts = ledger_facts(json.loads(ledger_path.read_text()), started)
        renewal_out = tools.renew()
        actions.append({"action": "renewal", "enabled": renewal.get("enabled") is True,
                        "result": (renewal_out.strip().splitlines() or ["ok"])[0][:120]})
        if renewal.get("enabled") is True:
            facts = ledger_facts(json.loads(ledger_path.read_text()), started)
        findings = grade(facts, sealing, renewal.get("enabled") is True) + extra_findings
        critical = [f for f in findings if f["level"] == "critical"]
        finished = clock()
        status.update({"last_success_at": finished.isoformat(), "last_error": None, "consecutive_failures": 0,
                       "facts": facts, "sealing_keys": sealing, "findings": findings, "actions": actions,
                       "seconds": round((finished - started).total_seconds(), 3)})
        if config["heartbeat_url"]:
            status["last_heartbeat_ok"] = heartbeat(config["heartbeat_url"]) if not critical else False
            status["last_heartbeat_at"] = clock().isoformat()
            status["last_heartbeat_withheld"] = bool(critical)
        write_status(status)
        if critical:
            notifier(config)
        print(json.dumps({"ok": True, "critical": len(critical), "warnings": len(findings) - len(critical),
                          "actions": [a["action"] for a in actions], "findings": findings,
                          "heartbeat_ok": status.get("last_heartbeat_ok")}, indent=2))
        return 0
    except (MaintenanceError, OSError, ValueError, KeyError) as error:
        message = str(error) if isinstance(error, MaintenanceError) else f"{type(error).__name__}: {str(error)[:160]}"
        status.update({"last_error": message, "last_failure_at": clock().isoformat(),
                       "consecutive_failures": status.get("consecutive_failures", 0) + 1, "actions": actions})
        write_status(status)
        notifier(config)
        print(json.dumps({"ok": False, "error": message, "actions": [a["action"] for a in actions]}, indent=2))
        return 1


def check(config, clock=now):
    status = load_status()
    last = status.get("last_success_at")
    problems = []
    if not last:
        problems.append("no successful maintenance run recorded")
    else:
        age = (clock() - datetime.datetime.fromisoformat(last)).total_seconds() / 3600
        if age > config["max_age_hours"]:
            problems.append(f"last success is {age:.1f}h old (limit {config['max_age_hours']}h)")
    if status.get("last_error"):
        problems.append("last run failed: " + status["last_error"])
    for finding in status.get("findings", []):
        if finding["level"] == "critical":
            problems.append(f"critical {finding['subject']}: {finding['message']}")
    if config["heartbeat_url"] and status.get("last_heartbeat_ok") is False:
        problems.append("last heartbeat ping failed or was withheld")
    print(json.dumps({"ok": not problems, "problems": problems, "last_success_at": last,
                      "warnings": [f["message"] for f in status.get("findings", []) if f["level"] == "warning"],
                      "consecutive_failures": status.get("consecutive_failures", 0)}, indent=2))
    return 1 if problems else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("run", "check", "status"))
    parser.add_argument("--config", default=str(BACKUPS_ROOT / "maintenance.json"))
    args = parser.parse_args()
    config = load_config(args.config)
    if args.command == "status":
        print(json.dumps(load_status(), indent=2, sort_keys=True))
        return 0
    if args.command == "check":
        return check(config)
    os.umask(0o077)
    BACKUPS_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(LOCK_FILE, "w", opener=lambda p, f: os.open(p, f, 0o600)) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(json.dumps({"ok": True, "skipped": "another run holds the lock"}))
            return 0
        return run_once(config)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MaintenanceError as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
