"""Build the MSP Open Sales Orders dashboard programmatically.

Creates / updates a `spreadsheet.dashboard` record with a fully populated
spreadsheet JSON: three list sections (open sales orders sorted by due
date, open order lines with qty ordered/delivered, manufacturing orders
with qty produced linked to those order lines).

No custom views, no UI clicks needed — entirely DB-driven via XML-RPC.

Idempotent — looks for dashboard by name + group, updates if found,
creates if not. Safe to re-run.

Usage:
    python create_msp_dashboard.py --target staging         # dry-run
    python create_msp_dashboard.py --target staging --commit
    python create_msp_dashboard.py --target prod --commit
"""
import argparse
import json
import os
import ssl
import sys
import xmlrpc.client
from pathlib import Path

DASHBOARD_NAME = "Open Sales Orders"
DASHBOARD_GROUP = "Open orders"   # existing group on staging+prod (id 10 on staging)

# Number of rows to render for each list. Beyond N rows the cells are empty.
SO_ROWS = 50
LINE_ROWS = 150
MO_ROWS = 100


# --------------------------------------------------------------------------
# Spreadsheet JSON builder
# --------------------------------------------------------------------------

def _list_block(list_id, columns, start_row, n_rows):
    """Build cell formulas for a list section starting at start_row.
    Returns dict of cell-ref -> formula string."""
    cells = {}
    # 0-indexed col letters
    for col_idx, fname in enumerate(columns):
        col_letter = chr(ord('A') + col_idx)
        # Header row
        cells[f"{col_letter}{start_row}"] = f'=ODOO.LIST.HEADER({list_id}, "{fname}")'
        # Data rows
        for r in range(1, n_rows + 1):
            cells[f"{col_letter}{start_row + r}"] = f'=ODOO.LIST({list_id},{r},"{fname}")'
    return cells


def build_spreadsheet_data():
    # ----- Lists -----
    so_cols = ["name", "partner_id", "commitment_date", "msp_drop_po",
               "client_order_ref", "amount_total", "user_id"]
    line_cols = ["order_id", "product_id", "name",
                 "product_uom_qty", "qty_delivered",
                 "x_studio_freight_terms"]
    mo_cols = ["name", "sale_line_id", "product_id", "state",
               "product_qty", "qty_produced", "date_finished"]

    lists = {
        "1": {
            "id": "1",
            "name": "Open Sales Orders by Due Date",
            "model": "sale.order",
            "domain": [["state", "=", "sale"]],
            "context": {},
            "orderBy": [{"name": "commitment_date", "asc": True}],
            "columns": so_cols,
            "fieldMatching": {},
        },
        "2": {
            "id": "2",
            "name": "Open Order Lines",
            "model": "sale.order.line",
            "domain": [["order_id.state", "=", "sale"],
                       ["display_type", "=", False]],
            "context": {},
            "orderBy": [{"name": "order_id", "asc": True}, {"name": "id", "asc": True}],
            "columns": line_cols,
            "fieldMatching": {},
        },
        "3": {
            "id": "3",
            "name": "Manufacturing Orders Linked to Open Sales",
            "model": "mrp.production",
            "domain": [["state", "not in", ["cancel", "draft"]],
                       ["sale_line_id", "!=", False],
                       ["sale_line_id.order_id.state", "=", "sale"]],
            "context": {},
            "orderBy": [{"name": "sale_line_id", "asc": True}, {"name": "id", "asc": True}],
            "columns": mo_cols,
            "fieldMatching": {},
        },
    }

    # ----- Sheet cells -----
    cells = {}

    # Title block
    cells["A1"] = "MSP — Open Sales Orders Dashboard"
    cells["A2"] = "Sorted by Expected Delivery Date. Updates live from the database."

    # Section 1: open sales orders
    section1_row = 4
    cells[f"A{section1_row}"] = "1) Open Sales Orders"
    cells.update(_list_block(1, so_cols, section1_row + 1, SO_ROWS))

    # Section 2: order lines with qty info
    section2_row = section1_row + SO_ROWS + 4
    cells[f"A{section2_row}"] = "2) Order Lines — Qty Ordered vs Delivered"
    cells.update(_list_block(2, line_cols, section2_row + 1, LINE_ROWS))

    # Section 3: MOs with manufactured qty
    section3_row = section2_row + LINE_ROWS + 4
    cells[f"A{section3_row}"] = "3) Manufacturing Orders — Qty Produced (drilled into the linked sales order line)"
    cells.update(_list_block(3, mo_cols, section3_row + 1, MO_ROWS))

    total_rows = section3_row + MO_ROWS + 5

    sheet = {
        "id": "sheet1",
        "name": "Open Orders",
        "colNumber": 12,
        "rowNumber": max(total_rows, 100),
        "cells": cells,
        "rows": {},
        "cols": {},
        "merges": [],
        "styles": {},
        "formats": {},
        "borders": {},
        "conditionalFormats": [],
        "dataValidationRules": [],
        "figures": [],
        "tables": [],
        "areGridLinesVisible": True,
        "isVisible": True,
        "headerGroups": {"ROW": [], "COL": []},
        "comments": {},
    }

    return {
        "version": 1,
        "sheets": [sheet],
        "styles": {},
        "formats": {},
        "borders": {},
        "revisionId": "START_REVISION",
        "uniqueFigureIds": 1,
        "settings": {
            "locale": {
                "name": "English (US)", "code": "en_US",
                "thousandsSeparator": ",", "decimalSeparator": ".",
                "dateFormat": "mm/dd/yyyy", "timeFormat": "hh:mm:ss",
                "formulaArgSeparator": ",", "weekStart": 7,
            },
        },
        "pivots": {},
        "pivotNextId": 1,
        "customTableStyles": {},
        "globalFilters": [],
        "lists": lists,
        "listNextId": 4,
        "chartOdooMenusReferences": {},
    }


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------

def _load_dotenv():
    p = Path(__file__).parent.parent / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


def connect(target):
    prefix = f"ODOO_{target.upper()}_"
    url = os.environ.get(prefix + "URL")
    db = os.environ.get(prefix + "DB")
    user = os.environ.get(prefix + "USER")
    api_key = os.environ.get(prefix + "API_KEY")
    if not all([url, db, user, api_key]):
        sys.exit(f"Missing {prefix}* env vars")
    ctx = ssl.create_default_context()
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", context=ctx, allow_none=True)
    uid = common.authenticate(db, user, api_key, {})
    if not uid:
        sys.exit(f"auth failed for {target}")
    obj = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", context=ctx, allow_none=True)

    def call(model, method, args, kwargs=None):
        return obj.execute_kw(db, uid, api_key, model, method, args, kwargs or {})
    return url, call


def upsert_dashboard(call, commit):
    # Find or create the group
    groups = call("spreadsheet.dashboard.group", "search_read",
                  [[("name", "=", DASHBOARD_GROUP)]],
                  {"fields": ["id", "name"]})
    if groups:
        group_id = groups[0]["id"]
        print(f"  group: id={group_id} name={DASHBOARD_GROUP!r}")
    else:
        if not commit:
            print(f"  group: would CREATE {DASHBOARD_GROUP!r}")
            group_id = None
        else:
            group_id = call("spreadsheet.dashboard.group", "create",
                            [{"name": DASHBOARD_GROUP, "sequence": 50}])
            print(f"  group: CREATED id={group_id} name={DASHBOARD_GROUP!r}")

    # Build the spreadsheet JSON
    data = build_spreadsheet_data()
    data_str = json.dumps(data)
    print(f"  spreadsheet_data: {len(data_str):,} chars")

    if group_id is None and not commit:
        print("  dashboard: would CREATE (group not yet created in dry-run)")
        return None

    # Find existing dashboard
    rows = call("spreadsheet.dashboard", "search_read",
                [[("name", "=", DASHBOARD_NAME),
                  ("dashboard_group_id", "=", group_id)]],
                {"fields": ["id", "name"]})
    vals = {
        "name": DASHBOARD_NAME,
        "dashboard_group_id": group_id,
        "spreadsheet_data": data_str,
        "is_published": True,
    }
    if rows:
        did = rows[0]["id"]
        if not commit:
            print(f"  dashboard id={did}: would UPDATE")
            return did
        call("spreadsheet.dashboard", "write", [[did], vals])
        print(f"  dashboard id={did}: UPDATED")
        return did
    if not commit:
        print(f"  dashboard {DASHBOARD_NAME!r}: would CREATE")
        return None
    did = call("spreadsheet.dashboard", "create", [vals])
    print(f"  dashboard id={did}: CREATED")
    return did


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["staging", "prod"], default="staging")
    parser.add_argument("--commit", action="store_true",
                        help="actually write (default: dry-run)")
    args = parser.parse_args()

    url, call = connect(args.target)
    print(f"Target: {args.target}  ({url})  mode: {'COMMIT' if args.commit else 'dry-run'}")
    print()

    upsert_dashboard(call, args.commit)

    print()
    if args.commit:
        print("Done. Find it in the UI:")
        print(f"  Apps menu (top-left grid icon) -> Dashboards -> '{DASHBOARD_GROUP}' -> '{DASHBOARD_NAME}'")
        print(f"  (Or the URL /odoo/dashboards once you click into the spreadsheet dashboard app.)")
    else:
        print("DRY-RUN. Re-run with --commit to apply.")


if __name__ == "__main__":
    main()
