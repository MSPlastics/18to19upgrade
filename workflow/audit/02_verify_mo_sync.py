"""Phase 3 - verify the MO landed on MES with correct metadata.

Reads the MO id(s) from audit_state.json, queries MES /api/work-orders for
matching entries, cross-checks Odoo workorder state, and appends a PASS/FAIL
block to the audit report.

Architecture note: MES does a live pull from Odoo (`mrp.workorder` search
filtered to states pending/waiting/ready/progress) every time /api/work-orders
is called. Only one workorder per MO surfaces at a time -- the current step.
Multi-step MOs progress: step1 ready -> step1 done + step2 ready -> ... -> all done.
"""
from __future__ import annotations
import datetime as _dt
from pathlib import Path
import _common as C

s = C.staging
state = C.state

required = ["mo_ids", "report_path", "target_product_name", "target_qty",
            "target_uom_name", "target_partner_name", "so_name", "fg_step_name"]
for k in required:
    if k not in state:
        raise SystemExit(f"missing state key {k!r} - run earlier phases first")

mo_id = state["mo_ids"][0]
mo_name = state["mo_names"][0]
PROD_NAME = state["target_product_name"]
QTY = state["target_qty"]
UOM_NAME = state["target_uom_name"]
PARTNER_NAME = state["target_partner_name"]
SO_NAME = state["so_name"]
PER_PALLET = state["target_per_pallet"]
EXPECTED_PALLETS = state["target_expected_pallets"]
MULTISTEP = state["multistep"]
LAST_STEP = state["last_step_name"]
REPORT = Path(state["report_path"])

# --- Odoo side: workorders + raw moves ---
mo = s.read_one("mrp.production", mo_id, ["name","state","workorder_ids","move_raw_ids","move_finished_ids"])
wos = s.call("mrp.workorder","read",[mo["workorder_ids"]],
    {"fields":["id","name","state","sequence","workcenter_id","operation_id","duration_expected"]})
wos.sort(key=lambda w: w["sequence"])
C.log(f"Odoo MO {mo['name']}: state={mo['state']}, {len(wos)} workorder(s)")
for w in wos:
    C.log(f"  WO seq={w['sequence']} name={w['name']:<15} state={w['state']:<10} wc={w['workcenter_id']} op={w['operation_id']}")

raw_moves = s.call("stock.move","read",[mo["move_raw_ids"]],
    {"fields":["id","product_id","product_uom_qty","product_uom","state","description_picking"]})
C.log(f"  {len(raw_moves)} raw moves:")
for rm in raw_moves:
    C.log(f"    {rm['product_id'][1][:50]:<50}  qty={rm['product_uom_qty']:<8}  uom={rm['product_uom'][1] if rm['product_uom'] else '-'}  state={rm['state']}")

fg_moves = s.call("stock.move","read",[mo["move_finished_ids"]],
    {"fields":["id","product_id","product_uom_qty","product_uom","state"]})
C.log(f"  {len(fg_moves)} finished moves:")
for fm in fg_moves:
    C.log(f"    {fm['product_id'][1]:<30}  qty={fm['product_uom_qty']:<8}  uom={fm['product_uom'][1]}  state={fm['state']}")

# --- MES side ---
mes_all = C.mes.get("/api/work-orders")
if isinstance(mes_all, dict) and "_error" in mes_all:
    raise SystemExit(f"MES /api/work-orders failed: {mes_all['_error']}: {mes_all['_body'][:200]}")
mes_wos = [w for w in mes_all if w.get("wo_number") == mo_name]
C.log(f"MES sees {len(mes_wos)} active work-order(s) for {mo_name}")

mes_wo_detail = None
mes_wo = None
if mes_wos:
    mes_wo = mes_wos[0]
    C.log(f"  MES WO: op={mes_wo.get('operation')}  wc={mes_wo.get('work_center')}  qty={mes_wo.get('target_qty')} {mes_wo.get('uom')}  customer={mes_wo.get('label_context',{}).get('customer')}  po={mes_wo.get('label_context',{}).get('customer_po')}  steps={mes_wo.get('total_steps')}/{mes_wo.get('current_step_seq')}")
    # Detail endpoint
    detail = C.mes.get(f"/api/work-orders/{mo_name}?wc={mes_wo.get('work_center')}")
    if isinstance(detail, dict) and "_error" not in detail:
        mes_wo_detail = detail
        C.log(f"  MES WO detail: count_per_unit={detail.get('count_per_unit')}  units_per_pallet={detail.get('units_per_pallet')}  total_rolls_ordered={detail.get('total_rolls_ordered')}  hoppers_count={len(detail.get('hoppers',[]))}")

# --- checks ---
checks = []
# Odoo workorders
checks.append(("MO has 2 workorders (multi-step BOM)", len(wos) == 2, f"actual {len(wos)}"))
if len(wos) >= 2:
    w0, w1 = wos[0], wos[-1]
    first_step_name = state.get("first_step_name", "")
    first_step_wc = state.get("first_step_wc", "")
    checks.append(
        (f"Step 1 is {first_step_name!r} on {first_step_wc!r}",
         (w0["operation_id"] and (not first_step_name or first_step_name in w0["operation_id"][1]))
         and (w0["workcenter_id"] and (not first_step_wc or first_step_wc in w0["workcenter_id"][1])),
         f"got op={w0['operation_id']}, wc={w0['workcenter_id']}"))
    checks.append((f"Step 2 is {LAST_STEP!r}",
                   w1["operation_id"] and (not LAST_STEP or LAST_STEP in w1["operation_id"][1]),
                   f"got {w1['operation_id']}"))
    checks.append(("Step 1 is state=ready (active)", w0["state"] == "ready", f"actual {w0['state']}"))
    checks.append(("Step 2 is state=blocked (waiting on step 1)", w1["state"] == "blocked", f"actual {w1['state']}"))

# Raw moves: BLEND lines on BOM expand into resin + additive constituents at MO time.
# Expected: 5 fixed packaging items + N blend constituents (N depends on the blend recipe).
PACKAGING_NAMES = {"3 inch cardboard core", "4x1 label units", "Poly Wrap", "Core Plugs", "4x6 Label"}
present_pkg = {rm["product_id"][1] for rm in raw_moves if rm["product_id"][1] in PACKAGING_NAMES}
blend_constituents = [rm for rm in raw_moves if rm["product_id"][1] not in PACKAGING_NAMES]
checks.append((f"All {len(PACKAGING_NAMES)} packaging components present", present_pkg == PACKAGING_NAMES,
               f"missing: {PACKAGING_NAMES - present_pkg}; actual: {present_pkg}"))
checks.append(("BLEND expanded into >= 1 resin/additive constituent", len(blend_constituents) >= 1,
               f"actual {len(blend_constituents)} constituents: {[r['product_id'][1] for r in blend_constituents]}"))

# FG moves
checks.append(("Exactly 1 finished move on MO", len(fg_moves) == 1, f"actual {len(fg_moves)}"))
if fg_moves:
    fm = fg_moves[0]
    checks.append((f"Finished move qty == {QTY} {UOM_NAME}", abs(fm['product_uom_qty'] - QTY) < 0.001 and fm['product_uom'][1] == UOM_NAME, f"actual {fm['product_uom_qty']} {fm['product_uom'][1] if fm['product_uom'] else '-'}"))

# MES side
checks.append((f"MES /api/work-orders shows {mo_name}", mes_wo is not None, f"got {len(mes_wos)} WO entries"))
if mes_wo:
    first_step_name = state.get("first_step_name", "")
    first_step_wc = state.get("first_step_wc", "")
    checks.append((f"MES WO operation == {first_step_name!r}",
                   not first_step_name or mes_wo.get("operation") == first_step_name,
                   f"actual {mes_wo.get('operation')}"))
    checks.append((f"MES WO work_center == {first_step_wc!r}",
                   not first_step_wc or mes_wo.get("work_center") == first_step_wc,
                   f"actual {mes_wo.get('work_center')}"))
    checks.append((f"MES WO target_qty == {QTY}", abs(float(mes_wo.get('target_qty', 0)) - QTY) < 0.001, f"actual {mes_wo.get('target_qty')}"))
    checks.append((f"MES WO uom == '{UOM_NAME}'", mes_wo.get("uom") == UOM_NAME, f"actual {mes_wo.get('uom')}"))
    lc = mes_wo.get("label_context", {})
    checks.append((f"MES sees customer == '{PARTNER_NAME}'", lc.get("customer") == PARTNER_NAME, f"actual {lc.get('customer')}"))
    checks.append((f"MES sees customer_po == '{SO_NAME}'", lc.get("customer_po") == SO_NAME, f"actual {lc.get('customer_po')}"))
if mes_wo_detail:
    expected_rolls = int(state.get("fg_roll_count") or QTY)
    checks.append((f"MES total_rolls_ordered == {expected_rolls}",
                   abs(float(mes_wo_detail.get("total_rolls_ordered") or 0) - expected_rolls) < 0.001,
                   f"actual {mes_wo_detail.get('total_rolls_ordered')}"))

# --- update report ---
ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
all_pass = all(ok for _, ok, _ in checks)
status = "PASS" if all_pass else "FAIL"

lines = [
    f"\n### {ts} - Phase 3: MO sync to MES - **{status}**",
    "",
    f"- Odoo MO: **{mo_name}** state=`{mo['state']}`, {len(wos)} workorder(s):",
]
for w in wos:
    wcname = w["workcenter_id"][1] if w["workcenter_id"] else "-"
    opname = w["operation_id"][1] if w["operation_id"] else "-"
    lines.append(f"  - seq {w['sequence']}: `{opname}` on `{wcname}`, state=`{w['state']}`, expected duration {w['duration_expected']}min")
lines.append(f"- Odoo raw moves: {len(raw_moves)}")
for rm in raw_moves:
    lines.append(f"  - `{rm['product_id'][1]}` x {rm['product_uom_qty']} {rm['product_uom'][1] if rm['product_uom'] else '-'} (state=`{rm['state']}`)")
lines.append(f"- Odoo finished moves: {len(fg_moves)}")
for fm in fg_moves:
    lines.append(f"  - `{fm['product_id'][1]}` x {fm['product_uom_qty']} {fm['product_uom'][1]} (state=`{fm['state']}`)")
lines.append("")
lines.append(f"- MES /api/work-orders sees: **{len(mes_wos)} active step** (multi-step MOs surface only the current step at a time)")
if mes_wo:
    lc = mes_wo.get("label_context", {})
    lines.append(f"  - operation=`{mes_wo.get('operation')}`, wc=`{mes_wo.get('work_center')}`, qty={mes_wo.get('target_qty')} {mes_wo.get('uom')}")
    lines.append(f"  - customer=`{lc.get('customer')}`, customer_po=`{lc.get('customer_po')}`, msp_drop_po=`{lc.get('msp_drop_po')}`")
    lines.append(f"  - target_feet={mes_wo.get('target_feet')}, density={mes_wo.get('density')}")
if mes_wo_detail:
    lines.append(f"  - count_per_unit={mes_wo_detail.get('count_per_unit')} (per pallet)")
    lines.append(f"  - hoppers configured: {len(mes_wo_detail.get('hoppers', []))}")
    lines.append(f"  - blend recipe id: {mes_wo_detail.get('recipe_id')}")
lines.append("")
lines.append("Checks:")
for label, ok, detail in checks:
    mark = "OK" if ok else "FAIL"
    lines.append(f"- [{mark}] {label} - {detail}")

text = REPORT.read_text(encoding="utf-8")
text = text.replace("| 3 - MES sync | _pending_ | | |",
                    f"| 3 - MES sync | {'PASS' if all_pass else 'FAIL'} | {'' if all_pass else 'see Phase 3 block'} | {'' if all_pass else 'blocker'} |")
text += "\n".join(lines) + "\n"
REPORT.write_text(text, encoding="utf-8")
C.log(f"appended Phase 3 block to {REPORT.name} ({status})")

if not all_pass:
    print("\n  At least one check FAILED. See report.")
    for l, ok, d in checks:
        if not ok: print(f"    FAIL: {l} -- {d}")
else:
    print(f"\n  PASS. MES sees MO {mo_name} with all expected metadata.")
print(f"\nNext: physically run the MR step on the operator UI for MO {mo_name}, then run 03_observe_production.py")
