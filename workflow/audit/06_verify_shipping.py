"""Phase 7 - validate the outbound delivery and verify FG lot persists.

Calls stock.picking.button_validate, then verifies:
  - state=done
  - All move_lines kept their MO-level FG lot (no FIFO substitution)
  - Quants moved out of WH/Stock and into customer location
  - No backorder created (we shipped all 50)
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
QTY = state["target_qty"]
FG_LOT_NAME = state["fg_lot_name"]
PALLET_NAMES = state["pallet_ids"]
REPORT = Path(state["report_path"])

pick_before = s.read_one("stock.picking", PICK_ID, ["name","state","move_line_ids"])
C.log(f"=== Phase 7: validating {PICK_NAME} (state before: {pick_before['state']}) ===")

if pick_before["state"] == "done":
    C.log("  picking already done - skipping validate, just re-verifying")
else:
    try:
        C.log(f"  calling stock.picking.button_validate([{PICK_ID}])")
        s.call_void("stock.picking", "button_validate", [[PICK_ID]])
    except Exception as e:
        C.log(f"  button_validate failed: {e}")
        # Some validators return wizards (immediate transfer prompt). Try again with context.
        try:
            res = s.call("stock.picking", "button_validate", [[PICK_ID]],
                         {"context": {"skip_immediate": True, "skip_backorder": True}})
            C.log(f"  retry with skip_immediate context: {res}")
        except Exception as e2:
            raise SystemExit(f"validate failed: {e2}")

# Re-read
pick = s.read_one("stock.picking", PICK_ID, ["name","state","move_line_ids","backorder_id","backorder_ids","date_done"])
C.log(f"  state after: {pick['state']}, backorder_id={pick['backorder_id']}, backorder_ids={pick['backorder_ids']}, date_done={pick['date_done']}")

mls = s.call("stock.move.line","read",[pick["move_line_ids"]],
    {"fields":["quantity","lot_id","package_id","result_package_id","location_dest_id"]})
C.log(f"  {len(mls)} move_line(s):")
for ml in mls:
    pkg = ml["package_id"][1] if ml["package_id"] else "(none)"
    rpkg = ml["result_package_id"][1] if ml["result_package_id"] else "(none)"
    lot = ml["lot_id"][1] if ml["lot_id"] else "(no lot)"
    dst = ml["location_dest_id"][1] if ml["location_dest_id"] else "-"
    C.log(f"    qty={ml['quantity']:<6} lot={lot:<20} pkg={pkg:<25} dest={dst}")

# Quants check: should be at customer location now, no longer in WH/Stock
customer_quants = s.search_read("stock.quant",
    [("product_id","=",PROD_ID), ("lot_id.name","=",FG_LOT_NAME),
     ("location_id.usage","=","customer")],
    ["quantity","location_id","package_id"])
wh_quants = s.search_read("stock.quant",
    [("product_id","=",PROD_ID), ("lot_id.name","=",FG_LOT_NAME),
     ("location_id.name","=","Stock")],
    ["quantity","location_id","package_id"])
C.log(f"  customer quants: {len(customer_quants)} (qty {sum(q['quantity'] for q in customer_quants)})")
C.log(f"  WH/Stock quants: {len(wh_quants)} (qty {sum(q['quantity'] for q in wh_quants)})")

checks = []
checks.append((f"Picking state=done", pick["state"] == "done", f"actual {pick['state']}"))
checks.append((f"date_done populated", bool(pick["date_done"]), f"actual {pick['date_done']}"))
checks.append((f"No backorder created (shipped all {int(QTY)})",
               not pick["backorder_ids"], f"actual {pick['backorder_ids']}"))
checks.append((f"All shipped move_lines kept FG lot {FG_LOT_NAME}",
               all(ml["lot_id"] and ml["lot_id"][1] == FG_LOT_NAME for ml in mls),
               f"lots: {[ml['lot_id'][1] if ml['lot_id'] else None for ml in mls]}"))
checks.append((f"Total shipped qty == {QTY}",
               abs(sum(ml["quantity"] for ml in mls) - QTY) < 0.001,
               f"actual {sum(ml['quantity'] for ml in mls)}"))
checks.append((f"FG lot now at customer location ({QTY} units)",
               abs(sum(q["quantity"] for q in customer_quants) - QTY) < 0.001,
               f"actual {sum(q['quantity'] for q in customer_quants)}"))
checks.append(("FG lot no longer in WH/Stock",
               sum(q["quantity"] for q in wh_quants) == 0,
               f"WH/Stock qty {sum(q['quantity'] for q in wh_quants)}"))

ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
all_pass = all(ok for _, ok, _ in checks)
status = "PASS" if all_pass else "FAIL"

lines = [
    f"\n### {ts} - Phase 7: Shipping - **{status}**",
    "",
    f"- Picking **{PICK_NAME}** state=`{pick['state']}`, date_done=`{pick['date_done']}`, backorder_id=`{pick['backorder_id']}`",
    f"- {len(mls)} shipped move_line(s):",
]
for ml in mls:
    pkg = ml["result_package_id"][1] if ml["result_package_id"] else (ml["package_id"][1] if ml["package_id"] else "(none)")
    lines.append(f"  - qty={ml['quantity']} | lot=`{ml['lot_id'][1] if ml['lot_id'] else None}` | package=`{pkg}`")
lines.append("")
lines.append(f"- Customer location quants for FG lot: {len(customer_quants)} totaling {sum(q['quantity'] for q in customer_quants)}")
lines.append(f"- WH/Stock quants for FG lot: {len(wh_quants)} totaling {sum(q['quantity'] for q in wh_quants)}")
lines.append("")
lines.append("Checks:")
for label, ok, detail in checks:
    mark = "OK" if ok else "FAIL"
    lines.append(f"- [{mark}] {label} - {detail}")

text = REPORT.read_text(encoding="utf-8")
text = text.replace("| 7 - Shipping | _pending_ | | |",
                    f"| 7 - Shipping | {'PASS' if all_pass else 'FAIL'} | {'' if all_pass else 'see Phase 7 block'} | {'' if all_pass else 'blocker'} |")
text += "\n".join(lines) + "\n"
REPORT.write_text(text, encoding="utf-8")
C.log(f"appended Phase 7 block ({status})")
print(f"\n  {status}")
