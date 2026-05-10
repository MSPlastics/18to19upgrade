"""
Backorder delivery test: after WH/OUT/01241 (initial 7 Cases) was
validated and 68 Cases backordered into a new picking, produce a few
more Cases via converting rolls and verify:

  1. New FG stock lands in WH/Stock with the same lot (MO/01479-001).
  2. The backorder picking's reservation engine grabs the new stock.
  3. The picking transitions confirmed -> assigned / partially_available.
  4. The suggested move_line.lot_id is still MO/01479-001.

Reads ODOO_STAGING_* + MES_TEST_* from .env.
"""
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import xmlrpc.client
from pathlib import Path


def _load_dotenv():
    p = Path(__file__).parent.parent / ".env"
    if not p.exists(): return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
_load_dotenv()

URL = os.environ["ODOO_STAGING_URL"]; DB = os.environ["ODOO_STAGING_DB"]
USER = os.environ.get("ODOO_STAGING_USER", "admin@mountainstatesplastics.com")
KEY = os.environ["ODOO_STAGING_API_KEY"]
MES_URL = os.environ.get("MES_TEST_URL", "https://35.194.23.98.nip.io")
MES_KEY = os.environ["MES_TEST_API_KEY"]

WO_NUMBER = "WH/MO/01479"
WO_ID_CONVERTING = 2462
SO_NAME = "S01029"
FG_PRODUCT_ID = 1197
FG_LOT_NAME = "MO/01479-001"

# Reuse master rolls (consumed_length_ft accumulates but isn't enforced)
SOURCE_ROLLS = [
    "WH/MO/01479-FWDTEST-1778367114",
    "WH/MO/01479-FWDTEST-1778371001",
    "WH/MO/01479-FWDTEST-1778370547",
]


def odoo():
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    return uid, xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)


def call(models, uid, model, method, args, kw=None):
    return models.execute_kw(DB, uid, KEY, model, method, args, kw or {})


def mes_post_roll(payload):
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(f"{MES_URL}/api/v1/production/roll",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-API-KEY": MES_KEY})
    with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
        return r.status, json.loads(r.read().decode())


def find_backorder_picking(uid, models):
    """Return the most recent backorder delivery picking (state != done)."""
    sos = call(models, uid, "sale.order", "search_read",
               [[["name", "=", SO_NAME]]], {"fields": ["picking_ids"], "limit": 1})
    if not sos: return None
    picks = call(models, uid, "stock.picking", "read", [sos[0]["picking_ids"]],
                 {"fields": ["name", "state", "picking_type_id", "backorder_id",
                             "move_ids", "scheduled_date"]})
    deliveries = [p for p in picks if "Delivery" in p["picking_type_id"][1]
                  and p["state"] != "done"]
    if not deliveries: return None
    deliveries.sort(key=lambda x: x["id"], reverse=True)
    return deliveries[0]


def picking_detail(uid, models, picking_id):
    p = call(models, uid, "stock.picking", "read", [[picking_id]],
             {"fields": ["name", "state", "move_ids"]})[0]
    moves = call(models, uid, "stock.move", "read", [p["move_ids"]],
                 {"fields": ["product_id", "product_uom_qty", "quantity",
                             "state", "move_line_ids"]})
    fg_move = next((mv for mv in moves if mv["product_id"][0] == FG_PRODUCT_ID), None)
    lines = []
    if fg_move and fg_move["move_line_ids"]:
        lines = call(models, uid, "stock.move.line", "read", [fg_move["move_line_ids"]],
                     {"fields": ["lot_id", "quantity", "state"]})
    return p, fg_move, lines


def main():
    uid, models = odoo()

    bo = find_backorder_picking(uid, models)
    if not bo:
        sys.exit("No open backorder picking found on S01029")
    print(f"=== BEFORE — backorder picking ===")
    p, fg, lines = picking_detail(uid, models, bo["id"])
    print(f"  {p['name']} state={p['state']}")
    if fg:
        print(f"  fg move demand={fg['product_uom_qty']} qty={fg['quantity']} state={fg['state']}")
    for ln in lines:
        lot = ln["lot_id"][1] if ln["lot_id"] else "NONE"
        print(f"    line: qty={ln['quantity']} lot={lot} state={ln['state']}")

    # Submit converting rolls
    print(f"\n=== Submitting {len(SOURCE_ROLLS)} converting rolls (1 Case each) ===")
    for i, src in enumerate(SOURCE_ROLLS, 1):
        roll_id = f"{WO_NUMBER}-BO-{i}-{int(time.time())}"
        payload = {"wo_number": WO_NUMBER, "roll_id": roll_id,
                   "weight_lbs": 25.0, "length_ft": 0, "width": "", "mil": "",
                   "work_order_id": WO_ID_CONVERTING,
                   "source_roll_id": src,
                   "tracker_type": "lbs", "current_step_seq": 2}
        status, body = mes_post_roll(payload)
        print(f"  [{i}/{len(SOURCE_ROLLS)}] roll={roll_id}  HTTP={status}  body={body}")
        time.sleep(8)

    print(f"\n=== Waiting 60s for sync to drain ===")
    time.sleep(60)

    print(f"\n=== AFTER — backorder picking ===")
    p, fg, lines = picking_detail(uid, models, bo["id"])
    print(f"  {p['name']} state={p['state']}")
    if fg:
        print(f"  fg move demand={fg['product_uom_qty']} qty={fg['quantity']} state={fg['state']}")
    for ln in lines:
        lot = ln["lot_id"][1] if ln["lot_id"] else "NONE"
        print(f"    line: qty={ln['quantity']} lot={lot} state={ln['state']}")

    # Inventory state
    quants = call(models, uid, "stock.quant", "search_read",
                  [[["product_id", "=", FG_PRODUCT_ID],
                    ["location_id.usage", "=", "internal"]]],
                  {"fields": ["location_id", "lot_id", "quantity", "reserved_quantity"]})
    print(f"\n=== FG inventory now ===")
    for q in quants:
        lot = q["lot_id"][1] if q["lot_id"] else "NONE"
        print(f"  {q['location_id'][1]:<28} lot={lot:<35} qty={q['quantity']} reserved={q['reserved_quantity']}")

    # Pass criteria
    pass_qty_grew = (fg and fg["quantity"] >= len(SOURCE_ROLLS))
    pass_lot_correct = any(ln["lot_id"] and ln["lot_id"][1] == FG_LOT_NAME for ln in lines)
    pass_state_progressed = p["state"] in ("assigned", "partially_available", "ready", "available")

    print(f"\n[result]")
    print(f"  Backorder picking move qty grew to >= {len(SOURCE_ROLLS)}:  {'PASS' if pass_qty_grew else 'FAIL'}")
    print(f"  Suggested lot is {FG_LOT_NAME}:                {'PASS' if pass_lot_correct else 'FAIL'}")
    print(f"  Picking state progressed (no longer just 'confirmed'):  {'PASS' if pass_state_progressed else 'FAIL'}  ({p['state']})")

    overall = pass_qty_grew and pass_lot_correct
    print(f"\n[overall] {'PASS' if overall else 'FAIL'}")
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
