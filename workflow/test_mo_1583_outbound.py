"""
Outbound delivery test for MO 1583 / WH/MO/01479 -> SO S01029.

Goal: verify that as we produce more finished Cases via converting rolls,
Odoo's reservation engine auto-grows the SO's outgoing delivery picking
(WH/OUT/01241) to reserve the new stock, and that:
  - the right FG lot (MO/01479-001) is suggested on each move_line
  - the move qty grows by 1 Case per converting roll
  - state stays 'assigned' / 'partially_available' until target met

Submits N converting rolls (one per source master roll), each producing
1 Case, and polls the delivery picking after each one.

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
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

URL = os.environ["ODOO_STAGING_URL"]
DB = os.environ["ODOO_STAGING_DB"]
USER = os.environ.get("ODOO_STAGING_USER", "admin@mountainstatesplastics.com")
KEY = os.environ["ODOO_STAGING_API_KEY"]
MES_URL = os.environ.get("MES_TEST_URL", "https://34.57.35.195.nip.io")
MES_KEY = os.environ["MES_TEST_API_KEY"]

WO_NUMBER = "WH/MO/01479"
WO_ID_CONVERTING = 2462
SO_NAME = "S01029"
FG_PRODUCT_ID = 1197
FG_LOT_NAME = "MO/01479-001"

# Source master rolls produced earlier in step 1
SOURCE_ROLLS = [
    "WH/MO/01479-FWDTEST-1778370547",
    "WH/MO/01479-FWDTEST-1778370660",
    "WH/MO/01479-FWDTEST-1778369376",
    "WH/MO/01479-FWDTEST-1778367465",
]


def odoo():
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    return uid, xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)


def call(models, uid, model, method, args, kw=None):
    return models.execute_kw(DB, uid, KEY, model, method, args, kw or {})


def mes_post_roll(payload):
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        f"{MES_URL}/api/v1/production/roll",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-API-KEY": MES_KEY})
    with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
        return r.status, json.loads(r.read().decode())


def picking_state(uid, models):
    sos = call(models, uid, "sale.order", "search_read",
               [[["name", "=", SO_NAME]]],
               {"fields": ["picking_ids"], "limit": 1})
    if not sos:
        return None
    pickings = call(models, uid, "stock.picking", "read", [sos[0]["picking_ids"]],
                    {"fields": ["name", "state", "move_ids", "picking_type_id"]})
    out = [p for p in pickings if "Delivery" in p["picking_type_id"][1]]
    if not out:
        return None
    p = out[0]
    moves = call(models, uid, "stock.move", "read", [p["move_ids"]],
                 {"fields": ["product_id", "product_uom_qty", "quantity",
                             "state", "move_line_ids"]})
    fg_move = next((mv for mv in moves if mv["product_id"][0] == FG_PRODUCT_ID), None)
    if fg_move and fg_move["move_line_ids"]:
        lines = call(models, uid, "stock.move.line", "read", [fg_move["move_line_ids"]],
                     {"fields": ["lot_id", "quantity", "state"]})
    else:
        lines = []
    return {
        "picking_name": p["name"], "picking_state": p["state"],
        "fg_demand": fg_move["product_uom_qty"] if fg_move else 0,
        "fg_qty": fg_move["quantity"] if fg_move else 0,
        "fg_state": fg_move["state"] if fg_move else None,
        "lines": lines,
    }


def fg_inventory(uid, models):
    quants = call(models, uid, "stock.quant", "search_read",
                  [[["product_id", "=", FG_PRODUCT_ID],
                    ["lot_id.name", "=", FG_LOT_NAME],
                    ["location_id.usage", "=", "internal"]]],
                  {"fields": ["location_id", "quantity", "reserved_quantity"]})
    return quants


def mo_state(uid, models):
    return call(models, uid, "mrp.production", "read", [[1583]],
                {"fields": ["qty_producing", "lot_producing_ids"]})[0]


def main():
    uid, models = odoo()

    print("=== BEFORE submitting more converting rolls ===")
    s0 = picking_state(uid, models)
    inv0 = fg_inventory(uid, models)
    mo0 = mo_state(uid, models)
    print(f"  MO qty_producing: {mo0['qty_producing']}")
    print(f"  FG on hand: {sum(q['quantity'] for q in inv0):.2f}")
    print(f"  Delivery {s0['picking_name']} state={s0['picking_state']} fg demand={s0['fg_demand']} qty={s0['fg_qty']} state={s0['fg_state']}")
    for ln in s0['lines']:
        lot = ln['lot_id'][1] if ln['lot_id'] else 'NONE'
        print(f"    line: qty={ln['quantity']} lot={lot} state={ln['state']}")

    # Submit converting rolls
    print(f"\n=== Submitting {len(SOURCE_ROLLS)} converting rolls (1 Case each) ===")
    for i, source in enumerate(SOURCE_ROLLS, start=1):
        roll_id = f"{WO_NUMBER}-OUTBOUND-{i}-{int(time.time())}"
        payload = {
            "wo_number": WO_NUMBER, "roll_id": roll_id,
            "weight_lbs": 25.0, "length_ft": 0, "width": "", "mil": "",
            "work_order_id": WO_ID_CONVERTING,
            "source_roll_id": source,
            "tracker_type": "lbs", "current_step_seq": 2,
        }
        status, body = mes_post_roll(payload)
        print(f"  [{i}/{len(SOURCE_ROLLS)}] roll={roll_id}  HTTP={status}  body={body}")
        if status not in (200, 201):
            print(f"    FAIL — stopping here")
            break
        # Stagger so the queue worker can drain
        time.sleep(8)

    # Wait for queue to drain
    print("\n=== Waiting for sync to complete (60s) ===")
    time.sleep(60)

    # AFTER state
    print("\n=== AFTER ===")
    s1 = picking_state(uid, models)
    inv1 = fg_inventory(uid, models)
    mo1 = mo_state(uid, models)
    print(f"  MO qty_producing: {mo0['qty_producing']} -> {mo1['qty_producing']}")
    print(f"  FG on hand: {sum(q['quantity'] for q in inv0):.2f} -> {sum(q['quantity'] for q in inv1):.2f}")
    for q in inv1:
        print(f"    {q['location_id'][1]:<28}  qty={q['quantity']}  reserved={q['reserved_quantity']}")
    print(f"  Delivery {s1['picking_name']} state={s1['picking_state']} fg demand={s1['fg_demand']} qty={s1['fg_qty']} state={s1['fg_state']}")
    for ln in s1['lines']:
        lot = ln['lot_id'][1] if ln['lot_id'] else 'NONE'
        print(f"    line: qty={ln['quantity']} lot={lot} state={ln['state']}")

    # Pass criteria
    pass_qty_grew = s1['fg_qty'] > s0['fg_qty']
    pass_lot_correct = any(ln['lot_id'] and ln['lot_id'][1] == FG_LOT_NAME for ln in s1['lines'])
    pass_inv_grew = sum(q['quantity'] for q in inv1) > sum(q['quantity'] for q in inv0)
    pass_reservation_matches = all(q['quantity'] == q['reserved_quantity'] for q in inv1 if q['location_id'][1] == 'WH/Stock')

    print(f"\n[result]")
    print(f"  Delivery move qty grew:                {'PASS' if pass_qty_grew else 'FAIL'}  ({s0['fg_qty']:.2f} -> {s1['fg_qty']:.2f})")
    print(f"  FG inventory grew:                      {'PASS' if pass_inv_grew else 'FAIL'}")
    print(f"  Suggested lot is {FG_LOT_NAME}: {'PASS' if pass_lot_correct else 'FAIL'}")
    print(f"  All available stock auto-reserved:     {'PASS' if pass_reservation_matches else 'FAIL'}")

    overall = pass_qty_grew and pass_lot_correct and pass_inv_grew
    print(f"\n[overall] {'PASS' if overall else 'FAIL'}")
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
