#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_compare.py — Phase 5 of POSTGRES_MIGRATION_RUNBOOK

Compares HTTP responses between the old SQLite-backed MES VM and the
new Postgres-backed MES VM, endpoint by endpoint. The two should
produce identical output (modulo timestamps and a few known-volatile
fields) — any difference means the new stack would behave differently
for operators, which gates cutover.

Usage:
  # Compare a fixed set of endpoints
  ./render_compare.py \\
      --old https://34.67.173.228.nip.io \\
      --new https://mes-testing-pg.us-central1-a.example.com \\
      --api-key msplastics-mes-2026-... \\
      --endpoint /api/work-orders \\
      --endpoint /api/health \\
      --endpoint /api/work-orders/WH/MO/01483 \\
      --endpoint /api/work-orders/WH/MO/01572

  # Auto-discover MOs to compare from the WO list
  ./render_compare.py \\
      --old https://... --new https://... --api-key ... \\
      --auto-mo-sample 25

Exit codes:
  0 — all sampled endpoints match (within tolerance)
  1 — at least one endpoint differs
  2 — operational error (one of the hosts unreachable)
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from typing import Any
from urllib.parse import urljoin

try:
    import requests
except ImportError:
    print("ERROR: requests required. pip install requests", file=sys.stderr)
    sys.exit(2)


# Fields known to legitimately differ between the two stacks. We strip
# them before diffing to avoid false-positive drift alerts.
VOLATILE_KEYS = {
    # Anything with these substrings in the key gets erased
    "timestamp", "_at", "elapsed", "duration_ms", "uptime",
    "server_time", "etag", "request_id",
}

# Top-level fields whose order doesn't matter (will sort before diff)
ORDER_INSENSITIVE_LIST_FIELDS = {"work_orders", "rolls", "pallets", "lots"}


def _scrub(obj: Any) -> Any:
    """Recursively erase known-volatile fields. Returns a normalised
    representation suitable for diffing."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if any(s in str(k).lower() for s in VOLATILE_KEYS):
                out[k] = "<VOLATILE>"
            else:
                out[k] = _scrub(v)
        return out
    if isinstance(obj, list):
        scrubbed = [_scrub(x) for x in obj]
        # Sort lists of dicts by a stable key if available
        if scrubbed and isinstance(scrubbed[0], dict):
            for key_candidate in ("id", "wo_number", "pallet_id", "roll_id", "name"):
                if all(key_candidate in x for x in scrubbed):
                    scrubbed.sort(key=lambda x: str(x.get(key_candidate)))
                    break
        return scrubbed
    return obj


def _fetch(host: str, endpoint: str, api_key: str | None,
           timeout: int = 30) -> tuple[int, str, Any | None]:
    """Returns (status_code, raw_text, parsed_json_or_None)."""
    url = urljoin(host.rstrip("/") + "/", endpoint.lstrip("/"))
    headers = {}
    if api_key:
        headers["X-API-KEY"] = api_key
    try:
        r = requests.get(url, headers=headers, timeout=timeout, verify=False)
    except requests.RequestException as e:
        return -1, f"REQUEST_FAILED: {e}", None
    body = r.text
    parsed = None
    if "application/json" in r.headers.get("Content-Type", "").lower():
        try:
            parsed = r.json()
        except ValueError:
            pass
    return r.status_code, body, parsed


def _compare_endpoint(old_host: str, new_host: str, api_key: str | None,
                      endpoint: str) -> dict:
    """Fetch from both, diff, return structured result."""
    old_s, old_body, old_json = _fetch(old_host, endpoint, api_key)
    new_s, new_body, new_json = _fetch(new_host, endpoint, api_key)

    result = {
        "endpoint": endpoint,
        "old_status": old_s,
        "new_status": new_s,
        "status_match": (old_s == new_s),
        "body_size_old": len(old_body),
        "body_size_new": len(new_body),
        "match": False,
    }

    if old_s != new_s:
        result["error"] = (f"status code mismatch: old={old_s}, new={new_s}")
        return result

    if old_json is not None and new_json is not None:
        # JSON comparison after scrubbing
        a = _scrub(old_json)
        b = _scrub(new_json)
        result["match"] = (a == b)
        if not result["match"]:
            # Produce a compact diff summary instead of dumping full bodies
            a_lines = json.dumps(a, indent=2, sort_keys=True, default=str).splitlines()
            b_lines = json.dumps(b, indent=2, sort_keys=True, default=str).splitlines()
            diff = list(difflib.unified_diff(a_lines, b_lines, lineterm="", n=2))
            result["diff_lines_count"] = len(diff)
            result["diff_sample"] = "\n".join(diff[:30])
    else:
        # Text comparison — strip whitespace + volatile patterns
        a = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^\"\s]*", "<TS>", old_body)
        b = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^\"\s]*", "<TS>", new_body)
        result["match"] = (a.strip() == b.strip())
        if not result["match"]:
            diff = list(difflib.unified_diff(
                a.splitlines(), b.splitlines(), lineterm="", n=2
            ))
            result["diff_lines_count"] = len(diff)
            result["diff_sample"] = "\n".join(diff[:30])
    return result


def _auto_discover_mo_endpoints(old_host: str, api_key: str | None,
                                limit: int) -> list[str]:
    """Hit /api/work-orders and pull `limit` random wo_numbers, return
    per-MO endpoint URLs for them."""
    _, _, parsed = _fetch(old_host, "/api/work-orders", api_key)
    if not parsed:
        return []
    wos = parsed if isinstance(parsed, list) else parsed.get("work_orders", [])
    if not wos:
        return []
    # Take first N rather than random so re-runs are reproducible
    sample = wos[:limit]
    return [f"/api/work-orders/{w.get('wo_number')}"
            for w in sample if w.get("wo_number")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old", required=True, help="Base URL of SQLite-backed VM")
    parser.add_argument("--new", required=True, help="Base URL of Postgres-backed VM")
    parser.add_argument("--api-key", default=None, help="X-API-KEY for both hosts")
    parser.add_argument("--endpoint", action="append", default=[],
                        help="Endpoint to compare (e.g. /api/work-orders). Repeatable.")
    parser.add_argument("--auto-mo-sample", type=int, default=0,
                        help="Auto-discover this many MO detail endpoints from /api/work-orders")
    parser.add_argument("--report", default=None,
                        help="Write JSON report to this path")
    parser.add_argument("--show-diffs", action="store_true",
                        help="Print diff snippets to stdout for mismatches")
    args = parser.parse_args(argv)

    # Silence the insecure-https warning so output stays readable; we're
    # hitting nip.io domains with self-signed certs and that's expected.
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

    endpoints = list(args.endpoint)
    if args.auto_mo_sample > 0:
        discovered = _auto_discover_mo_endpoints(args.old, args.api_key,
                                                 args.auto_mo_sample)
        print(f"Auto-discovered {len(discovered)} MO endpoints from --old")
        endpoints.extend(discovered)

    if not endpoints:
        parser.error("provide at least one --endpoint or --auto-mo-sample N")

    print(f"Comparing {len(endpoints)} endpoint(s) — old vs new")
    print()

    results = []
    matches = 0
    mismatches = 0
    for ep in endpoints:
        t0 = time.time()
        r = _compare_endpoint(args.old, args.new, args.api_key, ep)
        r["wall_seconds"] = round(time.time() - t0, 2)
        results.append(r)
        flag = "OK   " if r["match"] else "DIFF "
        if r["match"]:
            matches += 1
        else:
            mismatches += 1
        size_info = (f"old={r['body_size_old']}, new={r['body_size_new']}"
                     if r["status_match"] else f"status_old={r['old_status']} status_new={r['new_status']}")
        print(f"  [{flag}] {ep:50}  {size_info}  ({r['wall_seconds']}s)")
        if args.show_diffs and not r["match"] and "diff_sample" in r:
            print("    --- diff ---")
            for line in r["diff_sample"].splitlines():
                print(f"    {line}")
            print()

    print()
    print(f"Results: {matches} match, {mismatches} differ out of {len(endpoints)}")

    if args.report:
        with open(args.report, "w") as f:
            json.dump({
                "old": args.old,
                "new": args.new,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "results": results,
                "summary": {"match": matches, "differ": mismatches,
                            "total": len(endpoints)},
            }, f, indent=2, default=str)
        print(f"Report written: {args.report}")

    return 0 if mismatches == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
