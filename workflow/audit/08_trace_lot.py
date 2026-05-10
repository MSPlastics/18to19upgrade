"""Phase 9 - bidirectional lot traceability.

Backward: FG lot -> MO -> raw move_lines -> raw lots
Forward:  one raw lot -> consumption move_lines -> MO -> FG lot -> delivery -> customer
"""
from __future__ import annotations
import datetime as _dt
from pathlib import Path
import _common as C

s = C.staging
state = C.state

FG_LOT_NAME = state["fg_lot_name"]
FG_LOT_ID = state["fg_lot_id"]
PROD_ID = state["target_product_id"]
PROD_NAME = state["target_product_name"]
QTY = state["target_qty"]
PARTNER_ID = state["target_partner_id"]
PARTNER_NAME = state["target_partner_name"]
MO_ID = state["mo_ids"][0]
MO_NAME = state["mo_names"][0]
REPORT = Path(state["report_path"])

# ============== BACKWARD ==============
C.log(f"=== Phase 9 BACKWARD: FG lot {FG_LOT_NAME} -> MO -> raw lots ===")
mo = s.read_one("mrp.production", MO_ID, ["name","move_raw_ids","lot_producing_ids"])
raw_moves = s.call("stock.move","read",[mo["move_raw_ids"]],
    {"fields":["product_id","quantity","move_line_ids"]})
backward_chain = {}  # product_name -> [(qty, lot_name)]
for rm in raw_moves:
    if not rm["move_line_ids"]: continue
    mls = s.call("stock.move.line","read",[rm["move_line_ids"]],
        {"fields":["quantity","lot_id"]})
    for ml in mls:
        lot = ml["lot_id"][1] if ml["lot_id"] else "(no lot)"
        backward_chain.setdefault(rm["product_id"][1], []).append((ml["quantity"], lot))

C.log(f"  {len(backward_chain)} raw materials consumed for FG lot {FG_LOT_NAME}:")
for pname, entries in sorted(backward_chain.items()):
    total = sum(q for q,_ in entries)
    lots = sorted(set(l for _,l in entries))
    C.log(f"    {pname:<28} total={total:<10.4f} lot(s)={lots}")

# Expected resin/blend materials must have a real lot (not None, not FIX_DATA_*_AUTO)
EXPECTED_RESINS = {"Butene1-BF","Clear Repro","Frac1-A","Exeed 1018.RA","conSLIP fast","conANTIBLOCK clarity"}
backward_checks = []
for material in EXPECTED_RESINS:
    if material not in backward_chain:
        backward_checks.append((f"{material} consumed", False, "not in raw consumption"))
        continue
    lots = set(l for _,l in backward_chain[material])
    has_real_lot = any(l and not l.startswith("FIX_DATA_") and l != "(no lot)" for l in lots)
    backward_checks.append((f"{material} traces to real lot",
                            has_real_lot, f"lots={lots}"))

# ============== FORWARD ==============
# Pick a raw lot we KNOW was used (Butene1-BF lot) and trace forward
C.log(f"\n=== Phase 9 FORWARD: raw lot 5615421-01 (Butene1-BF) -> consumption -> MO -> FG -> delivery -> customer ===")
RAW_LOT_NAME = "5615421-01"
raw_lot = s.search_read("stock.lot", [("name","=",RAW_LOT_NAME)], ["id","product_id"])
if not raw_lot:
    C.log(f"  raw lot {RAW_LOT_NAME} not found, can't forward-trace")
    forward_checks = [(f"raw lot {RAW_LOT_NAME} exists on Odoo", False, "not found")]
else:
    rid = raw_lot[0]["id"]
    # Find consumption move_lines for this lot in our MO's date window
    cons_mls = s.search_read("stock.move.line",
        [("lot_id","=",rid), ("move_id.raw_material_production_id","=",MO_ID)],
        ["id","quantity","move_id"])
    C.log(f"  {len(cons_mls)} consumption move_line(s) for {RAW_LOT_NAME} on {MO_NAME}")
    forward_total = sum(ml["quantity"] for ml in cons_mls)
    C.log(f"  total consumed: {forward_total} lb")

    # Walk forward to MO -> FG lot
    fg_lot = state["fg_lot_name"]
    # Walk forward to delivery
    deliveries = s.search_read("stock.move.line",
        [("lot_id","=",FG_LOT_ID),
         ("location_id.name","=","Stock"),
         ("location_dest_id.usage","=","customer"),
         ("state","=","done")],
        ["picking_id","quantity","location_dest_id"])
    deliv_ids = sorted({d["picking_id"][0] for d in deliveries if d["picking_id"]})
    C.log(f"  -> Forward path: raw lot {RAW_LOT_NAME} -> MO {MO_NAME} -> FG lot {fg_lot} -> delivery picking(s) {deliv_ids}")
    if deliv_ids:
        deliv = s.read_one("stock.picking", deliv_ids[0], ["name","partner_id","state"])
        partner_full = s.read_one("res.partner", deliv["partner_id"][0], ["id","name","commercial_partner_id"]) if deliv["partner_id"] else None
        commercial = partner_full["commercial_partner_id"][0] if partner_full and partner_full.get("commercial_partner_id") else None
        commercial_name = partner_full["commercial_partner_id"][1] if partner_full and partner_full.get("commercial_partner_id") else "-"
        C.log(f"  -> Delivery {deliv['name']} state={deliv['state']} to partner {deliv['partner_id']} (commercial={commercial_name})")
    else:
        commercial = None

    forward_checks = [
        (f"Raw lot {RAW_LOT_NAME} consumed on {MO_NAME}", len(cons_mls) > 0, f"{len(cons_mls)} move_lines, {forward_total} lb"),
        (f"FG lot {fg_lot} delivered to a customer-location picking",
         len(deliv_ids) >= 1, f"deliveries: {deliv_ids}"),
        (f"Delivery customer is or is contact under {PARTNER_NAME} (id {PARTNER_ID})",
         commercial == PARTNER_ID, f"commercial partner id={commercial}"),
    ]

# Combine
checks = backward_checks + forward_checks

ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
all_pass = all(ok for _, ok, _ in checks)
status = "PASS" if all_pass else "FAIL"

lines = [
    f"\n### {ts} - Phase 9: Lot traceability - **{status}**",
    "",
    f"### Backward trace: FG lot `{FG_LOT_NAME}` -> MO `{MO_NAME}` -> {len(backward_chain)} raw materials",
    "",
]
for pname, entries in sorted(backward_chain.items()):
    total = sum(q for q,_ in entries)
    lots = sorted(set(l for _,l in entries))
    lines.append(f"  - `{pname}`: total {total:.4f}, lot(s) {lots}")
lines.append("")
lines.append(f"### Forward trace: raw lot `{RAW_LOT_NAME}` (Butene1-BF) -> MO `{MO_NAME}` -> FG lot `{FG_LOT_NAME}` -> delivery -> customer `{PARTNER_NAME}`")
lines.append("")
lines.append("Checks:")
for label, ok, detail in checks:
    mark = "OK" if ok else "FAIL"
    lines.append(f"- [{mark}] {label} - {detail}")

text = REPORT.read_text(encoding="utf-8")
# split backward/forward stage rows
back_pass = all(ok for _, ok, _ in backward_checks)
fwd_pass = all(ok for _, ok, _ in forward_checks)
text = text.replace("| 9 - Lot trace backward | _pending_ | | |",
                    f"| 9 - Lot trace backward | {'PASS' if back_pass else 'FAIL'} | {'' if back_pass else 'see Phase 9'} | {'' if back_pass else 'major'} |")
text = text.replace("| 9 - Lot trace forward | _pending_ | | |",
                    f"| 9 - Lot trace forward | {'PASS' if fwd_pass else 'FAIL'} | {'' if fwd_pass else 'see Phase 9'} | {'' if fwd_pass else 'major'} |")
text += "\n".join(lines) + "\n"
REPORT.write_text(text, encoding="utf-8")
C.log(f"appended Phase 9 block ({status})")
print(f"\n  {status}")
