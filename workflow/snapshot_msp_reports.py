"""Snapshot all MSP custom reports + their actions from a target Odoo as JSON.

Why: the create_msp_*.py upserters embed QWEB_ARCH as a Python string
constant — they are the source of truth. BUT if someone hand-edits a view
via Odoo Studio / dev mode on staging and forgets to mirror the change
back into the upserter script, the staging DB and source diverge silently.
If Odoo.sh then deletes the staging branch (it treats them as throw-away),
that edit is lost forever.

This script grabs the *as-deployed* state from a target instance and
writes it to git as a backup + diff baseline. Run periodically against
staging (and optionally prod after each deploy) to keep the snapshots
fresh.

Output: workflow/snapshots/qweb_reports/<target>/<view_key>.json
        workflow/snapshots/qweb_reports/<target>/_actions.json

Usage:
    python workflow/snapshot_msp_reports.py --target staging
    python workflow/snapshot_msp_reports.py --target prod
"""
from __future__ import annotations
import argparse
import json
import os
import ssl
import sys
import xmlrpc.client
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# All MSP-custom QWeb reports we manage via create_msp_*.py upserters.
# (key, report_name) pairs the snapshot script knows about.
REPORTS = [
    ("msp.report_saleorder_msp_v1",      "msp.report_saleorder_msp_v1"),
    ("msp.report_invoice_msp_v1",        "msp.report_invoice_msp_v1"),
    ("msp.report_pick_sheet_v1",         "msp.report_pick_sheet_v1"),
    ("msp.report_delivery_slip_v1",      "msp.report_delivery_slip_v1"),
    ("msp.report_pallet_sheet_v1",       "msp.report_pallet_sheet_v1"),
]


def _load_dotenv():
    p = REPO_ROOT / ".env"
    if not p.exists(): return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["staging", "prod"], required=True)
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

    out_dir = REPO_ROOT / "workflow" / "snapshots" / "qweb_reports" / args.target
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"snapshotting {len(REPORTS)} reports from {args.target} -> {out_dir.relative_to(REPO_ROOT)}")
    actions_dump = {}
    found = 0
    for view_key, report_name in REPORTS:
        # View
        view_ids = call("ir.ui.view", "search", [[("key", "=", view_key)]], {"limit": 1})
        if not view_ids:
            print(f"  - {view_key:<40} NOT FOUND")
            continue
        view = call("ir.ui.view", "read", [view_ids],
            {"fields": ["id", "key", "name", "type", "model", "arch_db", "active"]})[0]
        # Action
        action_ids = call("ir.actions.report", "search",
            [[("report_name", "=", report_name)]], {"limit": 1})
        action = None
        if action_ids:
            action = call("ir.actions.report", "read", [action_ids],
                {"fields": ["id", "name", "model", "report_name", "report_type",
                            "binding_model_id", "binding_type", "paperformat_id",
                            "print_report_name", "attachment", "attachment_use"]})[0]

        # Strip volatile / instance-local fields before dumping
        view_dump = {
            "key": view["key"],
            "name": view["name"],
            "type": view["type"],
            "model": view["model"],
            "active": view["active"],
            "arch_db": view["arch_db"],
        }
        out_file = out_dir / f"{view_key}.json"
        out_file.write_text(json.dumps(view_dump, indent=2, ensure_ascii=False, sort_keys=True),
                            encoding="utf-8")
        actions_dump[report_name] = {
            "name": action["name"] if action else None,
            "model": action["model"] if action else None,
            "report_name": action["report_name"] if action else None,
            "report_type": action["report_type"] if action else None,
            "binding_model_id": action["binding_model_id"][1] if action and action.get("binding_model_id") else None,
            "binding_type": action["binding_type"] if action else None,
            "paperformat_id": action["paperformat_id"][1] if action and action.get("paperformat_id") else None,
            "print_report_name": action["print_report_name"] if action else None,
            "attachment": action["attachment"] if action else None,
            "attachment_use": action["attachment_use"] if action else None,
        }
        size = len(view_dump["arch_db"] or "")
        print(f"  - {view_key:<40} arch {size:>6} chars + action {'OK' if action else 'MISSING'}")
        found += 1

    actions_file = out_dir / "_actions.json"
    actions_file.write_text(json.dumps(actions_dump, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nsnapshot complete: {found}/{len(REPORTS)} reports -> {out_dir.relative_to(REPO_ROOT)}")
    print(f"actions index    : {actions_file.relative_to(REPO_ROOT)}")
    print("\ncommit these to git so an Odoo.sh staging-branch deletion doesn't lose the as-deployed state.")


if __name__ == "__main__":
    main()
