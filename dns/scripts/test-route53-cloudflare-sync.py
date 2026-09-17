#!/usr/bin/env python3
"""Focused tests for route53-cloudflare-sync.py (no AWS or network: a fake runner answers)."""
import importlib.util
import json
import pathlib
import subprocess
import unittest
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("sync", HERE / "route53-cloudflare-sync.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

NLB = "ad943b23d921d4914b93737030722b00-99ffa2a12cf35e54.elb.ap-south-1.amazonaws.com."
ZONES = {"HostedZones": [
    {"Id": "/hostedzone/ZDRIFT", "Name": "driftplain.dev.", "Config": {"PrivateZone": False}},
    {"Id": "/hostedzone/ZPRIV", "Name": "internal.example.", "Config": {"PrivateZone": True}},
]}
RRSETS = {"ResourceRecordSets": [
    {"Name": "driftplain.dev.", "Type": "A", "AliasTarget": {"DNSName": NLB, "EvaluateTargetHealth": False, "HostedZoneId": "ZNLB"}},
    {"Name": "driftplain.dev.", "Type": "NS", "TTL": 172800, "ResourceRecords": [
        {"Value": "ns-1941.awsdns-50.co.uk."}, {"Value": "ns-393.awsdns-49.com."}]},
    {"Name": "driftplain.dev.", "Type": "SOA", "TTL": 900, "ResourceRecords": [{"Value": "ns-1941.awsdns-50.co.uk. awsdns-hostmaster.amazon.com. 1 7200 900 1209600 86400"}]},
    {"Name": "driftplain.dev.", "Type": "TXT", "TTL": 300, "ResourceRecords": [{"Value": '"google-site-verification=abc"'}]},
    {"Name": "api.driftplain.dev.", "Type": "A", "AliasTarget": {"DNSName": NLB, "EvaluateTargetHealth": False, "HostedZoneId": "ZNLB"}},
    {"Name": "_short.driftplain.dev.", "Type": "TXT", "TTL": 30, "ResourceRecords": [{"Value": '"a"'}, {"Value": '"b"'}]},
]}


class FakeRunner:
    def __init__(self, live_ns):
        self.live_ns = live_ns
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[0] == "dig":
            out = "\n".join(self.live_ns) + "\n" if cmd[2] == "NS" else ""
            return subprocess.CompletedProcess(cmd, 0, out, "")
        assert cmd[:6] == ["aws", "--profile", "saa", "--output", "json", "route53"], cmd
        op = cmd[6]
        payload = {"list-hosted-zones": ZONES, "list-resource-record-sets": RRSETS,
                   "get-dnssec": {"Status": {"ServeSignature": "NOT_SIGNING"}}}[op]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")


def export_with(live_ns):
    runner = FakeRunner(live_ns)
    export = sync.export_zones(sync.AwsCli("saa", runner), clock=lambda: datetime(2026, 9, 17, tzinfo=timezone.utc))
    return export, runner


class ExportTests(unittest.TestCase):
    def test_export_is_read_only_and_skips_private_zones(self):
        export, runner = export_with(["ns-393.awsdns-49.com.", "ns-1941.awsdns-50.co.uk."])
        self.assertEqual([z["name"] for z in export["zones"]], ["driftplain.dev"])
        ops = sorted({c[6] for c in runner.calls if c[0] == "aws"})
        self.assertEqual(ops, ["get-dnssec", "list-hosted-zones", "list-resource-record-sets"])
        for call in runner.calls:
            self.assertFalse(any(word in " ".join(call) for word in ("change", "create", "delete", "update")))
        zone = export["zones"][0]
        self.assertEqual(zone["dnssec"], "NOT_SIGNING")
        self.assertTrue(zone["delegated_to_route53"])
        self.assertEqual(zone["delegation"]["ds"], [])
        self.assertEqual(export["exported_at"], "2026-09-17T00:00:00Z")

    def test_delegation_elsewhere_is_reported(self):
        export, _ = export_with(["ada.ns.cloudflare.com.", "bob.ns.cloudflare.com."])
        self.assertFalse(export["zones"][0]["delegated_to_route53"])

    def test_summary_masks_the_google_proof(self):
        export, _ = export_with([])
        text = sync.summarise(export)
        self.assertNotIn("google-site-verification=abc", text)
        self.assertIn("<google verification, 30 chars>", text)
        self.assertIn("ALIAS " + NLB.rstrip("."), text)


class RenderTests(unittest.TestCase):
    def test_twins_mirror_every_non_provider_record(self):
        export, _ = export_with([])
        rendered = sync.render_records(export)["zone_records"]["driftplain.dev"]
        by_key = {(r["name"], r["type"], r["content"]): r for r in rendered}
        self.assertNotIn(("driftplain.dev", "NS", "ns-393.awsdns-49.com."), by_key)
        self.assertFalse(any(r["type"] in ("NS", "SOA") for r in rendered))
        apex = by_key["driftplain.dev", "CNAME", NLB.rstrip(".")]
        self.assertEqual((apex["ttl"], apex["proxied"]), (300, False))
        self.assertIn("api.driftplain.dev", {r["name"] for r in rendered if r["type"] == "CNAME"})
        txt = by_key["driftplain.dev", "TXT", '"google-site-verification=abc"']
        self.assertEqual(txt["ttl"], 300)
        self.assertEqual({r["content"] for r in rendered if r["name"] == "_short.driftplain.dev"}, {'"a"', '"b"'})
        self.assertTrue(all(r["ttl"] >= 60 for r in rendered), "Cloudflare TTL floor")
        self.assertFalse(any(r["proxied"] for r in rendered), "AWS-origin mirrors stay DNS-only")
        self.assertEqual(len(rendered), 5)


class DiffTests(unittest.TestCase):
    def test_diff_is_clean_for_the_rendered_file_and_flags_drift(self):
        export, _ = export_with([])
        rendered = sync.render_records(export)
        clean = sync.diff_records(export, rendered)
        self.assertEqual((clean["missing_in_records_file"], clean["extra_in_records_file"]), ([], []))
        drifted = json.loads(json.dumps(rendered))
        drifted["zone_records"]["driftplain.dev"][0]["proxied"] = True
        drifted["zone_records"]["driftplain.dev"].append(
            {"name": "extra.driftplain.dev", "type": "A", "content": "203.0.113.9", "ttl": 60, "proxied": False})
        result = sync.diff_records(export, drifted)
        self.assertEqual(len(result["missing_in_records_file"]), 1)
        self.assertEqual({r[1] for r in result["extra_in_records_file"]},
                         {rendered["zone_records"]["driftplain.dev"][0]["name"], "extra.driftplain.dev"})

    def test_cli_diff_exit_codes(self):
        import tempfile
        export, _ = export_with([])
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            (p / "export.json").write_text(json.dumps(export))
            self.assertEqual(sync.main(["render", "--export", str(p / "export.json"), "--out", str(p / "records.json")]), 0)
            self.assertEqual(sync.main(["diff", "--export", str(p / "export.json"), "--records", str(p / "records.json")]), 0)
            (p / "empty.json").write_text('{"zone_records": {}}')
            self.assertEqual(sync.main(["diff", "--export", str(p / "export.json"), "--records", str(p / "empty.json")]), 1)


if __name__ == "__main__":
    unittest.main()
