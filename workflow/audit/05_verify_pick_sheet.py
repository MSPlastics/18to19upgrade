"""Phase 6 - verify the data the Warehouse Pick Sheet will render.

PDF rendering via XMLRPC is blocked in v19 (`_render_qweb_pdf` is private).
So this verifies the underlying data the QWeb template reads:
  - move_lines per pallet with correct qty + lot + package
  - Order Summary aggregate
  - Grand Total per UoM
  - Per-pallet contents column data

The actual PDF must be visually verified by manually printing on Odoo UI.
"""
from __future__ import annotations
import datetime as _dt
from pathlib import Path
import _common as C

s = C.staging
state = C.state

PICK_ID = state["delivery_picking_ids"][0]
PICK_NAME = state["delivery_picking_names"][0]
PROD_ID = state["target_product_id"]
PROD_NAME = state["target_product_name"]
QTY = state["target_qty"]
PER_PALLET = state["target_per_pallet"]
EXPECTED_PALLETS = state["target_expected_pallets"]
UOM_NAME = state["target_uom_name"]
FG_LOT_NAME = state["fg_lot_name"]
PALLET_NAMES = state["pallet_ids"]
REPORT = Path(state["report_path"])

# Read picking + move + move_lines
pick = s.read_one("stock.picking", PICK_ID, ["name","state","move_ids","move_line_ids","msp_pallet_ids"])
mls = s.call("stock.move.line","read",[pick["move_line_ids"]],
    {"fields":["id","quantity","product_id","lot_id","package_id","product_uom_id"]})
C.log(f"Picking {pick['name']} state={pick['state']}, {len(mls)} move_line(s), msp_pallet_ids={pick.get('msp_pallet_ids')}")

# Group by package -> per-pallet view (matches pick sheet layout)
by_pkg = {}
for ml in mls:
    pkg_name = ml["package_id"][1] if ml["package_id"] else "(loose)"
    by_pkg.setdefault(pkg_name, []).append(ml)

# Per-product totals -> matches Order Summary
by_prod_lot = {}
for ml in mls:
    key = (ml["product_id"][1], ml["lot_id"][1] if ml["lot_id"] else "(no lot)", ml["product_uom_id"][1] if ml["product_uom_id"] else "-")
    by_prod_lot[key] = by_prod_lot.get(key, 0) + ml["quantity"]

C.log(f"  per-pallet view ({len(by_pkg)} groups):")
for pkg_name, mls_for_pkg in sorted(by_pkg.items()):
    qtys = sum(ml["quantity"] for ml in mls_for_pkg)
    lots = set(ml["lot_id"][1] if ml["lot_id"] else "(no lot)" for ml in mls_for_pkg)
    C.log(f"    {pkg_name}: {len(mls_for_pkg)} line(s), {qtys} units, lot(s)={lots}")

C.log(f"  per-product summary ({len(by_prod_lot)} rows):")
for (pname, lot, uom), qty in sorted(by_prod_lot.items()):
    C.log(f"    {pname} | lot {lot} | {qty} {uom}")

# Verify msp_pallet_ids is populated (msp_pallet addon's compute on stock.picking)
expected_pallet_ids = sorted(p["id"] for p in s.search_read("stock.package", [("name","in",PALLET_NAMES)], ["id"]))
actual_pallet_ids = sorted(pick.get("msp_pallet_ids") or [])

# Verify the QWeb report action exists
report_actions = s.search_read("ir.actions.report",
    [("model","=","stock.picking"), ("name","ilike","Warehouse Pick Sheet")],
    ["id","name","report_name","binding_model_id"])
C.log(f"  pick sheet report actions: {len(report_actions)}")
for r in report_actions:
    C.log(f"    id={r['id']} name='{r['name']}' report_name={r['report_name']}")

checks = []
checks.append((f"Picking has {EXPECTED_PALLETS} package(s) referenced", len(by_pkg) == EXPECTED_PALLETS, f"actual {len(by_pkg)}: {sorted(by_pkg.keys())}"))
for name in PALLET_NAMES:
    if name not in by_pkg:
        checks.append((f"Pallet {name} present on picking", False, "missing")); continue
    pkg_mls = by_pkg[name]
    qty = sum(ml["quantity"] for ml in pkg_mls)
    checks.append((f"Pallet {name} has {PER_PALLET} units", abs(qty - PER_PALLET) < 0.001, f"actual {qty}"))
    checks.append((f"Pallet {name} units have FG lot {FG_LOT_NAME}",
                   all(ml["lot_id"] and ml["lot_id"][1] == FG_LOT_NAME for ml in pkg_mls),
                   f"lots: {[ml['lot_id'][1] if ml['lot_id'] else None for ml in pkg_mls]}"))
checks.append((f"Order Summary: 1 row for {PROD_NAME} x {QTY} {UOM_NAME} on lot {FG_LOT_NAME}",
               by_prod_lot == {(PROD_NAME, FG_LOT_NAME, UOM_NAME): QTY},
               f"actual {by_prod_lot}"))
checks.append(("msp_pallet_ids on picking matches packages",
               actual_pallet_ids == expected_pallet_ids,
               f"actual {actual_pallet_ids}, expected {expected_pallet_ids}"))
checks.append(("Warehouse Pick Sheet report action exists", len(report_actions) >= 1, f"found {len(report_actions)}"))

ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
all_pass = all(ok for _, ok, _ in checks)
status = "PASS" if all_pass else "FAIL"

lines = [
    f"\n### {ts} - Phase 6: Pick Sheet data correctness - **{status}**",
    "",
    f"- Picking: **{PICK_NAME}** state=`{pick['state']}`",
    f"- {len(mls)} move_line(s) across {len(by_pkg)} package(s)",
    "",
    "Per-pallet view (matches Pick Checklist row order):",
]
for pkg_name in sorted(by_pkg):
    lots = set(ml["lot_id"][1] if ml["lot_id"] else "(no lot)" for ml in by_pkg[pkg_name])
    qty = sum(ml["quantity"] for ml in by_pkg[pkg_name])
    lines.append(f"  - **{pkg_name}**: {qty} units, lot(s) {sorted(lots)}")
lines.append("")
lines.append("Order Summary (matches bottom-of-sheet table):")
for (pname, lot, uom), qty in sorted(by_prod_lot.items()):
    lines.append(f"  - `{pname}` | lot `{lot}` | total **{qty} {uom}**")
lines.append("")
lines.append("Note: PDF render must be manually printed on Odoo UI to verify the visual layout. "
             "v19 made `_render_qweb_pdf` private to RPC.")
lines.append("")
lines.append("Checks:")
for label, ok, detail in checks:
    mark = "OK" if ok else "FAIL"
    lines.append(f"- [{mark}] {label} - {detail}")

text = REPORT.read_text(encoding="utf-8")
text = text.replace("| 6 - Pick sheet | _pending_ | | |",
                    f"| 6 - Pick sheet | {'PASS' if all_pass else 'FAIL'} | {'data only - PDF render not auto-verified' if all_pass else 'see Phase 6 block'} | {'cosmetic-pdf' if all_pass else 'blocker'} |")
text += "\n".join(lines) + "\n"
REPORT.write_text(text, encoding="utf-8")
C.log(f"appended Phase 6 block ({status})")
print(f"\n  {status}")
