"""HM5 scheduler behavior with fakes: tier choice, pruning, status, heartbeat and failure paths."""

import datetime
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sched = load("home_server_backup_schedule", "home-server-backup-schedule.py")
UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 9, 17, 14, 5, tzinfo=UTC)


def fake_export(export_dir, tier):
    return {"export_dir": str(export_dir), "origin": "home", "tier": tier, "snapshot": "00000009-00000001-1",
            "wal_lsn": "0/1", "snapshot_at_utc": "2026-09-17T14:05:10.000000Z", "finished_at": T0.isoformat(),
            "export_seconds": 12.5, "alembic_version": ["a4b5c6d7e8f9"], "table_row_counts": {"user": 9},
            "timings": {}, "objects": {}}


def fake_receipt():
    return {"finished_at": (T0 + datetime.timedelta(seconds=40)).isoformat(), "upload_seconds": 3.1,
            "objects": {"postgres.dump.age": {"key": "postgres/hourly/home-x/postgres.dump.age", "version_id": "v1",
                                              "size": 9, "sha256": "ff" * 32, "etag": "e"}}}


class FakeTool:
    def __init__(self, root, fail=None):
        self.root, self.fail, self.calls = root, fail, []

    def __call__(self, argv, timeout):
        self.calls.append((argv, timeout))
        if self.fail == argv[0]:
            raise sched.ScheduleError(f"{argv[0]} failed (exit 1): transport refused; output suppressed")
        if argv[0] == "export":
            return fake_export(self.root / "export-new", argv[argv.index("--tier") + 1])
        return fake_receipt()


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(sched, "BACKUPS_ROOT", self.root),
                        patch.object(sched, "STATUS_FILE", self.root / "schedule-status.json")]
        for p in self.patches:
            p.start()
        self.config = sched.load_config(ROOT / "home-server-backup-schedule.example.json")
        self.pings, self.notifications = [], []

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def heartbeat(self, url):
        self.pings.append(url)
        return True

    def notifier(self, config):
        self.notifications.append(config["notify"])

    def run_once(self, tool, clock=lambda: T0, config=None):
        return sched.run_once(config or self.config, tool=tool, clock=clock, heartbeat=self.heartbeat,
                              notifier=self.notifier, root=self.root)

    def test_first_run_of_a_utc_day_is_daily_then_hourly(self):
        tool = FakeTool(self.root)
        self.assertEqual(self.run_once(tool), 0)
        self.assertEqual(tool.calls[0][0][:5], ["export", "--source", "home", "--tier", "daily"])
        self.assertEqual(self.run_once(tool), 0)
        self.assertEqual(tool.calls[2][0][4], "hourly")
        next_day = T0 + datetime.timedelta(days=1)
        self.assertEqual(self.run_once(tool, clock=lambda: next_day), 0)
        self.assertEqual(tool.calls[4][0][4], "daily")
        status = sched.load_status()
        self.assertEqual(status["last_daily_utc_date"], next_day.date().isoformat())
        self.assertEqual(status["runs"], 3)

    def test_daily_only_runs_the_daily_export_then_skips_without_export_or_heartbeat(self):
        config = {**self.config, "daily_only": True, "max_age_hours": 26, "heartbeat_url": "https://hb.example.test/x"}
        tool = FakeTool(self.root)
        self.assertEqual(self.run_once(tool, config=config), 0)
        self.assertEqual(tool.calls[0][0][:5], ["export", "--source", "home", "--tier", "daily"])
        calls, pings = len(tool.calls), len(self.pings)
        self.assertEqual(self.run_once(tool, config=config), 0)
        self.assertEqual(len(tool.calls), calls, "an hourly slot in daily_only mode runs no export or upload")
        self.assertEqual(len(self.pings), pings, "a skipped run sends no heartbeat")
        status = sched.load_status()
        self.assertEqual(status["skipped_hourly_runs"], 1)
        self.assertIsNone(status["last_error"])
        next_day = T0 + datetime.timedelta(days=1)
        self.assertEqual(self.run_once(tool, clock=lambda: next_day, config=config), 0)
        self.assertEqual(tool.calls[-2][0][4], "daily")
        self.assertEqual(sched.check(config, clock=lambda: next_day + datetime.timedelta(hours=20)), 0)

    def test_daily_only_requires_a_daily_staleness_limit(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"enabled": True, "daily_only": True}, handle)
        with self.assertRaisesRegex(sched.ScheduleError, "at least 25"):
            sched.load_config(handle.name)
        with open(handle.name, "w") as stream:
            json.dump({"enabled": True, "daily_only": "yes", "max_age_hours": 26}, stream)
        with self.assertRaisesRegex(sched.ScheduleError, "true or false"):
            sched.load_config(handle.name)
        os.unlink(handle.name)

    def test_success_records_sanitized_status_and_rpo(self):
        self.assertEqual(self.run_once(FakeTool(self.root)), 0)
        status = sched.load_status()
        success = status["last_success"]
        self.assertEqual(success["snapshot_to_durable_seconds"], 30.0)  # 14:05:10 snapshot -> 14:05:40 durable
        self.assertEqual(success["objects"]["postgres.dump.age"], {"key": "postgres/hourly/home-x/postgres.dump.age",
                                                                   "version_id": "v1", "size": 9})
        self.assertNotIn("sha256", json.dumps(status))
        self.assertIsNone(status["last_error"])
        self.assertEqual(status["consecutive_failures"], 0)
        self.assertEqual(oct(os.stat(sched.STATUS_FILE).st_mode & 0o777), "0o600")
        self.assertEqual(self.pings, [])  # no heartbeat URL configured
        self.assertEqual(self.notifications, [])

    def test_heartbeat_only_after_a_verified_upload(self):
        config = {**self.config, "heartbeat_url": "https://heartbeat.example.test/abc"}
        self.assertEqual(self.run_once(FakeTool(self.root, fail="upload"), config=config), 1)
        self.assertEqual(self.pings, [])
        self.assertEqual(self.notifications, [True])
        status = sched.load_status()
        self.assertIn("upload failed", status["last_error"])
        self.assertEqual(status["consecutive_failures"], 1)
        self.assertNotIn("last_daily_utc_date", status)  # the failed daily attempt is retried next hour
        self.assertEqual(self.run_once(FakeTool(self.root), config=config), 1 - 1)
        self.assertEqual(self.pings, ["https://heartbeat.example.test/abc"])
        self.assertTrue(sched.load_status()["last_heartbeat_ok"])
        self.assertEqual(sched.load_status()["consecutive_failures"], 0)

    def test_export_failure_does_not_upload(self):
        tool = FakeTool(self.root, fail="export")
        self.assertEqual(self.run_once(tool), 1)
        self.assertEqual([c[0][0] for c in tool.calls], ["export"])
        self.assertEqual(self.notifications, [True])

    def test_disabled_configuration_fails_loudly(self):
        tool = FakeTool(self.root)
        self.assertEqual(self.run_once(tool, config={**self.config, "enabled": False}), 1)
        self.assertEqual(tool.calls, [])
        self.assertIn("disabled", sched.load_status()["last_error"])

    def test_prune_removes_only_old_uploaded_tiered_exports(self):
        def export_dir(name, tier, age_days, receipt=True):
            d = self.root / name
            d.mkdir()
            manifest = {"created_at": (T0 - datetime.timedelta(days=age_days)).isoformat()}
            if tier:
                manifest["tier"] = tier
            (d / "manifest.json").write_text(json.dumps(manifest))
            if receipt:
                (d / "upload-receipt.json").write_text("{}")
            return d
        old_hourly = export_dir("export-20260910T000000Z", "hourly", 5)
        old_daily = export_dir("export-20260911T000000Z", "daily", 4)
        fresh = export_dir("export-20260917T000000Z", "hourly", 1)
        hm3 = export_dir("export-20260915T150228Z", None, 30)
        unreceipted = export_dir("export-20260901T000000Z", "hourly", 16, receipt=False)
        restore = self.root / "restore-20260915T150323Z"
        restore.mkdir()
        removed = sched.prune_exports(self.root, 2, T0)
        self.assertEqual(removed, [old_hourly.name, old_daily.name])
        for kept in (fresh, hm3, unreceipted, restore):
            self.assertTrue(kept.exists(), kept)

    def test_check_flags_stale_or_failed_runs(self):
        self.assertEqual(sched.check(self.config, clock=lambda: T0), 1)  # nothing recorded yet
        self.assertEqual(self.run_once(FakeTool(self.root)), 0)
        self.assertEqual(sched.check(self.config, clock=lambda: T0 + datetime.timedelta(hours=1)), 0)
        self.assertEqual(sched.check(self.config, clock=lambda: T0 + datetime.timedelta(hours=3)), 1)
        self.assertEqual(self.run_once(FakeTool(self.root, fail="upload")), 1)
        self.assertEqual(sched.check(self.config, clock=lambda: T0), 1)

    def test_configuration_validation(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"enabled": True, "heartbeat_url": "http://plain.example.test/x"}, handle)
        with self.assertRaisesRegex(sched.ScheduleError, "https"):
            sched.load_config(handle.name)
        with open(handle.name, "w") as stream:
            json.dump({"enabled": True, "keep_local_days": 0}, stream)
        with self.assertRaisesRegex(sched.ScheduleError, "positive integer"):
            sched.load_config(handle.name)
        with open(handle.name, "w") as stream:
            json.dump({"enabled": True, "password": "x"}, stream)
        with self.assertRaisesRegex(sched.ScheduleError, "unexpected configuration keys"):
            sched.load_config(handle.name)
        os.unlink(handle.name)

    def test_plist_runs_the_installed_copy_hourly(self):
        import plistlib
        plist = plistlib.loads((ROOT / "dev.driftplain.home-server-backup.plist").read_bytes())
        self.assertEqual(plist["Label"], "dev.driftplain.home-server-backup")
        self.assertEqual(plist["StartInterval"], 3600)
        self.assertTrue(plist["RunAtLoad"])
        args = plist["ProgramArguments"]
        self.assertTrue(args[0].endswith("/home-server-identity/venv/bin/python3"))
        self.assertTrue(args[1].endswith("/home-server-backups/bin/home-server-backup-schedule.py"))
        self.assertEqual(args[2:], ["run", "--config", "/Users/steve/.local/share/driftplain/home-server-backups/schedule.json"])
        self.assertEqual(plist["Umask"], 0o077)
        # launchd starts agents with a minimal PATH; age comes from Homebrew.
        self.assertTrue(plist["EnvironmentVariables"]["PATH"].startswith("/opt/homebrew/bin:"))
        self.assertNotIn("Disabled", plist)  # the operator profile needs no Keychain: it can run unattended


if __name__ == "__main__":
    unittest.main()
