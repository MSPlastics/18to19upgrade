"""Set up 25 test pallets on WH/OUT/01338 for the grouped pick sheet visual.

Bypasses the full MES->reconcile sync (which is bottlenecked by free WH/Stock
quants) and manipulates Odoo directly:

  1. Reuse existing packages 5+6 (already named WH/MO/01459-PAL-1 and -PAL-2)
  2. Create 23 more packages WH/MO/01459-PAL-3 .. -PAL-25 with realistic
     msp_* metadata (small variation in weight + dims)
  3. Bump WH/OUT/01338's stock.move demand from 3 to 25
  4. Wipe existing move_lines and recreate 25 — one per package — each
     reserving 1 case from the FG lot

The point is to let the user see the new grouped layout with a real
order-volume's worth of pallets.
"""
import os, ssl, sys, xmlrpc.client
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
m = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", context=ctx, allow_none=True)

def call(model, method, args, kw=None):
    return m.execute_kw(DB, uid, KEY, model, method, args, kw or {})

def call_void(model, method, args, kw=None):
    """Swallow 'cannot marshal None' faults from void Odoo methods."""
    try: return call(model, method, args, kw)
    except xmlrpc.client.Fault as e:
        if 'cannot marshal None' in str(e): return None
        raise

PICK_NAME = "WH/OUT/01338"
LOT_NAME  = "MO/01459-001"
WO_PREFIX = "WH/MO/01459-PAL-"
PALLET_TYPE_NAME = "MSP Pallet"
TARGET_PALLETS = 25

# Realistic-ish dimension and weight variation (some pallets are smaller/lighter)
def gen_pallet_meta(n):
    # 80% are 40x48x52, 20% are 40x48x46 (shorter)
    if n % 5 == 0:
        dims = (40, 48, 46); cases = 18; wt = 720.0 + (n * 1.7)
    else:
        dims = (40, 48, 52); cases = 24; wt = 980.0 + (n * 2.3)
    return dims, cases, round(wt, 1)


print(f"=== probe ===")
pkg_type = call("stock.package.type", "search",
                [[("name", "=", PALLET_TYPE_NAME)]], {"limit": 1})[0]
lot_id = call("stock.lot", "search", [[("name", "=", LOT_NAME)]], {"limit": 1})[0]
print(f"  pkg_type id={pkg_type}  lot id={lot_id}")

picks = call("stock.picking", "search_read",
             [[("name", "=", PICK_NAME)]],
             {"fields": ["id", "move_ids", "move_line_ids"]})
pick = picks[0]
pick_id = pick["id"]
move = call("stock.move", "read", [pick["move_ids"]],
            {"fields": ["id", "product_id", "product_uom", "location_id",
                        "location_dest_id", "product_uom_qty"]})[0]
print(f"  pick {pick_id} move {move['id']} demand={move['product_uom_qty']}")


print(f"\n=== ensure {TARGET_PALLETS} packages WH/MO/01459-PAL-1..{TARGET_PALLETS} ===")
package_ids = []
for n in range(1, TARGET_PALLETS + 1):
    name = f"{WO_PREFIX}{n}"
    existing = call("stock.package", "search", [[("name", "=", name)]], {"limit": 1})
    dims, cases, wt = gen_pallet_meta(n)
    vals = {
        "msp_length_in":    float(dims[0]),
        "msp_width_in":     float(dims[1]),
        "msp_height_in":    float(dims[2]),
        "msp_gross_weight_lb": wt,
        "msp_unit_numbers_summary": f"{(n-1)*cases+1}-{n*cases}",
        "msp_finalized_at": "2026-05-10 12:00:00",
    }
    if existing:
        pid = existing[0]
        call("stock.package", "write", [[pid], vals])
        action = "updated"
    else:
        create_vals = dict(vals, name=name, package_type_id=pkg_type)
        pid = call("stock.package", "create", [create_vals])
        action = "created"
    package_ids.append((pid, n, cases))
    print(f"  pkg id={pid:>3}  {name:<22}  {action}  dims={dims}  cases={cases}  wt={wt}")


print(f"\n=== bump move demand to {TARGET_PALLETS} cases ===")
total_cases = sum(c for _, _, c in package_ids)
print(f"  total_cases across {TARGET_PALLETS} pallets = {total_cases}")
# Update move demand
call("stock.move", "write", [[move["id"]], {"product_uom_qty": float(total_cases)}])

print(f"\n=== wipe existing move_lines + create 1 per pallet ===")
# Wipe
existing_mls = call("stock.move.line", "search", [[("picking_id", "=", pick_id)]])
if existing_mls:
    call_void("stock.move.line", "unlink", [existing_mls])
    print(f"  unlinked {len(existing_mls)} existing move_lines")

# Create one move_line per package, qty = case count for that pallet
created = 0
for pid, n, cases in package_ids:
    call("stock.move.line", "create", [{
        "move_id": move["id"],
        "picking_id": pick_id,
        "product_id": move["product_id"][0],
        "product_uom_id": move["product_uom"][0],
        "lot_id": lot_id,
        "package_id": pid,
        "quantity": float(cases),
        "location_id": move["location_id"][0],
        "location_dest_id": move["location_dest_id"][0],
    }])
    created += 1
print(f"  created {created} move_lines")

# Verify
mls = call("stock.move.line", "search_read",
    [[("picking_id", "=", pick_id)]],
    {"fields": ["package_id", "quantity"], "order": "id ASC"})
print(f"\n=== final state ===")
print(f"  picking now has {len(mls)} move_lines")
print(f"  total qty across all = {sum(ml['quantity'] for ml in mls)}")
print(f"\nReady to print: WH/OUT/01338 -> Print -> Warehouse Pick Sheet — MSP")
