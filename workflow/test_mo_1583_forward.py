"""
Forward test for MO 1583 / WH/MO/01479: submit a 100 lb roll to the cloud
MES the same way operatorUI does, wait for the sync queue to push to Odoo,
then verify every expected stock.move.line on the MO's raw moves matches
the lots we seeded via setup_mo_1583_lot_test.py.

Reads ODOO_STAGING_* and uses the cloud test MES at 35.194.23.98.

Idempotent enough — each run uses a fresh roll_id so re-runs accumulate
test data on staging (which is fine).
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

MES_URL = os.environ.get("MES_TEST_URL", "https://35.194.23.98.nip.io")
MES_KEY = os.environ.get("MES_TEST_API_KEY")
if not MES_KEY:
    sys.exit("Missing MES_TEST_API_KEY env var (set in 18to19upgrade/.env)")

MO_ID = 1583
WO_NUMBER = "WH/MO/01479"
WO_ID_5_LAYER = 2461   # MR step on 5 Layer
WEIGHT_LBS = 100.0

# Aggregated expected raw consumption (calculated from blend ratios in
# previous review). Each entry: (product_id, friendly_name, expected_qty)
EXPECTED = [
    (45,  "Butene1-BF",            50.40),  # 9.8 + 15 + 17 + 8.6
    (3,   "Frac1-A",                6.00),  #  3 + 3
    (372, "Color Repro",           38.00),
    (99,  "Exceed 1012RA",          1.00),
    (40,  "conANTIBLOCK clarity",   0.60),  # 0.2 + 0.4
    (451, "con-brown1",             2.00),
    (43,  "conSLIP fast",           2.00),
]
EXPECTED_LOTS = {
    45:  "TEST-2026-05-09-Butene1-BF-001",
    3:   "TEST-2026-05-09-Frac1-A-001",
    372: "TEST-2026-05-09-Color-Repro-001",
    99:  "TEST-2026-05-09-Exceed-1012RA-001",
    40:  "TEST-2026-05-09-conANTIBLOCK-clarity-001",
    451: "TEST-2026-05-09-con-brown1-001",
    43:  "TEST-2026-05-09-conSLIP-fast-001",
}
TOLERANCE = 0.01  # lb


def odoo_connect():
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    if not uid:
        sys.exit("Odoo authentication failed")
    return uid, xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)


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


def read_raw_move_lines(uid, models):
    """Pull every stock.move.line attached to MO 1583's move_raw_ids.
    Returns rows including move_line id so callers can do id-based diffs
    (avoids false-duplicate matches when re-runs produce identical qty)."""
    mo = models.execute_kw(DB, uid, KEY, "mrp.production", "read", [[MO_ID]],
                           {"fields": ["move_raw_ids"]})[0]
    moves = models.execute_kw(DB, uid, KEY, "stock.move", "read", [mo["move_raw_ids"]],
                              {"fields": ["product_id", "move_line_ids", "product_uom_qty"]})
    rows = []
    for mv in moves:
        if not mv["move_line_ids"]:
            continue
        lines = models.execute_kw(DB, uid, KEY, "stock.move.line", "read", [mv["move_line_ids"]],
                                  {"fields": ["product_id", "lot_id", "quantity", "state"]})
        for ln in lines:
            rows.append({
                "id": ln["id"],
                "move_id": mv["id"],
                "product_id": ln["product_id"][0],
                "product_name": ln["product_id"][1],
                "lot_id": ln["lot_id"][0] if ln["lot_id"] else None,
                "lot_name": ln["lot_id"][1] if ln["lot_id"] else None,
                "quantity": ln["quantity"],
                "state": ln["state"],
            })
    return rows


def main():
    uid, models = odoo_connect()

    # Snapshot before
    before = read_raw_move_lines(uid, models)
    before_ids = {ln["product_id"]: [] for ln in before}
    for ln in before:
        before_ids[ln["product_id"]].append(ln)
    print(f"[before] {len(before)} existing move_lines on MO {WO_NUMBER}'s raw moves")

    roll_id = f"{WO_NUMBER}-FWDTEST-{int(time.time())}"
    payload = {
        "wo_number": WO_NUMBER,
        "roll_id": roll_id,
        "weight_lbs": WEIGHT_LBS,
        "length_ft": 0,
        "width": "",
        "mil": "",
        "work_order_id": WO_ID_5_LAYER,
        "tracker_type": "lbs",
        "current_step_seq": 1,
    }
    print(f"\n[post] submitting roll {roll_id} ({WEIGHT_LBS} lb) -> MES {MES_URL}/api/v1/production/roll")
    status, body = mes_post_roll(payload)
    print(f"[post] HTTP {status}")
    print(f"[post] body: {json.dumps(body, indent=2)[:600]}")
    if status not in (200, 201):
        sys.exit(f"FAIL: MES rejected the roll")
    if isinstance(body, dict) and body.get("missing_lots"):
        print(f"[post] WARNING missing_lots reported: {body['missing_lots']}")

    # Poll Odoo until new move_lines appear (sync queue worker runs every ~5s)
    print("\n[wait] polling Odoo for new raw move_lines...")
    deadline = time.time() + 240
    new_lines = []
    while time.time() < deadline:
        after = read_raw_move_lines(uid, models)
        # New lines = any move_line.id that wasn't in the snapshot.
        before_line_ids = {ln["id"] for ln in before}
        new_lines = [ln for ln in after if ln["id"] not in before_line_ids]
        if len(new_lines) >= len(EXPECTED):
            print(f"[wait] +{int(deadline - time.time())}s found {len(new_lines)} new lines")
            break
        print(f"[wait] +{int(time.time() - (deadline - 90))}s have {len(new_lines)} new lines, expecting {len(EXPECTED)}...")
        time.sleep(5)
    else:
        print(f"[wait] TIMEOUT — only {len(new_lines)} new lines found (expected {len(EXPECTED)})")

    # Verify each expected entry
    print("\n=== forward-test verification ===")
    print(f"{'Material':<22} | {'Expected qty':>12} | {'Actual qty':>10} | {'Lot match':<12} | Status")
    print("-" * 90)
    by_pid_actual = {}
    for ln in new_lines:
        by_pid_actual.setdefault(ln["product_id"], []).append(ln)

    overall_pass = True
    for pid, friendly, exp_qty in EXPECTED:
        lines_for_pid = by_pid_actual.get(pid, [])
        if not lines_for_pid:
            print(f"{friendly:<22} | {exp_qty:>12.2f} | {'(missing)':>10} | {'-':<12} | FAIL")
            overall_pass = False
            continue
        actual_qty = sum(ln["quantity"] for ln in lines_for_pid)
        # Aggregated by material — actual_qty should ≈ exp_qty
        lot_names = {ln["lot_name"] for ln in lines_for_pid if ln["lot_name"]}
        expected_lot = EXPECTED_LOTS.get(pid)
        lot_ok = expected_lot in lot_names
        qty_ok = abs(actual_qty - exp_qty) <= TOLERANCE
        status_str = "PASS" if (qty_ok and lot_ok) else ("QTY MISMATCH" if not qty_ok else "LOT MISMATCH")
        if not (qty_ok and lot_ok):
            overall_pass = False
        print(f"{friendly:<22} | {exp_qty:>12.2f} | {actual_qty:>10.4f} | {('Y' if lot_ok else 'N'):<12} | {status_str}")
        if not lot_ok:
            print(f"  -> got lots: {lot_names}, expected: {expected_lot}")

    print("\n=== Detail of new move_lines ===")
    for ln in new_lines:
        print(f"  product[{ln['product_id']:>4}] {ln['product_name'][:40]:<40} qty={ln['quantity']:>8.4f} lot={ln['lot_name']}")

    print(f"\n[result] {'PASS' if overall_pass else 'FAIL'}")
    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
