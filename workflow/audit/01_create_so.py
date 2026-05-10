"""Phase 1+2 - create the sale order on staging and confirm it.

Reads target_* from audit_state.json (written by 00_baseline.py). Creates a
single-line SO, confirms it, captures the resulting MO id(s) and delivery
picking id, and appends the Phase 1+2 PASS/FAIL block to the audit report.

Idempotency: if state already has `so_id`, we skip creation and just verify.
"""
from __future__ import annotations
import datetime as _dt
from pathlib import Path
import _common as C

s = C.staging
state = C.state

# --- preconditions ---
required = ["target_product_id", "target_qty", "target_uom_id",
            "target_partner_id", "report_path"]
for k in required:
    if k not in state:
        raise SystemExit(f"missing state key {k!r} - run 00_baseline.py first")

PROD_ID = state["target_product_id"]
PROD_NAME = state["target_product_name"]
QTY = state["target_qty"]
UOM_ID = state["target_uom_id"]
UOM_NAME = state["target_uom_name"]
PARTNER_ID = state["target_partner_id"]
PARTNER_NAME = state["target_partner_name"]
SHIP_ID = state.get("target_partner_shipping_id")
REPORT = Path(state["report_path"])

# --- create or load SO ---
so_id = state.get("so_id")
if so_id:
    C.log(f"state already has so_id={so_id} - verifying instead of creating")
    so = s.read_one("sale.order", so_id, ["id","name","state","partner_id","order_line"])
    if not so:
        raise SystemExit(f"so_id={so_id} in state but not found in Odoo - reset state")
else:
    line_vals = {
        "product_id": PROD_ID,
        "product_uom_qty": QTY,
        "product_uom_id": UOM_ID,
    }
    so_vals = {
        "partner_id": PARTNER_ID,
        "order_line": [(0, 0, line_vals)],
    }
    if SHIP_ID:
        so_vals["partner_shipping_id"] = SHIP_ID

    C.log(f"creating SO: partner={PARTNER_ID} ({PARTNER_NAME}), 1 line: product {PROD_ID} ({PROD_NAME}) x {QTY} {UOM_NAME}")
    so_id = s.call("sale.order", "create", [so_vals])
    state["so_id"] = so_id
    so = s.read_one("sale.order", so_id, ["id","name","state","partner_id","order_line"])
    C.log(f"  -> created SO id={so_id} name={so['name']} state={so['state']}")

    # confirm
    C.log(f"confirming SO {so['name']}")
    s.call_void("sale.order", "action_confirm", [[so_id]])
    so = s.read_one("sale.order", so_id, ["id","name","state","partner_id","order_line"])
    C.log(f"  -> after confirm: state={so['state']}")

state["so_name"] = so["name"]

# --- find auto-created MO(s) ---
mos = s.search_read("mrp.production",
    [("origin", "=", so["name"])],
    ["id","name","state","product_id","product_qty","product_uom_id","bom_id","date_start","picking_type_id","move_finished_ids","move_raw_ids","workorder_ids"],
    order="id ASC")
C.log(f"found {len(mos)} auto-created MO(s) for SO {so['name']}")
for mo in mos:
    C.log(f"  MO {mo['name']:<14}  state={mo['state']:<10}  product={mo['product_id']}  qty={mo['product_qty']}  bom={mo['bom_id']}")

state["mo_ids"] = [mo["id"] for mo in mos]
state["mo_names"] = [mo["name"] for mo in mos]

# --- find auto-created outgoing picking ---
pickings = s.search_read("stock.picking",
    [("origin","=",so["name"]), ("picking_type_id.code","=","outgoing")],
    ["id","name","state","origin","scheduled_date","move_ids"],
    order="id ASC")
C.log(f"found {len(pickings)} outgoing picking(s) for SO {so['name']}")
for p in pickings:
    C.log(f"  picking {p['name']:<14}  state={p['state']:<10}  scheduled={p['scheduled_date']}")
state["delivery_picking_ids"] = [p["id"] for p in pickings]
state["delivery_picking_names"] = [p["name"] for p in pickings]

# --- trigger MES inbound sync so the new MO is visible to /api/work-orders ---
# MES caches MO data via the periodic inbound sync (every ~5 min by default).
# Trigger it explicitly so the audit pipeline doesn't have to wait.
C.log("triggering MES /api/sync so the new MO is visible to /api/work-orders")
sync_r = C.mes.post("/api/sync", {})
C.log(f"  -> {sync_r}")

# --- pass/fail evaluation ---
checks = []
checks.append(("SO state == 'sale'", so["state"] == "sale", f"actual: {so['state']}"))
checks.append(("Exactly 1 MO created", len(mos) == 1, f"actual: {len(mos)} MO(s) - {[mo['name'] for mo in mos]}"))
if mos:
    mo = mos[0]
    checks.append(("MO product matches", mo["product_id"][0] == PROD_ID, f"expected={PROD_ID}, actual={mo['product_id']}"))
    checks.append(("MO qty matches", abs(mo["product_qty"] - QTY) < 0.001, f"expected={QTY}, actual={mo['product_qty']}"))
    checks.append(("MO BOM matches baseline", mo["bom_id"] and mo["bom_id"][0] == state["bom_id"], f"expected={state['bom_id']}, actual={mo['bom_id']}"))
checks.append(("Exactly 1 outgoing picking", len(pickings) == 1, f"actual: {len(pickings)}"))

# --- append result block to report ---
ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
all_pass = all(ok for _, ok, _ in checks)
status = "PASS" if all_pass else "FAIL"

lines = [
    f"\n### {ts} - Phase 1+2: Sale Order creation - **{status}**",
    "",
    f"- SO: **{so['name']}** (id {so_id}), state=`{so['state']}`",
    f"- Customer: {PARTNER_NAME} (id {PARTNER_ID})",
    f"- Line: product `{PROD_NAME}` (id {PROD_ID}) x **{QTY} {UOM_NAME}**",
    f"- Auto-created MO(s): " + (", ".join(f"**{mo['name']}** (id {mo['id']}, state=`{mo['state']}`)" for mo in mos) if mos else "_(none)_"),
    f"- Auto-created outgoing picking(s): " + (", ".join(f"**{p['name']}** (id {p['id']}, state=`{p['state']}`)" for p in pickings) if pickings else "_(none)_"),
    "",
    "Checks:",
]
for label, ok, detail in checks:
    mark = "OK" if ok else "FAIL"
    lines.append(f"- [{mark}] {label} - {detail}")

# update top-of-file matrix rows for stages 1 and 2
text = REPORT.read_text(encoding="utf-8")
for stage_label, ok in [("1 - SO created", checks[0][1]),
                        ("2 - MO auto-created", checks[1][1] and (len(checks)<5 or checks[2][1] and checks[3][1] and checks[4][1]))]:
    text = text.replace(f"| {stage_label} | _pending_ | | |",
                        f"| {stage_label} | {'PASS' if ok else 'FAIL'} | {'' if ok else 'see Phase 1+2 block'} | {'' if ok else 'blocker'} |")
text += "\n".join(lines) + "\n"
REPORT.write_text(text, encoding="utf-8")
C.log(f"appended Phase 1+2 block to {REPORT.name} ({status})")

if not all_pass:
    print("\n  At least one check FAILED. See report.")
else:
    print(f"\n  PASS. SO {so['name']}, MO {[mo['name'] for mo in mos]}, picking {[p['name'] for p in pickings]}")
print(f"\nNext: run 02_verify_mo_sync.py to confirm MES picked up the MO.")
