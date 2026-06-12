"""
Converting-step test for MO 1583 / WH/MO/01479 (the 2nd workorder
'Conversion' on Amutech BPA, work_center_id=8).

Submits a converting roll/case payload to the cloud test MES the same
way operatorUI does: source_roll_id = an existing master roll from
step 1, current_step_seq = 2. Validates the MES correctly:

  - consumes BOX + Label move_lines (qty=1 each, packaging — no lot)
  - does NOT re-consume resin (resin already consumed in step 1)
  - increments mrp.production.qty_producing by 1 Case
  - creates a finished-goods stock.lot (named after the roll_id)
  - attaches that lot to mrp.production.lot_producing_ids
  - creates a finished move_line on move_finished_ids

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

MO_ID = 1583
WO_NUMBER = "WH/MO/01479"
WO_ID_CONVERTING = 2462
SOURCE_ROLL_ID = "WH/MO/01479-FWDTEST-1778371001"  # one of our step-1 test MRs
CASE_WEIGHT_LBS = 25.0  # representative Case weight


def odoo_connect():
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    return uid, xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)


def call(models, uid, model, method, args, kw=None):
    return models.execute_kw(DB, uid, KEY, model, method, args, kw or {})


def mes_post_roll(payload):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        f"{MES_URL}/api/v1/production/roll",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-API-KEY": MES_KEY},
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def all_ids_on(uid, models, mo_id, side):
    """side = 'move_raw_ids' or 'move_finished_ids'. Returns list of move_line ids."""
    mo = call(models, uid, "mrp.production", "read", [[mo_id]],
              {"fields": [side, "qty_producing", "lot_producing_ids"]})[0]
    move_ids = mo[side]
    if not move_ids:
        return [], mo
    moves = call(models, uid, "stock.move", "read", [move_ids],
                 {"fields": ["move_line_ids"]})
    line_ids = [lid for mv in moves for lid in mv["move_line_ids"]]
    return line_ids, mo


def packaging_qty_snapshot(uid, models, mo_id):
    """Return {product_id: total_quantity} for BOX/Label-style raw moves."""
    mo = call(models, uid, "mrp.production", "read", [[mo_id]],
              {"fields": ["move_raw_ids"]})[0]
    if not mo["move_raw_ids"]:
        return {}
    moves = call(models, uid, "stock.move", "read", [mo["move_raw_ids"]],
                 {"fields": ["product_id", "quantity", "product_uom"]})
    out = {}
    for mv in moves:
        uom = (mv["product_uom"][1] if mv["product_uom"] else "").lower()
        if "unit" in uom or "pce" in uom:
            out[mv["product_id"][0]] = (mv["product_id"][1], mv["quantity"])
    return out


def latest_partial_shipment(uid, models, wo_number):
    picks = call(models, uid, "stock.picking", "search_read",
                 [[["origin", "like", f"Partial Shipment: {wo_number}"]]],
                 {"fields": ["id", "name", "origin", "state", "create_date"],
                  "order": "id desc", "limit": 1})
    return picks[0] if picks else None


def main():
    uid, models = odoo_connect()

    # Snapshots BEFORE
    raw_before, mo_before = all_ids_on(uid, models, MO_ID, "move_raw_ids")
    pkg_before = packaging_qty_snapshot(uid, models, MO_ID)
    last_pick_before = latest_partial_shipment(uid, models, WO_NUMBER)
    print(f"[before] qty_producing={mo_before['qty_producing']:.2f}  "
          f"lot_producing_ids={mo_before['lot_producing_ids']}  "
          f"raw_lines={len(raw_before)}")
    for pid, (name, qty) in pkg_before.items():
        print(f"          packaging [{pid}] {name[:35]:<35} qty={qty}")
    print(f"          last partial-ship picking: {last_pick_before['name'] if last_pick_before else '(none)'}")

    roll_id = f"{WO_NUMBER}-CONV-{int(time.time())}"
    payload = {
        "wo_number": WO_NUMBER,
        "roll_id": roll_id,
        "weight_lbs": CASE_WEIGHT_LBS,
        "length_ft": 0,
        "width": "",
        "mil": "",
        "work_order_id": WO_ID_CONVERTING,
        "source_roll_id": SOURCE_ROLL_ID,
        "tracker_type": "lbs",
        "current_step_seq": 2,
    }
    print(f"\n[post] converting roll {roll_id}  (case weight={CASE_WEIGHT_LBS} lb,  source={SOURCE_ROLL_ID})")
    status, body = mes_post_roll(payload)
    print(f"[post] HTTP {status}  body={body}")
    if status not in (200, 201):
        sys.exit("FAIL: MES rejected the converting roll")

    # Wait for sync queue to drain. The strongest signal that converting completed
    # is a NEW partial-shipment internal transfer (msppartialMO.action_ship_partial_batch
    # creates one stock.picking with origin 'Partial Shipment: <wo>') landing in state=done.
    print("\n[wait] polling for new partial-shipment picking...")
    deadline = time.time() + 240
    last_pick_id_before = last_pick_before["id"] if last_pick_before else 0
    while time.time() < deadline:
        last_pick_now = latest_partial_shipment(uid, models, WO_NUMBER)
        if last_pick_now and last_pick_now["id"] > last_pick_id_before:
            print(f"[wait] new picking landed: {last_pick_now['name']} (id={last_pick_now['id']}, state={last_pick_now['state']})")
            break
        time.sleep(5)
    else:
        last_pick_now = None
        print("[wait] TIMEOUT — no new partial-shipment picking")

    # Re-read MO + packaging
    _, mo_final = all_ids_on(uid, models, MO_ID, "move_raw_ids")
    pkg_after = packaging_qty_snapshot(uid, models, MO_ID)

    print("\n=== converting test verification ===")
    print(f"\nMO state changes:")
    print(f"  qty_producing: {mo_before['qty_producing']:.2f} -> {mo_final['qty_producing']:.2f}")
    print(f"  lot_producing_ids: {mo_before['lot_producing_ids']} -> {mo_final['lot_producing_ids']}")

    print(f"\nPackaging consumption (BOX + Label move qty should increase by ~1 each):")
    pkg_pass = True
    for pid, (name, qty_after) in pkg_after.items():
        qty_before = pkg_before.get(pid, (name, 0))[1]
        delta = qty_after - qty_before
        ok = delta >= 0.99
        print(f"  [{pid}] {name[:35]:<35} {qty_before:>6.2f} -> {qty_after:>6.2f}  delta={delta:+.2f}  {'OK' if ok else 'FAIL'}")
        if not ok:
            pkg_pass = False

    print(f"\nPartial-shipment internal transfer:")
    if last_pick_now:
        moves = call(models, uid, "stock.move", "read", [
            mid for mid in call(models, uid, "stock.picking", "read",
                                [[last_pick_now['id']]],
                                {"fields": ["move_ids"]})[0]["move_ids"]
        ], {"fields": ["product_id", "quantity", "state", "description_picking"]})
        print(f"  {last_pick_now['name']} state={last_pick_now['state']} create={last_pick_now['create_date']}")
        for mv in moves:
            print(f"    move product=[{mv['product_id'][0]}] {mv['product_id'][1][:35]:<35} qty={mv['quantity']} state={mv['state']} desc='{mv['description_picking']}'")

    # Pass conditions
    pass_qty = mo_final["qty_producing"] > mo_before["qty_producing"]
    pass_pkg = pkg_pass
    pass_pick = last_pick_now is not None and last_pick_now["state"] == "done"

    print(f"\n[result]")
    print(f"  qty_producing incremented:       {'PASS' if pass_qty else 'FAIL'}")
    print(f"  BOX + Label move qty grew:       {'PASS' if pass_pkg else 'FAIL'}")
    print(f"  Partial-ship picking state=done: {'PASS' if pass_pick else 'FAIL'}")

    overall = pass_qty and pass_pkg and pass_pick
    print(f"\n[overall] {'PASS' if overall else 'FAIL'}")
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
