"""HM4 isolated-validation checks driven through a fake transport: no SSH, curl, cluster or
network. Covers the acceptance contract, the isolation probe, and the no-token skip path."""

import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hv = load("home_server_validate", "home-server-validate.py")
TOKEN = "jwt-value-never-printed"
PASSWORD = "demo-password-never-printed"


class FakeApp:
    """A minimal stand-in for the home app behind the ingress."""

    def __init__(self, own_ids=(5, 7), runs=(114,), ci_token="existing-ci-token"):
        self.own_ids, self.runs, self.ci_token = list(own_ids), list(runs), ci_token
        self.calls, self.ingested = [], []

    def request(self, method, host, path, headers=None, body=None):
        self.calls.append((method, host, path))
        headers = headers or {}
        authed = headers.get("Authorization") == f"Bearer {TOKEN}"
        if host == hv.APP_HOST:
            if path == "/":
                return 200, b'<!doctype html><div id="root"></div>'
            if path == "/config.js":
                return 200, f'window.__APP_CONFIG__ = {{ apiBaseUrl: "https://{hv.API_HOST}" }};'.encode()
        if path == "/healthz":
            return 200, b'{"status":"ok"}'
        if path == "/readyz":
            return 200, b'{"status":"ready","db":"ok"}'
        if path == "/auth/login":
            ok = body == {"email": hv.DEMO_EMAIL, "password": PASSWORD}
            return (200, json.dumps({"accessToken": TOKEN, "tokenType": "bearer"}).encode()) if ok else (401, b"{}")
        if path == "/auth/me":
            return (200, json.dumps({"id": 2, "email": hv.DEMO_EMAIL}).encode()) if authed else (401, b"{}")
        if path == "/projects":
            return (200, json.dumps([{"id": i, "name": f"p{i}"} for i in self.own_ids]).encode()) if authed else (401, b"{}")
        if path.startswith("/projects/") and path.endswith("/ci-runs"):
            if headers.get("X-CI-Token") != self.ci_token:
                return 401, b"{}"
            self.ingested.append(body)
            return 201, json.dumps({"id": 999, "jenkinsBuildId": body["jenkinsBuildId"]}).encode()
        if path.startswith("/projects/"):
            if not authed:
                return 401, b"{}"
            pid = int(path.split("/")[2])
            if pid not in self.own_ids:
                return 404, b'{"detail":"not found"}'
            if path.endswith("/savings"):
                return 200, json.dumps({"kpis": {"runsCount": len(self.runs), "bankedRuns": 1, "savedPct": 86.13,
                                                 "qualityStatus": "ok", "acceptanceRate": 0.9},
                                        "selectedModel": "Nova 2 Lite", "baselineModel": "Claude Sonnet 4.5",
                                        "runs": [{"id": r} for r in self.runs]}).encode()
            if "/runs/" in path and path.endswith("/findings"):
                return 200, json.dumps({"findings": [{"id": 1}, {"id": 2}]}).encode()
            return 200, json.dumps({"id": pid}).encode()
        return 500, b"unexpected"


class HelperTests(unittest.TestCase):
    def test_foreign_probe_ids_skip_own_and_extend_past_max(self):
        self.assertEqual(hv.foreign_probe_ids([5, 7]), [1, 2, 3, 4, 6, 8, 9, 10])
        self.assertEqual(hv.foreign_probe_ids([]), [1, 2, 3])

    def test_config_js_must_name_the_private_api_host(self):
        self.assertTrue(hv.config_js_points_at(b'window.__APP_CONFIG__ = { apiBaseUrl: "https://api.home-server.driftplain.dev" };', hv.API_HOST))
        self.assertFalse(hv.config_js_points_at(b'window.__APP_CONFIG__ = { apiBaseUrl: "https://api.driftplain.dev" };', hv.API_HOST))
        self.assertFalse(hv.config_js_points_at(b"<html>", hv.API_HOST))


class RunChecksTests(unittest.TestCase):
    def test_full_pass_with_existing_token_writes_one_labelled_run(self):
        app = FakeApp()
        ok, results = hv.run_checks(app.request, PASSWORD, ci_token="existing-ci-token", stamp="20260915T170000Z")
        self.assertTrue(ok, results)
        for name in ("api_healthz", "api_readyz", "app_index", "app_config_js", "projects_unauthenticated_rejected",
                     "password_login_existing_user", "auth_me", "projects_list", "owner_isolation", "savings_dashboard",
                     "quality_findings_drill_in", "ci_ingest_bogus_token_rejected", "ci_ingest_existing_token"):
            self.assertTrue(results[name]["pass"], name)
        self.assertEqual(results["owner_isolation"]["foreign_ids_probed"], [1, 2, 3, 4, 6, 8, 9, 10])
        self.assertEqual(results["savings_dashboard"]["saved_pct"], 86.13)
        self.assertEqual(results["quality_findings_drill_in"]["findings_count"], 2)
        self.assertEqual(app.ingested, [{"jenkinsBuildId": "hm4-validation-20260915T170000Z", "findings": [],
                                         "tokensIn": 10, "tokensOut": 5, "model": "Nova 2 Lite"}])
        dumped = json.dumps(results)
        self.assertNotIn(TOKEN, dumped)
        self.assertNotIn(PASSWORD, dumped)
        self.assertNotIn("existing-ci-token", dumped)

    def test_config_check_follows_the_selected_runtime_api_host(self):
        # HM5/HM7: the umbrella's runtimeHostSet points config.js at a public API host; the
        # validator must be told which one, and the private default still fails against it.
        class Routed(FakeApp):
            def request(self, method, host, path, headers=None, body=None):
                if path == "/config.js":
                    return 200, b'window.__APP_CONFIG__ = { apiBaseUrl: "https://api.driftplain.dev" };'
                return super().request(method, host, path, headers, body)
        ok, results = hv.run_checks(Routed().request, PASSWORD, config_api_host="api.driftplain.dev")
        self.assertTrue(ok)
        self.assertEqual(results["app_config_js"], {"pass": True, "status": 200, "expected_api_host": "api.driftplain.dev"})
        ok, results = hv.run_checks(Routed().request, PASSWORD)
        self.assertFalse(ok)
        self.assertFalse(results["app_config_js"]["pass"])

    def test_edge_mode_maps_hosts_and_adds_the_cors_and_cache_checks(self):
        # HM7: the same checks run through the public edge; the private hostnames map to the
        # public pair and two edge-only facts are recorded (CORS preflight, uncached API).
        hosts = hv.parse_edge_hosts("app=driftplain.dev,api=api.driftplain.dev")
        self.assertEqual(hosts, {hv.APP_HOST: "driftplain.dev", hv.API_HOST: "api.driftplain.dev"})
        with self.assertRaises(hv.ValidationError):
            hv.parse_edge_hosts("app=driftplain.dev")
        self.assertEqual(hv.parse_headers(b"HTTP/2 200\r\nCF-Cache-Status: DYNAMIC\r\nserver: cloudflare\r\n"),
                         {"cf-cache-status": "DYNAMIC", "server": "cloudflare"})

        seen, response_headers = [], {}
        def request(method, host, path, headers=None, body=None):
            seen.append((method, host, path))
            if method == "OPTIONS":
                response_headers.clear(); response_headers.update({"access-control-allow-origin": headers["Origin"]})
                return 200, b""
            response_headers.clear(); response_headers.update({"cf-cache-status": "DYNAMIC", "server": "cloudflare"})
            return 200, b'{"status":"ok"}'
        ok, results = hv.run_edge_checks(request, lambda: response_headers, "https://driftplain.dev")
        self.assertTrue(ok)
        self.assertEqual(results["cors_preflight_from_public_app_origin"],
                         {"pass": True, "status": 200, "allow_origin": "https://driftplain.dev"})
        self.assertEqual(results["api_uncached_through_cloudflare"], {"pass": True, "status": 200, "cf_cache_status": "DYNAMIC"})
        self.assertEqual([m for m, _, _ in seen], ["OPTIONS", "GET"])
        self.assertTrue(all(host == hv.API_HOST for _, host, _ in seen))

        def cached(method, host, path, headers=None, body=None):
            response_headers.clear(); response_headers.update({"cf-cache-status": "HIT", "server": "cloudflare"})
            return 200, b""
        ok, results = hv.run_edge_checks(cached, lambda: response_headers, "https://driftplain.dev")
        self.assertFalse(ok)
        self.assertFalse(results["cors_preflight_from_public_app_origin"]["pass"])
        self.assertFalse(results["api_uncached_through_cloudflare"]["pass"])

    def test_without_a_token_the_ingest_check_is_skipped_not_faked(self):
        app = FakeApp()
        ok, results = hv.run_checks(app.request, PASSWORD)
        self.assertTrue(ok)
        self.assertIsNone(results["ci_ingest_existing_token"]["pass"])
        self.assertEqual(app.ingested, [])
        self.assertTrue(results["ci_ingest_bogus_token_rejected"]["pass"])

    def test_wrong_password_stops_before_any_authenticated_call(self):
        app = FakeApp()
        ok, results = hv.run_checks(app.request, "wrong")
        self.assertFalse(ok)
        self.assertFalse(results["password_login_existing_user"]["pass"])
        self.assertNotIn("auth_me", results)
        self.assertFalse(any(path == "/auth/me" for _, _, path in app.calls))

    def test_isolation_failure_is_reported(self):
        class Leaky(FakeApp):
            def request(self, method, host, path, headers=None, body=None):
                if path == "/projects/1":
                    return 200, b'{"id": 1}'
                return super().request(method, host, path, headers, body)

        ok, results = hv.run_checks(Leaky().request, PASSWORD)
        self.assertFalse(ok)
        self.assertFalse(results["owner_isolation"]["pass"])
        self.assertIn(200, results["owner_isolation"]["statuses"])


if __name__ == "__main__":
    unittest.main()
