"""Create saved favorite filters (`ir.filters`) for the open-orders dashboard.

Idempotent — looks up by name+model+user_id, updates if found, creates if not.

Once these filters exist, the workflow to build the dashboard is:
  1. Sales → Orders. Open the Filters dropdown → Favorites → pick the filter.
  2. With the filtered list visible, click the cog (⚙) → "Insert list in
     Spreadsheet". Pick "New Spreadsheet" or "Pin in Dashboard".
  3. Repeat for sale.order.line and mrp.production filters; the spreadsheet
     editor lets you combine pivots/lists from each into one dashboard tab.

Usage:
    python create_dashboard_filters.py --target staging         # dry-run
    python create_dashboard_filters.py --target staging --commit
    python create_dashboard_filters.py --target prod --commit
"""
import argparse
import os
import ssl
import sys
import xmlrpc.client
from pathlib import Path


FILTERS = [
    # Order-level: open orders sorted by due date (commitment_date asc).
    {
        "name": "Open Orders — by Due Date",
        "model_id": "sale.order",
        "domain": "[('state', '=', 'sale')]",
        "sort": '["commitment_date", "id"]',
        "context": "{}",
        "is_default": False,
    },
    # Line-level: open order lines, grouped by order, sorted by order due date.
    # The cog menu's "Insert list in Spreadsheet" preserves grouping.
    {
        "name": "Open Order Lines — by Due Date",
        "model_id": "sale.order.line",
        "domain": "[('order_id.state', '=', 'sale'), ('display_type', '=', False)]",
        "sort": '["order_id", "id"]',
        "context": "{'group_by': ['order_id']}",
        "is_default": False,
    },
    # MO-level: pivot source for manufactured qty. Group by origin sale line.
    {
        "name": "MOs by Origin Sales Order Line",
        "model_id": "mrp.production",
        "domain": "[('state', 'not in', ('cancel', 'draft')), ('sale_line_id', '!=', False)]",
        "sort": '["sale_line_id", "id"]',
        "context": "{'group_by': ['sale_line_id']}",
        "is_default": False,
    },
]


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


def upsert_filter(call, spec, commit):
    # `ir.filters.model_id` is a selection of model technical names, not a
    # Many2one to ir.model — pass the string directly.
    vals = {
        "name": spec["name"],
        "model_id": spec["model_id"],
        "domain": spec["domain"],
        "sort": spec["sort"],
        "context": spec["context"],
        "is_default": spec["is_default"],
        "user_ids": [(6, 0, [])],   # shared with all users (v19 renamed user_id -> user_ids Many2many)
        "active": True,
    }

    existing = call("ir.filters", "search_read",
                    [[("name", "=", spec["name"]), ("model_id", "=", spec["model_id"])]],
                    {"fields": ["id"]})
    if existing:
        fid = existing[0]["id"]
        if not commit:
            print(f"  filter {fid:>4} {spec['name']!r} on {spec['model_id']}: would UPDATE")
            return fid
        call("ir.filters", "write", [[fid], vals])
        print(f"  filter {fid:>4} {spec['name']!r} on {spec['model_id']}: UPDATED")
        return fid
    if not commit:
        print(f"  filter {spec['name']!r} on {spec['model_id']}: would CREATE")
        return None
    fid = call("ir.filters", "create", [vals])
    print(f"  filter {fid:>4} {spec['name']!r} on {spec['model_id']}: CREATED")
    return fid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["staging", "prod"], default="staging")
    parser.add_argument("--commit", action="store_true",
                        help="actually write (default: dry-run)")
    args = parser.parse_args()

    url, call = connect(args.target)
    print(f"Target: {args.target}  ({url})  mode: {'COMMIT' if args.commit else 'dry-run'}")
    print()

    for spec in FILTERS:
        upsert_filter(call, spec, args.commit)

    print()
    if args.commit:
        print("Done. Build the dashboard from the UI:")
        print("  1. Sales > Orders.  Filters > Favorites > 'Open Orders -- by Due Date'")
        print("  2. Cog menu > 'Insert list in Spreadsheet' > New Spreadsheet")
        print("  3. In the spreadsheet, Insert > Pivot or List from the other models")
        print("     (sale.order.line, mrp.production) using the matching favorite filters")
        print("  4. Save the spreadsheet > 'Pin in Dashboard' to make it sticky")
    else:
        print("DRY-RUN. Re-run with --commit to apply.")


if __name__ == "__main__":
    main()
