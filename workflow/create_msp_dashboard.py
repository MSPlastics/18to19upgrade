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
    for col_idx, fname in enumerate(columns):
        col_letter = chr(ord('A') + col_idx)
        cells[f"{col_letter}{start_row}"] = f'=ODOO.LIST.HEADER({list_id}, "{fname}")'
        for r in range(1, n_rows + 1):
            cells[f"{col_letter}{start_row + r}"] = f'=ODOO.LIST({list_id},{r},"{fname}")'
    return cells


def _style_block(start_row, start_col, n_rows, n_cols, style_id):
    """Apply style_id to a rectangular block of cells. Returns dict of cellref -> styleId."""
    out = {}
    for r in range(start_row, start_row + n_rows):
        for c in range(start_col, start_col + n_cols):
            col_letter = chr(ord('A') + c)
            out[f"{col_letter}{r}"] = style_id
    return out


def build_spreadsheet_data():
    # ----- Lists -----
    so_cols = ["name", "partner_id", "commitment_date", "msp_drop_po",
               "client_order_ref", "amount_total", "user_id"]
    line_cols = ["order_id", "product_id", "name",
                 "product_uom_qty", "qty_delivered",
                 "x_studio_freight_terms"]
    # Note: MSP populates sale_order_line_id / sale_order_id (likely a custom
    # module override) — not the standard sale_line_id. ~73% of MOs are
    # linked via these fields.
    # Visual order: id/links/state on the left, then a contiguous block of
    # quantity columns (To Produce | Produced | Delivered | Balance) at the
    # right so the math flows naturally. Balance is a computed spreadsheet
    # formula appended after the list columns.
    mo_cols = ["name", "sale_order_id", "sale_order_line_id", "product_id",
               "state", "date_finished", "product_qty", "qty_produced",
               "sale_order_line_id.qty_delivered"]

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
            "name": "Produced — Linked Sales Order Still Open",
            "model": "mrp.production",
            # Production is in progress or complete (state in progress/
            # to_close/done) AND the linked sales order is still open
            # (sale_order_id.state = 'sale'). When the SO state flips to
            # 'done' the order is fully shipped, so the MO drops off the
            # dashboard. Approximates "produced material not yet shipped".
            "domain": [
                ["state", "in", ["progress", "to_close", "done"]],
                ["sale_order_line_id", "!=", False],
                ["sale_order_id.state", "=", "sale"],
            ],
            "context": {},
            "orderBy": [{"name": "sale_order_id", "asc": True},
                        {"name": "date_finished", "asc": False}],
            "columns": mo_cols,
            "fieldMatching": {},
        },
    }

    # ----- Top-level styles (referenced by integer ids in sheet.styles map) -----
    styles = {
        "1": {  # Title
            "bold": True, "fontSize": 22, "textColor": "#0A182F",
            "verticalAlign": "middle",
        },
        "2": {  # Subtitle
            "fontSize": 10, "textColor": "#7B8794", "italic": True,
            "verticalAlign": "middle",
        },
        "3": {  # Section banner — navy bg, white text
            "bold": True, "fontSize": 13, "textColor": "#FFFFFF",
            "fillColor": "#0A182F", "verticalAlign": "middle",
        },
        "4": {  # Column header — light panel bg, navy text
            "bold": True, "fontSize": 10, "textColor": "#0A182F",
            "fillColor": "#F4F6F9", "verticalAlign": "middle",
        },
        "5": {  # Data cell (default)
            "fontSize": 10, "textColor": "#2C3E50", "verticalAlign": "middle",
        },
    }

    # ----- Sheet cells -----
    cells = {}
    cell_styles = {}    # cellref -> styleId
    rows_sizes = {}     # rowIndex (0-based, str) -> {"size": int}
    tables = []

    # --- Title block ---
    cells["A1"] = "MSP — Open Sales Orders Dashboard"
    cell_styles["A1"] = 1
    rows_sizes["0"] = {"size": 38}

    cells["A2"] = "Sorted by Expected Delivery Date. Live data — refreshes when you reopen."
    cell_styles["A2"] = 2
    rows_sizes["1"] = {"size": 18}

    # --- Section 1: open sales orders ---
    section1_row = 4
    cells[f"A{section1_row}"] = "  1.  OPEN SALES ORDERS"
    # Banner across all 7 columns of section
    cell_styles.update(_style_block(section1_row, 0, 1, len(so_cols), 3))
    rows_sizes[str(section1_row - 1)] = {"size": 30}

    # List header + data
    list1_start = section1_row + 1
    cells.update(_list_block(1, so_cols, list1_start, SO_ROWS))
    cell_styles.update(_style_block(list1_start, 0, 1, len(so_cols), 4))   # column headers
    cell_styles.update(_style_block(list1_start + 1, 0, SO_ROWS, len(so_cols), 5))  # data
    rows_sizes[str(list1_start - 1)] = {"size": 26}
    # Banded table over header + data
    tables.append({
        "range": f"A{list1_start}:{chr(ord('A') + len(so_cols) - 1)}{list1_start + SO_ROWS}",
        "type": "static",
        "config": {
            "hasFilters": False, "totalRow": False, "firstColumn": False,
            "lastColumn": False, "numberOfHeaders": 1, "bandedRows": True,
            "bandedColumns": False, "automaticAutofill": True, "styleId": "None",
        },
    })

    # --- Section 2: order lines ---
    section2_row = list1_start + SO_ROWS + 3
    cells[f"A{section2_row}"] = "  2.  ORDER LINES — QTY ORDERED vs DELIVERED"
    cell_styles.update(_style_block(section2_row, 0, 1, len(line_cols), 3))
    rows_sizes[str(section2_row - 1)] = {"size": 30}

    list2_start = section2_row + 1
    cells.update(_list_block(2, line_cols, list2_start, LINE_ROWS))
    cell_styles.update(_style_block(list2_start, 0, 1, len(line_cols), 4))
    cell_styles.update(_style_block(list2_start + 1, 0, LINE_ROWS, len(line_cols), 5))
    rows_sizes[str(list2_start - 1)] = {"size": 26}
    tables.append({
        "range": f"A{list2_start}:{chr(ord('A') + len(line_cols) - 1)}{list2_start + LINE_ROWS}",
        "type": "static",
        "config": {
            "hasFilters": False, "totalRow": False, "firstColumn": False,
            "lastColumn": False, "numberOfHeaders": 1, "bandedRows": True,
            "bandedColumns": False, "automaticAutofill": True, "styleId": "None",
        },
    })

    # --- Section 3: MOs ---
    section3_row = list2_start + LINE_ROWS + 3
    cells[f"A{section3_row}"] = "  3.  MANUFACTURED — LINKED SALES ORDER STILL OPEN (i.e. not yet fully shipped)"
    cell_styles.update(_style_block(section3_row, 0, 1, len(mo_cols), 3))
    rows_sizes[str(section3_row - 1)] = {"size": 30}

    list3_start = section3_row + 1
    cells.update(_list_block(3, mo_cols, list3_start, MO_ROWS))
    cell_styles.update(_style_block(list3_start, 0, 1, len(mo_cols), 4))
    cell_styles.update(_style_block(list3_start + 1, 0, MO_ROWS, len(mo_cols), 5))
    rows_sizes[str(list3_start - 1)] = {"size": 26}

    # Append a Balance column after the list. mo_cols has 9 entries (cols
    # A..I), so Balance lives in column J. The header cell + per-row formula
    # = qty_produced - qty_delivered. Cells are blanked when the row is empty
    # so we don't render a fake "0" balance for rows beyond the data.
    qty_produced_col = chr(ord('A') + mo_cols.index("qty_produced"))   # H
    qty_delivered_col = chr(ord('A') + mo_cols.index("sale_order_line_id.qty_delivered"))  # I
    balance_col = chr(ord('A') + len(mo_cols))   # J
    cells[f"{balance_col}{list3_start}"] = "Balance (Produced − Delivered)"
    cell_styles[f"{balance_col}{list3_start}"] = 4
    for r in range(1, MO_ROWS + 1):
        row_n = list3_start + r
        cells[f"{balance_col}{row_n}"] = (
            f'=IF({qty_produced_col}{row_n}="","",'
            f'{qty_produced_col}{row_n}-{qty_delivered_col}{row_n})'
        )
        cell_styles[f"{balance_col}{row_n}"] = 5

    table_last_col = chr(ord('A') + len(mo_cols))   # include balance col in the band
    tables.append({
        "range": f"A{list3_start}:{table_last_col}{list3_start + MO_ROWS}",
        "type": "static",
        "config": {
            "hasFilters": False, "totalRow": False, "firstColumn": False,
            "lastColumn": False, "numberOfHeaders": 1, "bandedRows": True,
            "bandedColumns": False, "automaticAutofill": True, "styleId": "None",
        },
    })

    total_rows = list3_start + MO_ROWS + 5

    # ----- Column widths (cols dict is 0-indexed by column number, as string) -----
    # Pick the widest layout among the three sections so all sections look right.
    # Sections: SO has 7 cols, lines has 6, MOs has 7.
    cols_sizes = {
        "0": {"size": 130},   # Order # / Order / MO #
        "1": {"size": 220},   # Customer / Product / Sale Order
        "2": {"size": 140},   # Expected Delivery / Description / Sale Line
        "3": {"size": 110},   # Drop PO / Qty Ordered / Product
        "4": {"size": 130},   # Customer PO / Qty Delivered / State
        "5": {"size": 130},   # Total / Freight / Date Finished
        "6": {"size": 130},   # Salesperson / — / Qty to Produce
        "7": {"size": 130},   # — / — / Qty Produced
        "8": {"size": 130},   # — / — / Qty Delivered
        "9": {"size": 150},   # — / — / Balance
    }

    sheet = {
        "id": "sheet1",
        "name": "Open Orders",
        # +1 to the MO width because we add a computed Balance column after
        # the list columns.
        "colNumber": max(len(so_cols), len(line_cols), len(mo_cols) + 1),
        "rowNumber": max(total_rows, 100),
        "cells": cells,
        "rows": rows_sizes,
        "cols": cols_sizes,
        "merges": [],
        "styles": cell_styles,
        "formats": {},
        "borders": {},
        "conditionalFormats": [],
        "dataValidationRules": [],
        "figures": [],
        "tables": tables,
        "areGridLinesVisible": False,   # cleaner without the default grid
        "isVisible": True,
        "headerGroups": {"ROW": [], "COL": []},
        "comments": {},
    }

    return {
        # The "version" key is the o-spreadsheet engine version, not Odoo's
        # version. Odoo 19 ships with engine 18.5.10. Storing a different
        # version (e.g. 1) makes the engine refuse to render cells.
        "version": "18.5.10",
        "sheets": [sheet],
        "styles": styles,
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
