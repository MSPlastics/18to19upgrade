"""Cancel the current SO+MO and clear audit state so Phase 1+ can re-run.

Run: python workflow/audit/reset_audit_state.py [--keep-silos]

What it does:
  1. Cancel MO via mrp.production.action_cancel (if state allows)
  2. Cancel SO via sale.order._action_cancel
  3. Drop SO/MO/roll/picking ids from audit_state.json (keep target_*)
  4. Strip Phase 1+ blocks from the audit report (so re-runs append cleanly)
"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path
import _common as C

ap = argparse.ArgumentParser()
ap.add_argument("--keep-silos", action="store_true", help="don't reset silo lots in this script (we use setup_silos.py for that)")
args = ap.parse_args()

s = C.staging
state = C.state

so_id = state.get("so_id")
mo_ids = state.get("mo_ids", [])

# ---- cancel MOs ----
for mo_id in mo_ids:
    mo = s.read_one("mrp.production", mo_id, ["name","state"])
    if not mo:
        C.log(f"  MO id={mo_id} not found in Odoo, skipping")
        continue
    if mo["state"] == "cancel":
        C.log(f"  MO {mo['name']} already cancel")
        continue
    if mo["state"] == "done":
        C.log(f"  WARN: MO {mo['name']} is done, cannot cancel")
        continue
    # action_cancel returns None - use call_void
    try:
        C.log(f"  cancelling MO {mo['name']} (state={mo['state']})")
        s.call_void("mrp.production", "action_cancel", [[mo_id]])
        mo2 = s.read_one("mrp.production", mo_id, ["state"])
        C.log(f"    -> state={mo2['state']}")
    except Exception as e:
        C.log(f"    cancel FAILED: {e}")

# ---- cancel SO ----
if so_id:
    so = s.read_one("sale.order", so_id, ["name","state"])
    if so and so["state"] not in ("cancel",):
        try:
            C.log(f"  cancelling SO {so['name']} (state={so['state']})")
            # _action_cancel is private; use action_cancel  v17+
            for method in ["action_cancel", "_action_cancel"]:
                try:
                    s.call_void("sale.order", method, [[so_id]])
                    C.log(f"    {method} OK")
                    break
                except Exception as e:
                    C.log(f"    {method} failed: {e}")
            so2 = s.read_one("sale.order", so_id, ["state"])
            C.log(f"    -> state={so2['state']}")
        except Exception as e:
            C.log(f"    cancel FAILED: {e}")

# ---- reset state ----
KEEP_KEYS = {"target_product_id","target_product_name","target_product_token",
             "target_qty","target_uom_id","target_uom_name",
             "target_partner_id","target_partner_name","target_partner_shipping_id",
             "target_per_pallet","target_expected_pallets",
             "bom_id","multistep","last_step_name","fg_step_name",
             "audit_started_at","report_path"}
all_keys = set(state.all().keys())
for k in all_keys - KEEP_KEYS:
    state._data.pop(k, None)
state._save()
C.log(f"  state reset; kept keys: {sorted(state.all().keys())}")

# ---- strip Phase 1+ blocks from report ----
report_path = Path(state["report_path"])
if report_path.exists():
    text = report_path.read_text(encoding="utf-8")
    # Reset matrix rows
    text = re.sub(r"\| (1 - SO created|2 - MO auto-created|3 - MES sync|4 - Production / consumption / FG lot|5 - Pallet build \+ reconcile|6 - Pick sheet|7 - Shipping|8 - Invoice|9 - Lot trace backward|9 - Lot trace forward) \| (PASS|FAIL) \|.*?\|.*?\|",
                  lambda m: f"| {m.group(1)} | _pending_ | | |", text)
    # Drop all "### YYYY-MM-DD HH:MM:SS - Phase N:" blocks
    text = re.sub(r"\n### \d{4}-\d\d-\d\d \d\d:\d\d:\d\d - Phase \d+.*?(?=\n### |\Z)",
                  "", text, flags=re.DOTALL)
    report_path.write_text(text, encoding="utf-8")
    C.log(f"  stripped phase blocks from {report_path.name}")

print("\nReset complete. Next: setup_silos.py (with real Odoo lots), then 01_create_so.py.")
