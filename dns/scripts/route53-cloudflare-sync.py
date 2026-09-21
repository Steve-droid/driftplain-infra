#!/usr/bin/env python3
"""route53-cloudflare-sync.py — E21/HM5 Route 53 export → Cloudflare twin records → drift diff.

Read-only everywhere: `aws route53 list-hosted-zones / list-resource-record-sets / get-dnssec`
(profile `saa`) and public `dig` lookups at 1.1.1.1 for the live delegation. Nothing here
mutates DNS, Cloudflare or AWS. Sub-commands:

  export --out export.json                 sanitized snapshot of every public zone: records,
                                           DNSSEC signing status, live NS/DS delegation
  render --export export.json --out cloudflare/records.tfvars.json
                                           the Cloudflare twin of every non-provider record
                                           (DNS-only; a Route 53 alias → CNAME to its target)
  diff   --export export.json --records cloudflare/records.tfvars.json
                                           exit 1 when the committed twin set drifted from
                                           the live zones (missing / extra / changed)

The twin file is a Terraform JSON var-file passed explicitly to the `cloudflare/` root
(`-var-file=dev.tfvars -var-file=records.tfvars.json`). NS and SOA at the apex are provider-
owned and never mirrored; the registrar delegation change is a separate explicit approval.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

MIN_TTL = 60  # Cloudflare's floor for DNS-only records
ALIAS_TTL = 300  # Route 53 aliases carry no TTL; mirror them at the zone's TXT TTL
PROVIDER_OWNED = {"NS", "SOA"}


class AwsCli:
    """Thin runner around read-only `aws route53` calls (tests inject a fake)."""

    def __init__(self, profile: str, runner=subprocess.run):
        self.profile = profile
        self.runner = runner

    def json(self, *args: str):
        cmd = ["aws", "--profile", self.profile, "--output", "json", "route53", *args]
        result = self.runner(cmd, text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    def dig(self, rtype: str, name: str, resolver: str = "1.1.1.1") -> list[str]:
        result = self.runner(["dig", "+short", rtype, name, f"@{resolver}"], text=True, capture_output=True, check=True)
        return sorted(line.strip().lower() for line in result.stdout.splitlines() if line.strip())


def normalise_name(name: str) -> str:
    return name.rstrip(".").lower()


def export_zones(cli: AwsCli, clock=lambda: datetime.now(timezone.utc)) -> dict:
    zones = []
    for zone in cli.json("list-hosted-zones")["HostedZones"]:
        if zone["Config"].get("PrivateZone"):
            continue
        zone_id = zone["Id"].split("/")[-1]
        name = normalise_name(zone["Name"])
        rrsets = cli.json("list-resource-record-sets", "--hosted-zone-id", zone_id)["ResourceRecordSets"]
        records = []
        route53_ns: list[str] = []
        for rr in rrsets:
            rr_name = normalise_name(rr["Name"])
            entry = {"name": rr_name, "type": rr["Type"]}
            if "AliasTarget" in rr:
                entry["alias"] = normalise_name(rr["AliasTarget"]["DNSName"])
                entry["evaluate_target_health"] = bool(rr["AliasTarget"].get("EvaluateTargetHealth"))
            else:
                entry["ttl"] = rr.get("TTL")
                entry["values"] = [v["Value"] for v in rr.get("ResourceRecords", [])]
                if rr["Type"] == "NS" and rr_name == name:
                    route53_ns = sorted(normalise_name(v) for v in entry["values"])
            records.append(entry)
        dnssec = cli.json("get-dnssec", "--hosted-zone-id", zone_id)["Status"]["ServeSignature"]
        live_ns = cli.dig("NS", name)
        live_ds = cli.dig("DS", name)
        zones.append({
            "name": name,
            "zone_id": zone_id,
            "dnssec": dnssec,
            "route53_name_servers": route53_ns,
            "delegation": {"ns": live_ns, "ds": live_ds},
            "delegated_to_route53": sorted(n.rstrip(".") for n in live_ns) == [n.rstrip(".") for n in route53_ns],
            "records": records,
        })
    return {
        "tool": "route53-cloudflare-sync",
        "exported_at": clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zones": sorted(zones, key=lambda z: z["name"]),
    }


def twins(zone: str, record: dict) -> list[dict]:
    """The Cloudflare record(s) that reproduce one Route 53 record set; [] when provider-owned."""
    if record["type"] in PROVIDER_OWNED and record["name"] == zone:
        return []
    if "alias" in record:
        return [{
            "name": record["name"],
            "type": "CNAME",
            "content": record["alias"],
            "ttl": ALIAS_TTL,
            "proxied": False,
            "comment": "Mirror of a Route 53 alias (retained zone twin; the home tunnel serves the runtime hosts since HM7)",
        }]
    ttl = max(int(record.get("ttl") or MIN_TTL), MIN_TTL)
    return [{
        "name": record["name"],
        "type": record["type"],
        "content": value,
        "ttl": ttl,
        "proxied": False,
        "comment": f"Mirror of the Route 53 {record['type']} record (retained zone twin until the HM8 review)",
    } for value in record["values"]]


def render_records(export: dict) -> dict:
    zone_records = {}
    for zone in export["zones"]:
        entries = []
        for record in zone["records"]:
            entries.extend(twins(zone["name"], record))
        zone_records[zone["name"]] = sorted(entries, key=lambda r: (r["name"], r["type"], r["content"]))
    return {"zone_records": zone_records}


def record_key(zone: str, record: dict) -> tuple:
    return (zone, record["name"], record["type"], record["content"], int(record["ttl"]), bool(record["proxied"]))


def diff_records(export: dict, records_file: dict) -> dict:
    expected = {record_key(z, r) for z, rs in render_records(export)["zone_records"].items() for r in rs}
    committed = {record_key(z, r) for z, rs in records_file.get("zone_records", {}).items() for r in rs}
    return {
        "missing_in_records_file": sorted(expected - committed),
        "extra_in_records_file": sorted(committed - expected),
        "zones_exported": sorted(z["name"] for z in export["zones"]),
        "zones_in_records_file": sorted(records_file.get("zone_records", {})),
    }


def summarise(export: dict) -> str:
    lines = []
    for zone in export["zones"]:
        lines.append(f"{zone['name']}  zone {zone['zone_id']}  dnssec={zone['dnssec']}  "
                     f"delegated_to_route53={zone['delegated_to_route53']}  ds={len(zone['delegation']['ds'])}")
        for record in zone["records"]:
            if "alias" in record:
                lines.append(f"  {record['type']:5} {record['name']:28} ALIAS {record['alias']}")
            else:
                shown = [v if not v.startswith('"google-site-verification=') else f"<google verification, {len(v)} chars>"
                         for v in record["values"]]
                lines.append(f"  {record['type']:5} {record['name']:28} ttl={record['ttl']} {shown}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="saa")
    sub = parser.add_subparsers(dest="command", required=True)
    p_export = sub.add_parser("export", help="read-only snapshot of the public Route 53 zones")
    p_export.add_argument("--out", required=True)
    p_render = sub.add_parser("render", help="write the Cloudflare twin records var-file")
    p_render.add_argument("--export", required=True)
    p_render.add_argument("--out", required=True)
    p_diff = sub.add_parser("diff", help="compare the export against the committed twin records")
    p_diff.add_argument("--export", required=True)
    p_diff.add_argument("--records", required=True)
    args = parser.parse_args(argv)

    if args.command == "export":
        export = export_zones(AwsCli(args.profile))
        with open(args.out, "w") as fh:
            json.dump(export, fh, indent=2)
            fh.write("\n")
        print(summarise(export))
        print(f"wrote {args.out}")
        return 0
    with open(args.export) as fh:
        export = json.load(fh)
    if args.command == "render":
        rendered = render_records(export)
        with open(args.out, "w") as fh:
            json.dump(rendered, fh, indent=2)
            fh.write("\n")
        total = sum(len(v) for v in rendered["zone_records"].values())
        print(f"wrote {args.out}: {total} records across {len(rendered['zone_records'])} zones")
        return 0
    with open(args.records) as fh:
        records_file = json.load(fh)
    result = diff_records(export, records_file)
    print(json.dumps(result, indent=2))
    drifted = result["missing_in_records_file"] or result["extra_in_records_file"] \
        or result["zones_exported"] != result["zones_in_records_file"]
    print("DRIFT: the committed twin records differ from the live zones" if drifted
          else "OK: the committed twin records match the live zones")
    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())
