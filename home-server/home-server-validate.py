#!/usr/bin/env python3
"""E21/HM4 — isolated validation of the home app against the restored database (Mac operator).

The home ingress is a ClusterIP Service (no LAN/public exposure). This tool opens ONE bounded
SSH port-forward from the Mac to that ClusterIP:443 on the home node, then drives the app
through the F5 ingress with curl, mapping the private hostnames to the forwarded port
(`--resolve <host>:<port>:127.0.0.1`) and trusting the home CA (`home-server-ca`) only.

Checks (the HM4 acceptance): API `/healthz` + `/readyz`; the frontend index and its runtime
`config.js` pointing at the private API host; password login for the EXISTING demo user;
`/auth/me`; owner isolation (the user's projects list, then foreign project ids → 403/404 and
unauthenticated → 401); the savings/quality dashboard and a run's findings drill-in; CI ingest
with an EXISTING project token when one is supplied through the environment (never minted here;
a bogus token must be rejected). Nothing is seeded; the only write is the optional, clearly
labelled CI run (`jenkins_build_id` hm4-validation-<stamp>) when a token is supplied.

`--edge app=<host>,api=<host>` (HM7) runs the same checks through the public Cloudflare edge
instead of the port-forward: the private hostnames map to the public pair, the system trust
store verifies Cloudflare's certificate, and two edge-only checks are added (a CORS preflight
from the public app origin must be allowed; the API must answer uncached, `cf-cache-status`
DYNAMIC). The password still comes from the home Secret over SSH.

Secrets: the demo login password is read from the home Secret over kubectl into memory only
and sent in the login body; tokens live in memory; nothing secret is printed, logged or written.
The JSON summary holds booleans, status codes, ids and dashboard aggregates only.
"""
import argparse
import base64
import datetime
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent


def load_database_module():
    spec = importlib.util.spec_from_file_location("home_server_database", ROOT / "home-server-database.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


db = load_database_module()

APP_HOST = "app.home-server.driftplain.dev"
API_HOST = "api.home-server.driftplain.dev"
INGRESS_NAMESPACE, INGRESS_SERVICE = "nginx-ingress", "nginx-ingress-controller"
CA_NAMESPACE, CA_SECRET = "cert-manager", "home-server-ca"
DEMO_EMAIL = "stevelevit230@gmail.com"
CI_TOKEN_ENV = "HOME_SERVER_CI_TOKEN"
CURL_TIMEOUT = 20


class ValidationError(Exception):
    pass


# ── transport ─────────────────────────────────────────────────────────────────────

class CurlClient:
    """HTTPS through the forwarded port with host mapping + the home CA; bodies stay in memory."""

    def __init__(self, port, ca_path):
        self.port, self.ca_path = port, ca_path

    def request(self, method, host, path, headers=None, body=None):
        argv = ["curl", "-sS", "--max-time", str(CURL_TIMEOUT), "--cacert", str(self.ca_path),
                "--resolve", f"{host}:{self.port}:127.0.0.1", "-X", method,
                "-o", "-", "-w", "\n%{http_code}", f"https://{host}:{self.port}{path}"]
        for key, value in (headers or {}).items():
            argv += ["-H", f"{key}: {value}"]
        if body is not None:
            argv += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
        try:
            result = subprocess.run(argv, input=(json.dumps(body).encode() if body is not None else None),
                                    capture_output=True, timeout=CURL_TIMEOUT + 5)
        except subprocess.TimeoutExpired:
            raise ValidationError(f"TIMEOUT: {method} {host}{path}") from None
        if result.returncode:
            raise ValidationError(f"curl failed for {method} {host}{path} (exit {result.returncode}); output suppressed")
        payload, _, code = result.stdout.rpartition(b"\n")
        return int(code), payload


class EdgeClient:
    """HTTPS through the public edge: private hostnames → the public pair, system trust store,
    response headers kept (lower-cased) for the edge-only checks; bodies stay in memory."""

    def __init__(self, host_map, resolve_ip=None):
        self.host_map, self.resolve_ip = host_map, resolve_ip
        self.last_headers = {}

    def request(self, method, host, path, headers=None, body=None):
        public = self.host_map[host]
        argv = ["curl", "-sS", "--max-time", str(CURL_TIMEOUT), "-X", method, "-D", "-",
                "-o", "-", "-w", "\n%{http_code}", f"https://{public}{path}"]
        if self.resolve_ip:  # a resolver still holding the pre-cutover negative answer; TLS is still verified
            argv += ["--resolve", f"{public}:443:{self.resolve_ip}"]
        for key, value in (headers or {}).items():
            argv += ["-H", f"{key}: {value}"]
        if body is not None:
            argv += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
        try:
            result = subprocess.run(argv, input=(json.dumps(body).encode() if body is not None else None),
                                    capture_output=True, timeout=CURL_TIMEOUT + 5)
        except subprocess.TimeoutExpired:
            raise ValidationError(f"TIMEOUT: {method} {public}{path}") from None
        if result.returncode:
            raise ValidationError(f"curl failed for {method} {public}{path} (exit {result.returncode}); output suppressed")
        raw, _, code = result.stdout.rpartition(b"\n")
        head, _, payload = raw.partition(b"\r\n\r\n")
        self.last_headers = parse_headers(head)
        return int(code), payload


def parse_headers(head):
    """`curl -D -` block → {lower-name: value}; the status line is skipped."""
    out = {}
    for line in head.decode(errors="replace").split("\r\n")[1:]:
        name, sep, value = line.partition(":")
        if sep:
            out[name.strip().lower()] = value.strip()
    return out


def parse_edge_hosts(spec):
    """`app=<host>,api=<host>` → {private app host: public app host, private api host: public api host}."""
    pairs = dict(item.split("=", 1) for item in spec.split(",") if "=" in item)
    if set(pairs) != {"app", "api"} or not all(pairs.values()):
        raise ValidationError("--edge needs exactly app=<host>,api=<host>")
    return {APP_HOST: pairs["app"], API_HOST: pairs["api"]}


def run_edge_checks(request, headers_after, app_origin):
    """Edge-only checks: the browser at the public app origin may call the API (CORS preflight
    allowed), and API answers are never served from the Cloudflare cache."""
    results, ok = {}, True
    code, _ = request("OPTIONS", API_HOST, "/auth/login",
                      headers={"Origin": app_origin, "Access-Control-Request-Method": "POST",
                               "Access-Control-Request-Headers": "content-type"})
    allowed = headers_after().get("access-control-allow-origin")
    passed = code in (200, 204) and allowed == app_origin
    results["cors_preflight_from_public_app_origin"] = {"pass": passed, "status": code, "allow_origin": allowed}
    ok = ok and passed
    code, _ = request("GET", API_HOST, "/healthz")
    cache = headers_after().get("cf-cache-status")
    passed = code == 200 and cache == "DYNAMIC" and headers_after().get("server") == "cloudflare"
    results["api_uncached_through_cloudflare"] = {"pass": passed, "status": code, "cf_cache_status": cache}
    ok = ok and passed
    return ok, results


def wait_port(port, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(1)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


class Forward:
    """`ssh -N -L 127.0.0.1:<port>:<clusterIP>:443 home-server`, bounded start, always closed."""

    def __init__(self, port, cluster_ip):
        self.port, self.cluster_ip, self.process = port, cluster_ip, None

    def __enter__(self):
        self.process = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ExitOnForwardFailure=yes",
                                         "-N", "-L", f"127.0.0.1:{self.port}:{self.cluster_ip}:443", db.TARGET_SSH_ALIAS],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not wait_port(self.port, 30):
            self.__exit__(None, None, None)
            raise ValidationError("TIMEOUT: the SSH port-forward did not open within 30s")
        return self

    def __exit__(self, *_):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


# ── pure helpers (unit-tested) ─────────────────────────────────────────────────────

def parse_json(payload):
    try:
        return json.loads(payload or b"null")
    except ValueError:
        return None


def foreign_probe_ids(own_ids, extra=3):
    """Project ids to probe for owner isolation: every id up to max(own)+extra that is not ours."""
    top = (max(own_ids) if own_ids else 0) + extra
    return [i for i in range(1, top + 1) if i not in set(own_ids)]


def config_js_points_at(body, api_host):
    text = body.decode(errors="replace")
    return "window.__APP_CONFIG__" in text and f'"https://{api_host}"' in text


def run_checks(request, password, ci_token=None, stamp=None, config_api_host=API_HOST):
    """Drive the acceptance checks through `request(method, host, path, headers, body)`.

    `config_api_host` is the public API host the frontend's runtime config must name: the
    private host during isolated validation (HM4), the selected `runtimeHostSet` API host once
    the umbrella routes a public pair (staging in HM5, `api.driftplain.dev` after HM7).
    """
    results, ok = {}, True

    def record(name, passed, **facts):
        nonlocal ok
        results[name] = {"pass": bool(passed), **facts}
        ok = ok and bool(passed)

    code, body = request("GET", API_HOST, "/healthz")
    record("api_healthz", code == 200 and (parse_json(body) or {}).get("status") == "ok", status=code)
    code, body = request("GET", API_HOST, "/readyz")
    ready = parse_json(body) or {}
    record("api_readyz", code == 200 and ready.get("db") == "ok", status=code, db=ready.get("db"))
    code, body = request("GET", APP_HOST, "/")
    record("app_index", code == 200 and b"<div id=\"root\"" in body, status=code, bytes=len(body))
    code, body = request("GET", APP_HOST, "/config.js")
    record("app_config_js", code == 200 and config_js_points_at(body, config_api_host), status=code,
           expected_api_host=config_api_host)

    code, body = request("GET", API_HOST, "/projects")
    record("projects_unauthenticated_rejected", code == 401, status=code)

    code, body = request("POST", API_HOST, "/auth/login", body={"email": DEMO_EMAIL, "password": password})
    token = (parse_json(body) or {}).get("accessToken") if code == 200 else None
    record("password_login_existing_user", code == 200 and bool(token), status=code)
    if not token:
        return ok, results
    auth = {"Authorization": f"Bearer {token}"}

    code, body = request("GET", API_HOST, "/auth/me", headers=auth)
    me = parse_json(body) or {}
    record("auth_me", code == 200 and me.get("email") == DEMO_EMAIL, status=code, user_id=me.get("id"))

    code, body = request("GET", API_HOST, "/projects", headers=auth)
    projects = parse_json(body) if code == 200 else None
    own_ids = [p["id"] for p in (projects or [])]
    record("projects_list", code == 200 and isinstance(projects, list) and bool(own_ids), status=code,
           own_project_ids=own_ids, names=[p.get("name") for p in (projects or [])])
    if not own_ids:
        return ok, results

    probes = {}
    for pid in foreign_probe_ids(own_ids):
        code, _ = request("GET", API_HOST, f"/projects/{pid}", headers=auth)
        probes[pid] = code
    record("owner_isolation", all(code in (403, 404) for code in probes.values()) and bool(probes),
           foreign_ids_probed=sorted(probes), statuses=sorted(set(probes.values())))

    project_id = own_ids[0]
    code, body = request("GET", API_HOST, f"/projects/{project_id}/savings", headers=auth)
    savings = parse_json(body) if code == 200 else None
    kpis = (savings or {}).get("kpis") or {}
    runs = (savings or {}).get("runs") or []
    record("savings_dashboard", code == 200 and "kpis" in (savings or {}), status=code, project_id=project_id,
           runs_count=kpis.get("runsCount"), banked_runs=kpis.get("bankedRuns"), saved_pct=kpis.get("savedPct"),
           quality_status=kpis.get("qualityStatus"), acceptance_rate=kpis.get("acceptanceRate"),
           selected_model=(savings or {}).get("selectedModel"), baseline_model=(savings or {}).get("baselineModel"))
    if runs:
        run_id = runs[0]["id"]
        code, body = request("GET", API_HOST, f"/projects/{project_id}/runs/{run_id}/findings", headers=auth)
        findings = parse_json(body) if code == 200 else None
        record("quality_findings_drill_in", code == 200 and isinstance(findings, dict), status=code, run_id=run_id,
               findings_count=len(findings.get("findings", [])) if isinstance(findings, dict) else None)

    code, _ = request("POST", API_HOST, f"/projects/{project_id}/ci-runs", headers={"X-CI-Token": "not-a-real-token"},
                      body={"jenkinsBuildId": "hm4-bogus", "findings": [], "tokensIn": 1, "tokensOut": 1, "model": "x"})
    record("ci_ingest_bogus_token_rejected", code == 401, status=code)
    if ci_token:
        build = f"hm4-validation-{stamp or db.stamp(db.now())}"
        code, body = request("POST", API_HOST, f"/projects/{project_id}/ci-runs", headers={"X-CI-Token": ci_token},
                             body={"jenkinsBuildId": build, "findings": [], "tokensIn": 10, "tokensOut": 5,
                                   "model": (savings or {}).get("selectedModel") or "fake-model"})
        out = parse_json(body) or {}
        record("ci_ingest_existing_token", code == 201 and out.get("jenkinsBuildId") == build, status=code,
               run_id=out.get("id"), jenkins_build_id=build)
    else:
        results["ci_ingest_existing_token"] = {"pass": None, "skipped": f"no existing token supplied via {CI_TOKEN_ENV}"}
    return ok, results


# ── operator entrypoint ────────────────────────────────────────────────────────────

def home_json(remote_prefix, args):
    return db.kube_json(remote_prefix(args), [])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--out", help="private JSON result file (default: backups root)")
    parser.add_argument("--config-api-host", default=API_HOST,
                        help="API host the frontend config.js must name (the umbrella's runtimeHostSet API host)")
    parser.add_argument("--edge", metavar="app=<host>,api=<host>",
                        help="run through the public Cloudflare edge with these public hostnames (HM7)")
    parser.add_argument("--edge-resolve", metavar="IP",
                        help="pin the public hostnames to this Cloudflare anycast address (certificate still verified)")
    args = parser.parse_args(argv)
    edge_hosts = parse_edge_hosts(args.edge) if args.edge else None
    started = db.now()
    _, remote_prefix, target = db.target_shell_without_cluster()
    service = home_json(remote_prefix, ["-n", INGRESS_NAMESPACE, "get", "svc", INGRESS_SERVICE])
    cluster_ip = service["spec"]["clusterIP"]
    if service["spec"].get("type") != "ClusterIP":
        raise ValidationError("the home ingress Service is not ClusterIP; refusing")
    app_secret = home_json(remote_prefix, ["-n", db.NAMESPACE, "get", "secret", db.APP_SECRET])["data"]
    password = base64.b64decode(app_secret["DEMO_SEED_PASSWORD"]).decode()
    ci_token = os.environ.get(CI_TOKEN_ENV) or None
    if edge_hosts:
        client = EdgeClient(edge_hosts, args.edge_resolve)
        ok, results = run_checks(client.request, password, ci_token, db.stamp(started),
                                 config_api_host=args.config_api_host)
        edge_ok, edge_results = run_edge_checks(client.request, lambda: client.last_headers,
                                               f"https://{edge_hosts[APP_HOST]}")
        ok, results = ok and edge_ok, {**results, **edge_results}
        hosts = {"app": edge_hosts[APP_HOST], "api": edge_hosts[API_HOST], "config_api": args.config_api_host,
                 "path": "public edge (Cloudflare → tunnel → ingress)", "resolved_to": args.edge_resolve}
    else:
        ca_pem = base64.b64decode(home_json(remote_prefix, ["-n", CA_NAMESPACE, "get", "secret", CA_SECRET])["data"]["ca.crt"])
        with tempfile.TemporaryDirectory(prefix="hm4-validate-") as tmp:
            ca_path = Path(tmp) / "home-server-ca.crt"
            ca_path.write_bytes(ca_pem)
            with Forward(args.port, cluster_ip):
                ok, results = run_checks(CurlClient(args.port, ca_path).request, password, ci_token, db.stamp(started),
                                         config_api_host=args.config_api_host)
        hosts = {"app": APP_HOST, "api": API_HOST, "config_api": args.config_api_host,
                 "path": "SSH port-forward to the ClusterIP ingress"}
    finished = db.now()
    summary = {"target": target, "ingress_cluster_ip": cluster_ip, "hosts": hosts,
               "started_at": started.isoformat(), "seconds": round((finished - started).total_seconds(), 3),
               "all_pass": ok, "checks": results}
    out = Path(args.out) if args.out else db.private_dir(db.BACKUPS_ROOT) / f"hm4-validation-{db.stamp(started)}.json"
    db.write_private(out, json.dumps(summary, indent=2, sort_keys=True).encode())
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except (ValidationError, db.DatabaseError) as error:
        print(f"ERROR: {error}")
        raise SystemExit(1)
