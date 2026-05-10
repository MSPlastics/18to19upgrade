"""Reshape WH/OUT/01338 to demo three band types in the pick sheet:

  - Group A: 12 pallets of product 1006 (lot MO/01459-001)        [pure]
  - Group B: 8 pallets of a SECOND product (its own MO + lot)     [pure]
  - Mixed: 2 pallets each carrying both products                   [mixed]

The Mixed band proves the dedicated breakdown rendering. The two pure
groups show the per-product header pattern.

Picks the second product/lot from the highest-stock MO in WH/Stock that
isn't MO/01459 — keeps it self-contained.
"""
import os, ssl, sys, xmlrpc.client
from pathlib import Path

def _load():
    p = Path(__file__).parent.parent / ".env"
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
_load()

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
    try: return call(model, method, args, kw)
    except xmlrpc.client.Fault as e:
        if 'cannot marshal None' in str(e): return None
        raise

PICK_NAME = "WH/OUT/01338"

# Product A — already set up
PROD_A = 1006              # 10969 [SUPPLYONE] 10x1000 ST SAMPLE
LOT_A_NAME = "MO/01459-001"
WO_A = "WH/MO/01459"

# Pick Product B from any MO with FG lot
print("=== probe a 2nd product (different MO/lot) ===")
mos = call("mrp.production", "search_read",
    [[["lot_producing_ids", "!=", False],
      ["product_id", "!=", PROD_A]]],
    {"fields": ["id", "name", "lot_producing_ids", "product_id", "product_uom_id"],
     "order": "id DESC", "limit": 1})
if not mos:
    sys.exit("no second MO with FG lot found on staging")
mo_b = mos[0]
PROD_B = mo_b["product_id"][0]
LOT_B_ID = mo_b["lot_producing_ids"][0]
LOT_B_NAME = call("stock.lot", "read", [[LOT_B_ID]], {"fields": ["name"]})[0]["name"]
WO_B = mo_b["name"]
prod_b_uom = mo_b["product_uom_id"][0]
print(f"  Product B = id={PROD_B} ({mo_b['product_id'][1]})")
print(f"  WO B = {WO_B}  Lot B = {LOT_B_NAME} (id {LOT_B_ID})")

LOT_A_ID = call("stock.lot", "search", [[("name", "=", LOT_A_NAME)]], {"limit": 1})[0]
prod_a_data = call("product.product", "read", [[PROD_A]], {"fields": ["uom_id"]})[0]
prod_a_uom = prod_a_data["uom_id"][0]

pkg_type = call("stock.package.type", "search",
                [[("name", "=", "MSP Pallet")]], {"limit": 1})[0]

# Picking + move
pick = call("stock.picking", "search_read",
            [[("name", "=", PICK_NAME)]],
            {"fields": ["id", "move_ids"]})[0]
pick_id = pick["id"]

# Existing move (for product A)
moves = call("stock.move", "read", pick["move_ids"],
             {"fields": ["id", "product_id", "product_uom", "location_id",
                         "location_dest_id", "product_uom_qty"]})
move_a = next((mv for mv in moves if mv["product_id"][0] == PROD_A), moves[0])
print(f"  existing move A id={move_a['id']}")

# Wipe existing move_lines to start clean
existing_mls = call("stock.move.line", "search", [[("picking_id", "=", pick_id)]])
if existing_mls:
    call_void("stock.move.line", "unlink", [existing_mls])
    print(f"  unlinked {len(existing_mls)} existing move_lines")

# ---- DEFINE THE 22 PALLETS ----
# 12 pure A pallets (PAL-1..12), 8 pure B pallets (PAL-13..20), 2 mixed (PAL-21..22)
# Pallet meta: ((lot_id_or_None_for_each_line)... , dims, weight)
# For mixed pallets, we'll create TWO move_lines on the same package.
def gen_meta(n):
    # 80% are 40x48x52, 20% are shorter
    if n % 5 == 0:
        return (40, 48, 46), 18, 730.0 + n * 2.1
    return (40, 48, 52), 24, 980.0 + n * 1.8

# Update demand on existing move A: total cases of product A across all pallets (pure + mixed)
PAL_A_PURE = list(range(1, 13))   # 1..12
PAL_B_PURE = list(range(13, 21))  # 13..20
PAL_MIXED = list(range(21, 23))   # 21..22

# Create new pallets we don't have yet (extends existing 1..25 from previous test)
for n in range(1, 23):
    name = f"WH/MO/01459-PAL-{n}" if n <= 20 else f"WH/MO/01459-PAL-{n}"
    # Reuse if exists
    existing = call("stock.package", "search", [[("name", "=", name)]], {"limit": 1})
    dims, _, wt = gen_meta(n)
    vals = {
        "msp_length_in":    float(dims[0]),
        "msp_width_in":     float(dims[1]),
        "msp_height_in":    float(dims[2]),
        "msp_gross_weight_lb": round(wt, 1),
        "msp_unit_numbers_summary": f"{n*100}-{n*100+24}",
        "msp_finalized_at": "2026-05-10 12:00:00",
    }
    if existing:
        call("stock.package", "write", [[existing[0]], vals])
    else:
        create_vals = dict(vals, name=name, package_type_id=pkg_type)
        call("stock.package", "create", [create_vals])

# Compute total demand for A and B
total_a_cases = sum(gen_meta(n)[1] for n in PAL_A_PURE) + sum(12 for _ in PAL_MIXED)  # mixed pallets carry 12 of A each
total_b_cases = sum(gen_meta(n)[1] for n in PAL_B_PURE) + sum(10 for _ in PAL_MIXED)  # mixed pallets carry 10 of B each
print(f"\n  total demand: Product A = {total_a_cases} cases, Product B = {total_b_cases} cases")

# Update move A's demand
call("stock.move", "write", [[move_a["id"]], {"product_uom_qty": float(total_a_cases)}])

# Create a NEW move for product B on the same picking
mv_b_id = call("stock.move", "create", [{
    "picking_id": pick_id,
    "description_picking": f"Pick Product B from {WO_B}",
    "product_id": PROD_B,
    "product_uom_qty": float(total_b_cases),
    "product_uom": prod_b_uom,
    "location_id": move_a["location_id"][0],
    "location_dest_id": move_a["location_dest_id"][0],
}])
print(f"  created Product B move id={mv_b_id} demand={total_b_cases}")

# Helper to create move_lines
def create_ml(move_id, product_id, uom_id, lot_id, package_id, qty):
    call("stock.move.line", "create", [{
        "move_id": move_id,
        "picking_id": pick_id,
        "product_id": product_id,
        "product_uom_id": uom_id,
        "lot_id": lot_id,
        "package_id": package_id,
        "quantity": float(qty),
        "location_id": move_a["location_id"][0],
        "location_dest_id": move_a["location_dest_id"][0],
    }])

# Pure A pallets
print(f"\n  creating {len(PAL_A_PURE)} pure A move_lines...")
for n in PAL_A_PURE:
    pkg = call("stock.package", "search", [[("name", "=", f"WH/MO/01459-PAL-{n}")]], {"limit": 1})[0]
    cases = gen_meta(n)[1]
    create_ml(move_a["id"], PROD_A, prod_a_uom, LOT_A_ID, pkg, cases)

# Pure B pallets
print(f"  creating {len(PAL_B_PURE)} pure B move_lines...")
for n in PAL_B_PURE:
    pkg = call("stock.package", "search", [[("name", "=", f"WH/MO/01459-PAL-{n}")]], {"limit": 1})[0]
    cases = gen_meta(n)[1]
    create_ml(mv_b_id, PROD_B, prod_b_uom, LOT_B_ID, pkg, cases)

# Mixed pallets (2 move_lines each: 12 of A + 10 of B)
print(f"  creating {len(PAL_MIXED)} mixed pallets (each = 12A + 10B)...")
for n in PAL_MIXED:
    pkg = call("stock.package", "search", [[("name", "=", f"WH/MO/01459-PAL-{n}")]], {"limit": 1})[0]
    create_ml(move_a["id"], PROD_A, prod_a_uom, LOT_A_ID, pkg, 12)
    create_ml(mv_b_id, PROD_B, prod_b_uom, LOT_B_ID, pkg, 10)

# Verify
mls = call("stock.move.line", "search_read",
    [[("picking_id", "=", pick_id)]],
    {"fields": ["package_id", "quantity", "product_id", "lot_id"]})
print(f"\n=== final state ===")
print(f"  picking has {len(mls)} move_lines across {len(set(ml['package_id'][0] for ml in mls if ml['package_id']))} pallets")
print(f"\nReady to print: WH/OUT/01338 -> Print -> Warehouse Pick Sheet — MSP")
print(f"Expect 3 sections: Group A (12 pallets), Group B (8 pallets), Mixed (2 pallets)")
