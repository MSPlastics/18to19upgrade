"""
Inline single-step test for MO 93 / WH/MO/00094 (`In-line extrusion` on
Line 6 6" Davis, work_center_id=5).

Submits a 100 lb roll with current_step_seq=1 against the single-WO MO.
Validates that MES correctly:

  - is_extrusion path fires (total_steps=1 OR extrusion WC)
  - resin consumption gets distributed by hopper percentages
  - BOX + Label also consume in the SAME pass (single-step inline
    consumes packaging too, unlike multi-step extrusion which leaves
    packaging for converting)
  - FG production block fires (single-step always produces FG)
  - mrp.production.qty_producing increments
  - Partial-shipment internal transfer created in state=done

Known drift to flag (not fixed by this test):
  The MES blend recipe (x_blends id=6) lists a single legacy additive
  con-Antiblock/slip (product 579) at 2%, but the BOM splits this into
  conANTIBLOCK clarity (40) at ~1% + conSLIP slow (42) at ~1%. record_roll
  builds consumed_lots from hoppers JSON (= legacy product), so the
  substring match against the BOM products fails for the additives, and
  the "MES provided blend but resin not matched" gate SKIPS them. The
  Butene/Frac match cleanly.

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

MO_ID = 93
WO_NUMBER = "WH/MO/00094"
WO_ID_INLINE = 140
WEIGHT_LBS = 100.0

EXPECTED_LOTS = {
    45: ("Butene1-BF",            "TEST-2026-05-09-Butene1-BF-001"),
    3:  ("Frac1-A",               "TEST-2026-05-09-Frac1-A-001"),
    # clarity (40) + slow (42) are in BOM but blend drift means they
    # may be skipped or hit fallback; we don't enforce qty/lot on them.
}
# Hopper JSON: Butene1-BF 83, Frac1-A 15, con-Antiblock/slip 2.
# For 100 lb: Butene 83.0, Frac 15.0.
EXPECTED_QTY = {
    45: 83.0,
    3:  15.0,
}
TOL = 0.01


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
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def all_raw_line_ids(uid, models):
    mo = call(models, uid, "mrp.production", "read", [[MO_ID]],
              {"fields": ["move_raw_ids", "qty_producing", "lot_producing_ids"]})[0]
    if not mo["move_raw_ids"]:
        return [], mo
    moves = call(models, uid, "stock.move", "read", [mo["move_raw_ids"]],
                 {"fields": ["move_line_ids"]})
    return [lid for mv in moves for lid in mv["move_line_ids"]], mo


def latest_partial_shipment(uid, models):
    picks = call(models, uid, "stock.picking", "search_read",
                 [[["origin", "like", f"Partial Shipment: {WO_NUMBER}"]]],
                 {"fields": ["id", "name", "state"], "order": "id desc", "limit": 1})
    return picks[0] if picks else None


def main():
    uid, models = odoo()
    raw_before, mo_before = all_raw_line_ids(uid, models)
    pick_before = latest_partial_shipment(uid, models)
    print(f"[before] qty_producing={mo_before['qty_producing']}  "
          f"lot_producing_ids={mo_before['lot_producing_ids']}  "
          f"raw_lines={len(raw_before)}  last_pick={pick_before['name'] if pick_before else '(none)'}")

    roll_id = f"{WO_NUMBER}-INLINE-{int(time.time())}"
    payload = {
        "wo_number": WO_NUMBER, "roll_id": roll_id,
        "weight_lbs": WEIGHT_LBS, "length_ft": 0,
        "width": "", "mil": "",
        "work_order_id": WO_ID_INLINE,
        "tracker_type": "lbs",
        "current_step_seq": 1,  # inline = single step, but seq still 1
    }
    print(f"\n[post] inline roll {roll_id} ({WEIGHT_LBS} lb)")
    status, body = mes_post_roll(payload)
    print(f"[post] HTTP {status}  body={body}")
    if status not in (200, 201):
        sys.exit("FAIL: MES rejected the roll")

    # Wait for sync — strongest signal: new partial-ship picking
    pick_id_before = pick_before["id"] if pick_before else 0
    print(f"\n[wait] polling for new partial-shipment picking (or timeout 240s)...")
    deadline = time.time() + 240
    pick_after = None
    while time.time() < deadline:
        cur = latest_partial_shipment(uid, models)
        if cur and cur["id"] > pick_id_before:
            pick_after = cur
            print(f"[wait] new picking: {cur['name']} state={cur['state']}")
            break
        time.sleep(5)
    else:
        print("[wait] TIMEOUT")

    raw_after, mo_after = all_raw_line_ids(uid, models)
    new_lines = [lid for lid in raw_after if lid not in raw_before]

    print(f"\n=== Inline test verification ===")
    print(f"\nMO state changes:")
    print(f"  qty_producing: {mo_before['qty_producing']} -> {mo_after['qty_producing']}")
    print(f"  lot_producing_ids: {mo_before['lot_producing_ids']} -> {mo_after['lot_producing_ids']}")

    print(f"\n{len(new_lines)} new raw move_lines:")
    by_pid = {}
    if new_lines:
        rows = call(models, uid, "stock.move.line", "read", [new_lines],
                    {"fields": ["product_id", "lot_id", "quantity", "state"]})
        for ln in rows:
            lot = ln["lot_id"][1] if ln["lot_id"] else "(no lot)"
            by_pid.setdefault(ln["product_id"][0], []).append(ln)
            print(f"  [{ln['product_id'][0]:>4}] {ln['product_id'][1][:32]:<32} qty={ln['quantity']:>8.4f}  lot={lot}")

    print(f"\n=== Pass criteria ===")
    pass_qty_inc = mo_after["qty_producing"] > mo_before["qty_producing"]
    pass_pick = pick_after is not None and pick_after["state"] == "done"
    print(f"  qty_producing incremented:  {'PASS' if pass_qty_inc else 'FAIL'}")
    print(f"  Partial-ship state=done:    {'PASS' if pass_pick else 'FAIL'}")

    pass_resin = True
    for pid, exp_qty in EXPECTED_QTY.items():
        lines = by_pid.get(pid, [])
        if not lines:
            print(f"  [{pid}] {EXPECTED_LOTS[pid][0]:<22}: MISSING")
            pass_resin = False
            continue
        actual = sum(ln["quantity"] for ln in lines)
        lot_names = {ln["lot_id"][1] for ln in lines if ln["lot_id"]}
        exp_lot = EXPECTED_LOTS[pid][1]
        qty_ok = abs(actual - exp_qty) <= TOL
        lot_ok = exp_lot in lot_names
        ok = qty_ok and lot_ok
        if not ok:
            pass_resin = False
        status_str = "PASS" if ok else (f"qty {actual:.4f} vs exp {exp_qty:.4f}  lots={lot_names}")
        print(f"  [{pid}] {EXPECTED_LOTS[pid][0]:<22} qty {actual:>8.4f} (exp {exp_qty:.2f})  lot match={'Y' if lot_ok else 'N'}  -> {status_str}")

    pass_pkg = (50 in by_pid and 52 in by_pid)
    print(f"  BOX (50) + Label (52) consumed: {'PASS' if pass_pkg else 'FAIL'}")

    overall = pass_qty_inc and pass_pick and pass_resin and pass_pkg
    print(f"\n[overall] {'PASS' if overall else 'FAIL'}")
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
