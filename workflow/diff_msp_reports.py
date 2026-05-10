"""Diff the QWeb arch in the create_msp_*.py upserter scripts vs the
as-deployed state on a target Odoo instance.

Catches the silent-divergence case: someone hand-edits a view via Odoo
Studio / dev mode, and forgets to mirror the change back into the
upserter script's QWEB_ARCH constant. The script's arch becomes stale.
If the target gets re-deployed (or someone runs the upserter against prod),
the live edit is overwritten.

Outputs unified-diff for any view where script's QWEB_ARCH != target's
arch_db. Exit code 0 if no drift, 1 if drift detected.

Usage:
    python workflow/diff_msp_reports.py --target staging
    python workflow/diff_msp_reports.py --target prod
"""
from __future__ import annotations
import argparse
import difflib
import html
import importlib.util
import os
import re
import ssl
import sys
import xmlrpc.client
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = REPO_ROOT / "workflow"

# (script_filename, view_key) for each MSP report.
# script must define a top-level QWEB_ARCH constant.
SCRIPTS = [
    ("create_msp_sale_report.py",      "msp.report_saleorder_msp_v1"),
    ("create_msp_invoice.py",          "msp.report_invoice_msp_v1"),
    ("create_msp_pick_sheet.py",       "msp.report_pick_sheet_v1"),
    ("create_msp_delivery_slip.py",    "msp.report_delivery_slip_v1"),
    ("create_msp_pallet_sheet.py",     "msp.report_pallet_sheet_v1"),
]


def _load_dotenv():
    p = REPO_ROOT / ".env"
    if not p.exists(): return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def _normalize_qweb(arch: str) -> str:
    """Parse + re-serialize both sides through lxml so we strip benign
    Odoo XML serializer noise (entity encoding, attr quote style,
    self-close vs explicit-close, whitespace) and only flag real
    semantic drift. Falls back to a string-level cleanup if parse fails."""
    if not arch:
        return ""
    try:
        from lxml import etree
        # QWeb root is <t t-call="..."> — wrap to give a single root just in case.
        root = etree.fromstring(f"<root>{arch}</root>".encode("utf-8"))
        # Canonical: re-serialize with consistent quoting + encoding.
        canon = etree.tostring(root, pretty_print=False, encoding="unicode")
        # Strip the wrapper tags we added.
        if canon.startswith("<root>"): canon = canon[len("<root>"):]
        if canon.endswith("</root>"): canon = canon[:-len("</root>")]
        return canon.strip()
    except Exception:
        # Fall back: best-effort string normalization (entity decode + tag collapse + ws strip)
        s = arch.replace("\r\n", "\n").strip()
        s = re.sub(r"<([a-zA-Z][\w:-]*)([^<>]*?)>\s*</\1>", r"<\1\2/>", s)
        s = html.unescape(s)
        return "\n".join(line.rstrip() for line in s.splitlines())


def _load_arch_from_script(script_path: Path) -> str | None:
    """Import the script as a module and return its QWEB_ARCH constant."""
    spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        # Some scripts call sys.exit on import if --target is missing;
        # we just want the constant. Catch and continue.
        pass
    except Exception as e:
        print(f"  ! failed to import {script_path.name}: {e}")
        return None
    return getattr(mod, "QWEB_ARCH", None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["staging", "prod"], required=True)
    ap.add_argument("--summary-only", action="store_true",
                    help="only print drift status, no full diff body")
    args = ap.parse_args()
    _load_dotenv()

    prefix = "ODOO_PROD" if args.target == "prod" else "ODOO_STAGING"
    url = os.environ.get(f"{prefix}_URL")
    db = os.environ.get(f"{prefix}_DB")
    user = os.environ.get(f"{prefix}_USER", "admin@mountainstatesplastics.com")
    key = os.environ.get(f"{prefix}_API_KEY")
    if not all([url, db, key]):
        sys.exit(f"Missing {prefix}_URL/{prefix}_DB/{prefix}_API_KEY in .env")

    ctx = ssl.create_default_context()
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", context=ctx, allow_none=True)
    uid = common.authenticate(db, user, key, {})
    if not uid: sys.exit(f"auth failed against {args.target}")
    m = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", context=ctx, allow_none=True)
    def call(model, method, args_, kw=None):
        return m.execute_kw(db, uid, key, model, method, args_, kw or {})

    drift_count = 0
    print(f"diffing {len(SCRIPTS)} MSP reports: scripts vs {args.target}")
    for script_name, view_key in SCRIPTS:
        script_path = WORKFLOW_DIR / script_name
        if not script_path.exists():
            print(f"  - {view_key:<40} script {script_name} NOT FOUND")
            continue
        script_arch = _load_arch_from_script(script_path)
        if script_arch is None:
            print(f"  - {view_key:<40} script {script_name}: no QWEB_ARCH constant")
            continue
        view_ids = call("ir.ui.view", "search", [[("key", "=", view_key)]], {"limit": 1})
        if not view_ids:
            print(f"  - {view_key:<40} not on {args.target}")
            continue
        live_arch = call("ir.ui.view", "read", [view_ids], {"fields": ["arch_db"]})[0]["arch_db"] or ""

        # Normalize both sides through the same canonicalizer so we only
        # see real semantic drift (not Odoo's XML serializer noise).
        s = _normalize_qweb(script_arch)
        l = _normalize_qweb(live_arch)
        if s == l:
            print(f"  OK      {view_key:<40} (arch matches)")
            continue
        drift_count += 1
        print(f"  DRIFT   {view_key:<40} script {len(s)} chars, live {len(l)} chars")
        if not args.summary_only:
            diff = difflib.unified_diff(
                s.splitlines(keepends=False),
                l.splitlines(keepends=False),
                fromfile=f"script:{script_name}",
                tofile=f"{args.target}:{view_key}",
                lineterm="",
                n=3,
            )
            for line in diff:
                print(f"    {line}")
            print()

    print(f"\nresult: {drift_count} drift(s) detected" if drift_count else "\nresult: no drift")
    sys.exit(1 if drift_count else 0)


if __name__ == "__main__":
    main()
