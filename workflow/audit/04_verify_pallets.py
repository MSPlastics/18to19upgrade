"""Phase 5 - verify pallets reconciled to Odoo correctly.

Checks: stock.package records exist with right names, custom msp_* fields
populated, quants in each package match expected per-pallet count of FG lot.
"""
from __future__ import annotations
import datetime as _dt
from pathlib import Path
import _common as C

s = C.staging
state = C.state

PALLET_NAMES = state.get("pallet_ids") or []
EXPECTED_PALLETS = state["target_expected_pallets"]
PER_PALLET = state["target_per_pallet"]
FG_LOT_NAME = state.get("fg_lot_name")
PROD_ID = state["target_product_id"]
REPORT = Path(state["report_path"])
MO_ID = state["mo_ids"][0]
MO_NAME = state["mo_names"][0]


pkgs = s.search_read("stock.package", [("name","in",PALLET_NAMES)],
    ["id","name","msp_gross_weight_lb","msp_length_in","msp_width_in","msp_height_in",
     "msp_finalized_at","msp_mo_ids","msp_lot_ids","quant_ids","package_type_id"])
by_name = {p["name"]: p for p in pkgs}

C.log(f"=== Phase 5: pallet verify for {MO_NAME} ===")
C.log(f"  expected {len(PALLET_NAMES)} pallets, found {len(pkgs)} on Odoo")

checks = []
checks.append((f"All {len(PALLET_NAMES)} pallet packages exist on Odoo",
               len(pkgs) == len(PALLET_NAMES),
               f"missing: {set(PALLET_NAMES) - set(p['name'] for p in pkgs)}"))

for name in PALLET_NAMES:
    if name not in by_name:
        checks.append((f"{name} package present", False, "missing"))
        continue
    p = by_name[name]
    quants = s.call("stock.quant","read",[p["quant_ids"]],{"fields":["product_id","quantity","lot_id","location_id"]}) if p["quant_ids"] else []
    fg_quants = [q for q in quants if q["product_id"][0] == PROD_ID]
    fg_qty = sum(q["quantity"] for q in fg_quants)
    fg_lots = sorted(set(q["lot_id"][1] if q["lot_id"] else "(no lot)" for q in fg_quants))
    C.log(f"  pkg {name}: type={p['package_type_id']}, gross_weight={p['msp_gross_weight_lb']} lb, {len(quants)} quant(s), FG qty={fg_qty}, lots={fg_lots}")
    C.log(f"    msp_mo_ids={p['msp_mo_ids']}, msp_lot_ids={p['msp_lot_ids']}")
    checks.append((f"{name} package_type == 'MSP Pallet'",
                   p["package_type_id"] and p["package_type_id"][1] == "MSP Pallet",
                   f"got {p['package_type_id']}"))
    checks.append((f"{name} msp_gross_weight_lb populated",
                   p["msp_gross_weight_lb"] and p["msp_gross_weight_lb"] > 0,
                   f"got {p['msp_gross_weight_lb']}"))
    checks.append((f"{name} msp_finalized_at populated",
                   bool(p["msp_finalized_at"]),
                   f"got {p['msp_finalized_at']}"))
    checks.append((f"{name} contains {PER_PALLET} units of FG product",
                   abs(fg_qty - PER_PALLET) < 0.001,
                   f"actual {fg_qty}"))
    checks.append((f"{name} FG quants on FG lot {FG_LOT_NAME}",
                   FG_LOT_NAME in fg_lots,
                   f"actual lots {fg_lots}"))
    checks.append((f"{name} msp_mo_ids includes {MO_NAME}",
                   MO_ID in (p["msp_mo_ids"] or []),
                   f"msp_mo_ids={p['msp_mo_ids']}, looking for MO id {MO_ID}"))

ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
all_pass = all(ok for _, ok, _ in checks)
status = "PASS" if all_pass else "FAIL"

lines = [
    f"\n### {ts} - Phase 5: Pallet build + reconcile - **{status}**",
    "",
    f"- Expected {len(PALLET_NAMES)} pallets, found {len(pkgs)} stock.package(s) on Odoo",
]
for name in PALLET_NAMES:
    if name not in by_name:
        lines.append(f"  - **{name}** MISSING on Odoo"); continue
    p = by_name[name]
    quants = s.call("stock.quant","read",[p["quant_ids"]],{"fields":["product_id","quantity","lot_id"]}) if p["quant_ids"] else []
    fg_qty = sum(q["quantity"] for q in quants if q["product_id"][0] == PROD_ID)
    fg_lots = sorted(set(q["lot_id"][1] if q["lot_id"] else "(no lot)" for q in quants if q["product_id"][0] == PROD_ID))
    lines.append(f"  - **{name}**: gross={p['msp_gross_weight_lb']} lb, finalized_at={p['msp_finalized_at']}, dims={p['msp_length_in']}x{p['msp_width_in']}x{p['msp_height_in']} in, {len(quants)} quant(s), FG qty={fg_qty}, lots={fg_lots}")
    lines.append(f"    - msp_mo_ids={p['msp_mo_ids']}, msp_lot_ids={p['msp_lot_ids']}")
lines.append("")
lines.append("Checks:")
for label, ok, detail in checks:
    mark = "OK" if ok else "FAIL"
    lines.append(f"- [{mark}] {label} - {detail}")

text = REPORT.read_text(encoding="utf-8")
text = text.replace("| 5 - Pallet build + reconcile | _pending_ | | |",
                    f"| 5 - Pallet build + reconcile | {'PASS' if all_pass else 'FAIL'} | {'' if all_pass else 'see Phase 5 block'} | {'' if all_pass else 'blocker'} |")
text += "\n".join(lines) + "\n"
REPORT.write_text(text, encoding="utf-8")
C.log(f"appended Phase 5 block ({status})")
print(f"\n  {status}")
