"""End-to-end test for sync_pallet_to_odoo.

  1. Probes staging Odoo for a (lot, picking) with an unassigned FG move_line
  2. Inserts a synthetic Pallet + MasterRoll on the test MES tied to that MO
  3. POSTs /api/v1/production/pallet/finalize
  4. Polls sync_queue until the job goes to 'done' (or fails)
  5. Verifies Odoo: stock.package created with msp_* fields, and the
     target move_line now has result_package_id set
"""
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xmlrpc.client
from pathlib import Path


def _load_dotenv():
    p = Path(__file__).parent.parent / ".env"
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
_load_dotenv()

URL = os.environ["ODOO_STAGING_URL"]; DB = os.environ["ODOO_STAGING_DB"]
USER = os.environ.get("ODOO_STAGING_USER", "admin@mountainstatesplastics.com")
KEY = os.environ["ODOO_STAGING_API_KEY"]
MES_URL = os.environ.get("MES_TEST_URL", "https://34.57.35.195.nip.io")
MES_KEY = os.environ["MES_TEST_API_KEY"]


def odoo_call(model, method, args, kw=None):
    ctx = ssl.create_default_context()
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", context=ctx, allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", context=ctx, allow_none=True)
    return models.execute_kw(DB, uid, KEY, model, method, args, kw or {})


import base64

def gcloud_ssh(cmd):
    """Run a command on the test VM, returning stdout. cmd is base64-encoded
    on the way over so Windows shell quoting can't mangle it."""
    b64 = base64.b64encode(cmd.encode()).decode()
    full = (
        f'gcloud compute ssh mes-testing --zone=us-central1-a '
        f'--command="echo {b64} | base64 -d | bash"'
    )
    r = subprocess.run(full, capture_output=True, text=True, timeout=60, shell=True)
    if r.returncode:
        print(f"  SSH error: {r.stderr}", file=sys.stderr)
    return r.stdout.strip()


def sqlite_exec(sql):
    """Run write SQL on the VM (no result expected)."""
    cmd = (
        f"sudo -u anthony python3 -c \""
        f"import sqlite3, sys; "
        f"c = sqlite3.connect('/opt/mes/data/mes_data.db'); "
        f"c.executescript(sys.stdin.read()); "
        f"c.commit(); c.close()"
        f"\" <<'__SQL__'\n{sql}\n__SQL__"
    )
    return gcloud_ssh(cmd)


def sqlite_query(sql):
    """Run SELECT and return list of rows (each row is a list of fields)."""
    cmd = (
        f"sudo -u anthony python3 -c \""
        f"import sqlite3, sys, json; "
        f"c = sqlite3.connect('/opt/mes/data/mes_data.db'); "
        f"rows = c.execute(sys.stdin.read()).fetchall(); "
        f"print(json.dumps(rows))"
        f"\" <<'__SQL__'\n{sql}\n__SQL__"
    )
    out = gcloud_ssh(cmd)
    return json.loads(out) if out else []


def mes_post(path, body):
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        f"{MES_URL}{path}", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-API-KEY": MES_KEY})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


# 1. probe Odoo
print("=== 1. probe Odoo for an unassigned FG move_line on an open delivery ===")
mls = odoo_call("stock.move.line", "search_read",
    [[["result_package_id", "=", False],
      ["state", "in", ["partially_available", "assigned", "confirmed"]],
      ["picking_id.state", "!=", "done"],
      ["picking_id.picking_type_id.code", "=", "outgoing"],
      ["lot_id", "!=", False]]],
    {"fields": ["id", "picking_id", "product_id", "lot_id", "quantity"], "limit": 1})
if not mls:
    sys.exit("no unassigned FG move_lines on open delivery pickings — nothing to test against")
ml = mls[0]
ml_id = ml["id"]
lot_name = ml["lot_id"][1]
picking_name = ml["picking_id"][1]
fg_qty = ml["quantity"]
print(f"  target: ml_id={ml_id} lot={lot_name} picking={picking_name} qty={fg_qty}")

# Find the MO that produces this lot
lot_recs = odoo_call("stock.lot", "search_read",
    [[["name", "=", lot_name]]], {"fields": ["id"], "limit": 1})
mo_recs = odoo_call("mrp.production", "search_read",
    [[["lot_producing_ids", "in", [lot_recs[0]["id"]]]]],
    {"fields": ["name"], "limit": 1})
wo_number = mo_recs[0]["name"]
print(f"  MO: {wo_number}")

# 2. Insert synthetic Pallet + MasterRoll on test MES
ts = int(time.time())
pallet_id = f"{wo_number}-PAL-E2E-{ts}"
roll_id = f"{wo_number}-PAL-E2E-{ts}-ROLL-1"
print(f"\n=== 2. insert MES Pallet + MasterRoll ===")
print(f"  pallet_id={pallet_id}")
print(f"  roll_id={roll_id}")

# pallet_id is the PK; wo_number in MES schema; rolls are separate rows in master_rolls
sqlite_exec(
    f"INSERT INTO pallets(pallet_id, wo_number, dim_length, dim_width, dim_height, created_at) "
    f"VALUES('{pallet_id}', '{wo_number}', 40, 48, 52, datetime('now'));"
)
sqlite_exec(
    f"INSERT INTO master_rolls(roll_id, wo_number, weight_lbs, length_ft, pallet_id, unit_number, created_at) "
    f"VALUES('{roll_id}', '{wo_number}', {fg_qty * 25}, 0, '{pallet_id}', 1, datetime('now'));"
)

# 3. POST finalize
print(f"\n=== 3. POST /api/v1/production/pallet/finalize ===")
gross_lb = round(fg_qty * 25 + 50, 1)  # rolls weight + 50 lb tare
status, body = mes_post("/api/v1/production/pallet/finalize",
                        {"pallet_id": pallet_id, "gross_weight_lb": gross_lb})
print(f"  status={status}  success={body.get('success')}  weight={gross_lb} lb")
if status != 200:
    print(f"  error: {body}")
    sys.exit(1)

# 4. Poll sync_queue until terminal state
print(f"\n=== 4. poll sync_queue for the new pallet/finalize job ===")
deadline = time.time() + 180
last_state = None
final_status = "?"
while time.time() < deadline:
    rows = sqlite_query(
        f"SELECT id, status, retries, substr(coalesce(last_error,''),1,120) "
        f"FROM sync_queue "
        f"WHERE endpoint='pallet/finalize' "
        f"  AND payload LIKE '%{pallet_id}%' "
        f"ORDER BY id DESC LIMIT 1;"
    )
    if rows:
        sync_id, st, retries, err = rows[0]
        if (st, retries) != last_state:
            elapsed = int(time.time() - (deadline - 180))
            print(f"  [{elapsed:>3}s] id={sync_id} status={st} retries={retries} {(err or '')[:80]}")
            last_state = (st, retries)
        final_status = st
        if st in ("done", "permanently_failed"):
            break
    time.sleep(5)

# 5. Verify Odoo state
print(f"\n=== 5. verify Odoo state ===")
pkgs = odoo_call("stock.package", "search_read",
    [[["name", "=", pallet_id]]],
    {"fields": ["id", "name", "msp_gross_weight_lb", "msp_dimensions_display",
                "msp_unit_numbers_summary", "msp_finalized_at",
                "msp_mo_ids", "msp_lot_ids"]})
if not pkgs:
    print(f"  FAIL: no stock.package created for {pallet_id}")
    sys.exit(1)
pkg = pkgs[0]
print(f"  stock.package id={pkg['id']}")
for k in ("msp_gross_weight_lb", "msp_dimensions_display", "msp_unit_numbers_summary",
          "msp_finalized_at", "msp_mo_ids", "msp_lot_ids"):
    print(f"    {k:<30} = {pkg[k]}")

ml_after = odoo_call("stock.move.line", "read", [[ml_id]],
    {"fields": ["result_package_id", "state"]})
print(f"  target move_line {ml_id}: result_package_id={ml_after[0]['result_package_id']} state={ml_after[0]['state']}")

# pass criteria
ok = (
    final_status == "done"
    and pkg["msp_gross_weight_lb"] == gross_lb
    and ml_after[0]["result_package_id"]
    and ml_after[0]["result_package_id"][0] == pkg["id"]
)
print(f"\n[result] {'PASS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
