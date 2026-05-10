"""Find MOs on staging with FG lot + an open delivery picking that has
   unassigned (result_package_id IS NULL) FG move_lines — these are
   sync_pallet_to_odoo's happy-path targets."""
import os, ssl, xmlrpc.client
from pathlib import Path

def _load_dotenv():
    p = Path(__file__).parent.parent / ".env"
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
_load_dotenv()

URL = os.environ["ODOO_STAGING_URL"]; DB = os.environ["ODOO_STAGING_DB"]
USER = os.environ.get("ODOO_STAGING_USER", "admin@mountainstatesplastics.com")
KEY = os.environ["ODOO_STAGING_API_KEY"]

ctx = ssl.create_default_context()
common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", context=ctx, allow_none=True)
uid = common.authenticate(DB, USER, KEY, {})
models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", context=ctx, allow_none=True)

def call(model, method, args, kw=None):
    return models.execute_kw(DB, uid, KEY, model, method, args, kw or {})

# 1. find any unassigned move_lines on open delivery pickings
mls = call("stock.move.line", "search_read",
           [[["result_package_id", "=", False],
             ["state", "in", ["partially_available", "assigned", "confirmed"]],
             ["picking_id.state", "!=", "done"],
             ["picking_id.picking_type_id.code", "=", "outgoing"],
             ["lot_id", "!=", False]]],
           {"fields": ["picking_id", "product_id", "lot_id", "quantity", "state", "move_id"],
            "limit": 50})

print(f"=== {len(mls)} unassigned FG move_lines on open delivery pickings ===")
by_lot = {}
for ml in mls:
    lot = ml["lot_id"][1] if ml["lot_id"] else "NONE"
    pick = ml["picking_id"][1] if ml["picking_id"] else "?"
    prod = ml["product_id"][1] if ml["product_id"] else "?"
    key = (lot, pick, prod)
    by_lot.setdefault(key, 0)
    by_lot[key] += 1

for (lot, pick, prod), cnt in sorted(by_lot.items(), key=lambda x: -x[1]):
    print(f"  {pick:<20} lot={lot:<35} cases={cnt}  product={prod[:50]}")

# 2. for the top result, look up which MO produces that lot
if by_lot:
    top_lot = list(by_lot.keys())[0][0]
    print(f"\n=== MO that produces lot '{top_lot}' ===")
    lot_recs = call("stock.lot", "search_read",
                    [[["name", "=", top_lot]]],
                    {"fields": ["name", "product_id"], "limit": 5})
    for lr in lot_recs:
        mo_recs = call("mrp.production", "search_read",
                       [[["lot_producing_ids", "in", [lr["id"]]]]],
                       {"fields": ["name", "state"], "limit": 5})
        for m in mo_recs:
            print(f"  MO: {m['name']} (state={m['state']}) — sync target")
