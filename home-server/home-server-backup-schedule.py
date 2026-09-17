#!/usr/bin/env python3
"""E21/HM5 — Mac interim scheduler for hourly encrypted home PostgreSQL backups.

launchd runs `run` every hour (dev.driftplain.home-server-backup.plist). Each run:
  1. picks the tier — the first run of a UTC day is `daily` (carries the credential
     bundle, retained 30 days); every other run is `hourly` (retained one day);
  2. exports the home CNPG instance over SSH with home-server-database.py (one
     REPEATABLE READ snapshot, age-encrypted in pipes) and uploads it with single
     PUTs as the operator profile (the write-only Roles Anywhere identity is the
     in-cluster path; it stays gated until sessions are approved);
  3. prunes local export directories older than `keep_local_days` — only tiered
     exports that carry an upload receipt; the HM3 export and restore directories stay;
  4. writes a sanitized status file (tiers, stamps, seconds, sizes, keys, version IDs,
     row counts; never values, hashes of data, dumps or credentials);
  5. pings the external heartbeat URL only after a verified upload; on any failure
     it records the failure, posts a fixed macOS notification and exits non-zero.

`check` exits non-zero when the last success is older than `max_age_hours` or the last
run failed (for a manual glance and for the runbook); `status` prints the status file.
"""

import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent
DATABASE_TOOL = ROOT / "home-server-database.py"
BACKUPS_ROOT = Path.home() / ".local" / "share" / "driftplain" / "home-server-backups"
STATUS_FILE = BACKUPS_ROOT / "schedule-status.json"
LOCK_FILE = BACKUPS_ROOT / "schedule.lock"
TIERS = ("hourly", "daily")
NOTIFICATION = ('display notification "Hourly home database backup failed. Check schedule-status.json." '
                'with title "Driftplain home-server"')
CONFIG_DEFAULTS = {"enabled": False, "heartbeat_url": None, "keep_local_days": 2, "export_timeout": 900,
                   "max_age_hours": 2, "notify": True}


class ScheduleError(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def load_config(path):
    config = {**CONFIG_DEFAULTS, **json.loads(Path(path).read_text())}
    unknown = set(config) - set(CONFIG_DEFAULTS)
    if unknown:
        raise ScheduleError("unexpected configuration keys: " + ", ".join(sorted(unknown)))
    url = config["heartbeat_url"]
    if url is not None and not (isinstance(url, str) and url.startswith("https://") and len(url) < 512):
        raise ScheduleError("heartbeat_url must be an https URL")
    for key in ("keep_local_days", "export_timeout", "max_age_hours"):
        if not isinstance(config[key], int) or config[key] < 1:
            raise ScheduleError(f"{key} must be a positive integer")
    return config


def load_status():
    try:
        return json.loads(STATUS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def write_status(status):
    BACKUPS_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = STATUS_FILE.with_suffix(".tmp")
    with open(tmp, "w", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        json.dump(status, stream, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, STATUS_FILE)


def choose_tier(status, instant):
    """The first successful run of each UTC day is the daily export; the rest are hourly."""
    return "daily" if status.get("last_daily_utc_date") != instant.date().isoformat() else "hourly"


def run_tool(argv, timeout):
    """Run home-server-database.py; its stdout is sanitized JSON, its stderr a fixed one-liner."""
    try:
        result = subprocess.run([sys.executable, str(DATABASE_TOOL), *argv], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ScheduleError(f"TIMEOUT: {argv[0]} exceeded {timeout}s") from None
    if result.returncode:
        last = (result.stderr.strip().splitlines() or ["no diagnostics"])[-1][:200]
        raise ScheduleError(f"{argv[0]} failed (exit {result.returncode}): {last}")
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise ScheduleError(f"{argv[0]} produced no JSON result") from None


def prune_exports(root, keep_days, instant):
    """Remove tiered, uploaded export directories older than keep_days. Nothing else."""
    removed = []
    cutoff = instant - datetime.timedelta(days=keep_days)
    for directory in sorted(root.glob("export-*")):
        manifest_path, receipt_path = directory / "manifest.json", directory / "upload-receipt.json"
        if not (directory.is_dir() and manifest_path.is_file() and receipt_path.is_file()):
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
            created = datetime.datetime.fromisoformat(manifest["created_at"])
        except (OSError, ValueError, KeyError):
            continue
        if manifest.get("tier") not in TIERS or created >= cutoff:
            continue
        shutil.rmtree(directory)
        removed.append(directory.name)
    return removed


def ping_heartbeat(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 — https enforced in load_config
            return 200 <= response.status < 300
    except (OSError, ValueError):
        return False


def notify(config):
    if config["notify"]:
        subprocess.run(["/usr/bin/osascript", "-e", NOTIFICATION], capture_output=True, timeout=30)


def run_once(config, tool=run_tool, clock=now, heartbeat=ping_heartbeat, notifier=notify, root=None):
    root = root or BACKUPS_ROOT
    status = load_status()
    started = clock()
    tier = choose_tier(status, started)
    status.update({"last_run_at": started.isoformat(), "last_tier": tier, "runs": status.get("runs", 0) + 1})
    try:
        if not config["enabled"]:
            raise ScheduleError("schedule disabled in configuration")
        export = tool(["export", "--source", "home", "--tier", tier, "--timeout", str(config["export_timeout"])],
                      config["export_timeout"] + 120)
        receipt = tool(["upload", "--export-dir", export["export_dir"]], 600)
        finished = clock()
        snapshot_at = datetime.datetime.fromisoformat(export["snapshot_at_utc"].replace("Z", "+00:00"))
        upload_done = datetime.datetime.fromisoformat(receipt["finished_at"])
        removed = prune_exports(root, config["keep_local_days"], finished)
        success = {
            "at": finished.isoformat(), "tier": tier, "export_dir": export["export_dir"],
            "snapshot": export["snapshot"], "wal_lsn": export["wal_lsn"], "snapshot_at_utc": export["snapshot_at_utc"],
            "alembic_version": export["alembic_version"], "table_row_counts": export["table_row_counts"],
            "export_seconds": export["export_seconds"], "upload_seconds": receipt["upload_seconds"],
            "snapshot_to_durable_seconds": round((upload_done - snapshot_at).total_seconds(), 3),
            "objects": {name: {"key": entry["key"], "version_id": entry["version_id"], "size": entry["size"]}
                        for name, entry in receipt["objects"].items()},
            "pruned_local_exports": removed,
        }
        status.update({"last_success": success, "last_success_at": finished.isoformat(), "last_error": None,
                       "consecutive_failures": 0})
        if tier == "daily":
            status["last_daily_utc_date"] = started.date().isoformat()
        if config["heartbeat_url"]:
            status["last_heartbeat_ok"] = heartbeat(config["heartbeat_url"])
            status["last_heartbeat_at"] = clock().isoformat()
        write_status(status)
        print(json.dumps({"ok": True, "tier": tier, "snapshot": success["snapshot"],
                          "snapshot_to_durable_seconds": success["snapshot_to_durable_seconds"],
                          "export_seconds": success["export_seconds"], "upload_seconds": success["upload_seconds"],
                          "pruned_local_exports": removed, "heartbeat_ok": status.get("last_heartbeat_ok")}, indent=2))
        return 0
    except ScheduleError as error:
        status.update({"last_error": str(error), "last_failure_at": clock().isoformat(),
                       "consecutive_failures": status.get("consecutive_failures", 0) + 1})
        write_status(status)
        notifier(config)
        print(json.dumps({"ok": False, "tier": tier, "error": str(error)}, indent=2))
        return 1


def check(config, clock=now):
    status = load_status()
    last = status.get("last_success_at")
    problems = []
    if not last:
        problems.append("no successful backup recorded")
    else:
        age = (clock() - datetime.datetime.fromisoformat(last)).total_seconds() / 3600
        if age > config["max_age_hours"]:
            problems.append(f"last success is {age:.1f}h old (limit {config['max_age_hours']}h)")
    if status.get("last_error"):
        problems.append("last run failed: " + status["last_error"])
    if config["heartbeat_url"] and status.get("last_heartbeat_ok") is False:
        problems.append("last heartbeat ping failed")
    print(json.dumps({"ok": not problems, "problems": problems, "last_success_at": last,
                      "last_tier": status.get("last_tier"), "consecutive_failures": status.get("consecutive_failures", 0)},
                     indent=2))
    return 1 if problems else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("run", "check", "status"))
    parser.add_argument("--config", default=str(BACKUPS_ROOT / "schedule.json"))
    args = parser.parse_args()
    config = load_config(args.config)
    if args.command == "status":
        print(json.dumps(load_status(), indent=2, sort_keys=True))
        return 0
    if args.command == "check":
        return check(config)
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
    except ScheduleError as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
