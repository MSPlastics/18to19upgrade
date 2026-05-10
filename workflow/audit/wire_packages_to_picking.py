"""Workaround for known issue: Odoo's default reservation strategy doesn't
auto-prefer packaged quants over loose ones. Manually re-wire the outbound
delivery's move_lines to point at the actual MSP Pallet packages.

Idempotent: if move_lines already correctly wired, no-op.
"""
from __future__ import annotations
import _common as C

s = C.staging
state = C.state

PICK_ID = state["delivery_picking_ids"][0]
PROD_ID = state["target_product_id"]
PER_PALLET = state["target_per_pallet"]
PALLET_NAMES = state["pallet_ids"]
FG_LOT_NAME = state["fg_lot_name"]

# Resolve FG lot id
lot_id = s.search_read("stock.lot", [("name","=",FG_LOT_NAME),("product_id","=",PROD_ID)],
                       ["id"])[0]["id"]
# Resolve packages
pkgs = s.search_read("stock.package", [("name","in",PALLET_NAMES)], ["id","name"])
pkg_by_name = {p["name"]: p["id"] for p in pkgs}

# Read existing picking
pick = s.read_one("stock.picking", PICK_ID, ["name","state","move_ids","move_line_ids"])
print(f"Picking {pick['name']} state={pick['state']}")

# Read its main move (FG product)
moves = s.call("stock.move","read",[pick["move_ids"]],
    {"fields":["id","product_id","product_uom","product_uom_qty","quantity",
               "location_id","location_dest_id","move_line_ids"]})
fg_move = next(m for m in moves if m["product_id"][0] == PROD_ID)
print(f"  FG move id={fg_move['id']}: demand={fg_move['product_uom_qty']}, current move_lines={len(fg_move['move_line_ids'])}")

# Existing move_lines
existing = s.call("stock.move.line","read",[fg_move["move_line_ids"]],
    {"fields":["id","quantity","lot_id","package_id","result_package_id"]}) if fg_move["move_line_ids"] else []
already_wired = (
    len(existing) == len(PALLET_NAMES) and
    all(ml.get("package_id") and ml["package_id"][1] in PALLET_NAMES for ml in existing) and
    all(ml.get("result_package_id") and ml["result_package_id"][1] in PALLET_NAMES for ml in existing) and
    all(abs(ml["quantity"] - PER_PALLET) < 0.001 for ml in existing)
)
if already_wired:
    print("  already wired to packages, no-op")
    exit(0)

# Unreserve picking (releases the phantom reservation)
print("  unreserving picking...")
try:
    s.call_void("stock.picking", "do_unreserve", [[PICK_ID]])
    print("    OK")
except Exception as e:
    print(f"    do_unreserve failed: {e}")

# do_unreserve usually deletes move_lines; verify they're gone
remaining_mls = s.call("stock.move.line","search", [[("move_id","=",fg_move["id"])]])
if remaining_mls:
    print(f"  unlinking {len(remaining_mls)} stale move_line(s)")
    s.call_void("stock.move.line", "unlink", [remaining_mls])
else:
    print("  no stale move_lines to unlink (do_unreserve cleared them)")

# Create 2 new move_lines, one per package
for name in PALLET_NAMES:
    pkg_id = pkg_by_name[name]
    line_vals = {
        "move_id": fg_move["id"],
        "picking_id": PICK_ID,
        "product_id": PROD_ID,
        "product_uom_id": fg_move["product_uom"][0],
        "lot_id": lot_id,
        "package_id": pkg_id,           # source package (in WH/Stock)
        "result_package_id": pkg_id,    # destination package (pallet stays assembled to customer)
        "quantity": float(PER_PALLET),
        "location_id": fg_move["location_id"][0],
        "location_dest_id": fg_move["location_dest_id"][0],
    }
    new_id = s.call("stock.move.line", "create", [line_vals])
    print(f"  + move_line id={new_id} qty={PER_PALLET} pkg={name} lot={FG_LOT_NAME}")

# Re-read state
pick2 = s.read_one("stock.picking", PICK_ID, ["state","move_line_ids"])
print(f"\nfinal picking state={pick2['state']}, move_line_ids={pick2['move_line_ids']}")
