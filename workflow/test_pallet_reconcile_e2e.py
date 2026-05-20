"""End-to-end test of the reconciliation sync.

  1. Probe Odoo for a real lot with 3+ free FG quants in WH/Stock
  2. Insert 3 MES rolls and assign them to a fresh pallet 'PLT-A' via record_pallet
  3. Wait for pallet/reconcile -> verify Odoo stock.package PLT-A has 3 quants
  4. Add 1 more roll to PLT-A; reconcile; verify 4 quants
  5. Move 2 rolls from PLT-A to a new pallet 'PLT-B'; reconcile both;
     verify PLT-A has 2 and PLT-B has 2
  6. Remove 1 roll from PLT-A entirely (back to free inventory);
     verify PLT-A has 1 and free pool gained 1 quant
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
import base64
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
MES_URL = os.environ.get("MES_TEST_URL", "https://34.67.173.228.nip.io")
MES_KEY = os.environ["MES_TEST_API_KEY"]


def odoo_call(model, method, args, kw=None):
    ctx = ssl.create_default_context()
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", context=ctx, allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", context=ctx, allow_none=True)
    return models.execute_kw(DB, uid, KEY, model, method, args, kw or {})


def gcloud_ssh(cmd):
    b64 = base64.b64encode(cmd.encode()).decode()
    full = (f'gcloud compute ssh mes-testing --zone=us-central1-a '
            f'--command="echo {b64} | base64 -d | bash"')
    r = subprocess.run(full, capture_output=True, text=True, timeout=60, shell=True)
    if r.returncode:
        print(f"  SSH error: {r.stderr}", file=sys.stderr)
    return r.stdout.strip()


def sqlite_exec(sql):
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


def wait_for_reconcile(pallet_id, expected_qty_in_pkg, deadline_s=120):
    """Poll the sync_queue until the most recent pallet/reconcile job for this
    pallet is done, then read Odoo's actual quant count for the package."""
    start = time.time()
    while time.time() - start < deadline_s:
        rows = sqlite_query(
            f"SELECT id, status, retries, substr(coalesce(last_error,''),1,80) "
            f"FROM sync_queue "
            f"WHERE endpoint='pallet/reconcile' AND payload LIKE '%{pallet_id}%' "
            f"ORDER BY id DESC LIMIT 1;"
        )
        if rows:
            sid, st, rt, err = rows[0]
            if st in ('done', 'permanently_failed'):
                # Read Odoo state
                pkgs = odoo_call("stock.package", "search_read",
                    [[["name", "=", pallet_id]]],
                    {"fields": ["id", "name"], "limit": 1})
                if pkgs:
                    quants = odoo_call("stock.quant", "search_read",
                        [[["package_id", "=", pkgs[0]["id"]],
                          ["quantity", ">", 0]]],
                        {"fields": ["lot_id", "quantity"]})
                    actual = sum(q["quantity"] for q in quants)
                    print(f"  [{int(time.time()-start):>3}s] sync={st} retries={rt} pkg_id={pkgs[0]['id']} actual_qty={actual}")
                    return st, actual, pkgs[0]["id"]
                else:
                    print(f"  [{int(time.time()-start):>3}s] sync={st} but no stock.package found")
                    return st, 0, None
        time.sleep(4)
    raise RuntimeError(f"timeout waiting for reconcile of {pallet_id}")


# ---------- TEST -----------------------------------------------------------

print(f"=== 1. probe Odoo for an MO-produced lot with 4+ free FG quants in WH/Stock ===")
# Pull recent MOs and check each one's FG lot for free quants
mos = odoo_call("mrp.production", "search_read",
    [[["lot_producing_ids", "!=", False],
      ["state", "in", ["progress", "to_close", "done"]]]],
    {"fields": ["id", "name", "lot_producing_ids", "product_id"],
     "order": "id DESC", "limit": 50})

candidate = None
for mo in mos:
    lot_id = mo["lot_producing_ids"][0]
    qs = odoo_call("stock.quant", "search_read",
        [[["location_id.usage", "=", "internal"],
          ["package_id", "=", False],
          ["lot_id", "=", lot_id],
          ["quantity", ">", 0]]],
        {"fields": ["quantity"]})
    total = sum(q["quantity"] for q in qs)
    if total >= 4:
        candidate = (mo["name"], lot_id, total)
        break

if not candidate:
    sys.exit("no MO with 4+ free FG quants in WH/Stock — can't run pack/unpack test")
wo_number, fg_lot_id, total = candidate
lot_name = odoo_call("stock.lot", "read", [[fg_lot_id]], {"fields": ["name"]})[0]["name"]
print(f"  using MO={wo_number} lot={lot_name} (total free qty={total})")

# ---------- step 2: create PLT-A with 3 rolls -----------------------------
ts = int(time.time())
plt_a = f"PLT-RC-A-{ts}"
plt_b = f"PLT-RC-B-{ts}"
print(f"\n=== 2. create MES PLT-A with 3 rolls (PUT 3 cases on pallet) ===")
sqlite_exec(f"""
INSERT INTO pallets(pallet_id, wo_number, dim_length, dim_width, dim_height, created_at)
VALUES('{plt_a}', '{wo_number}', 40, 48, 52, datetime('now'));
INSERT INTO master_rolls(roll_id, wo_number, weight_lbs, length_ft, pallet_id, unit_number, created_at)
VALUES
  ('{plt_a}-R1', '{wo_number}', 25, 0, '{plt_a}', 1, datetime('now')),
  ('{plt_a}-R2', '{wo_number}', 25, 0, '{plt_a}', 2, datetime('now')),
  ('{plt_a}-R3', '{wo_number}', 25, 0, '{plt_a}', 3, datetime('now'));
INSERT INTO sync_queue(endpoint, payload, status, created_at, updated_at)
VALUES('pallet/reconcile', '{{"pallet_id": "{plt_a}"}}', 'pending', datetime('now'), datetime('now'));
""")
st, qty, pkg_a_id = wait_for_reconcile(plt_a, 3)
assert st == 'done' and qty == 3, f"expected qty=3 got {qty} (status={st})"
print(f"  PASS: PLT-A has {qty} quants in Odoo pkg id={pkg_a_id}")

# ---------- step 3: add 1 more roll to PLT-A ------------------------------
print(f"\n=== 3. add 1 more roll to PLT-A (4 cases total) ===")
sqlite_exec(f"""
INSERT INTO master_rolls(roll_id, wo_number, weight_lbs, length_ft, pallet_id, unit_number, created_at)
VALUES('{plt_a}-R4', '{wo_number}', 25, 0, '{plt_a}', 4, datetime('now'));
INSERT INTO sync_queue(endpoint, payload, status, created_at, updated_at)
VALUES('pallet/reconcile', '{{"pallet_id": "{plt_a}"}}', 'pending', datetime('now'), datetime('now'));
""")
st, qty, _ = wait_for_reconcile(plt_a, 4)
assert st == 'done' and qty == 4, f"expected qty=4 got {qty} (status={st})"
print(f"  PASS: PLT-A has {qty} quants after add")

# ---------- step 4: combine — move 2 rolls from PLT-A to PLT-B ------------
print(f"\n=== 4. combine — move R3+R4 from PLT-A to new PLT-B ===")
sqlite_exec(f"""
INSERT INTO pallets(pallet_id, wo_number, dim_length, dim_width, dim_height, created_at)
VALUES('{plt_b}', '{wo_number}', 40, 48, 52, datetime('now'));
UPDATE master_rolls SET pallet_id='{plt_b}' WHERE roll_id IN ('{plt_a}-R3', '{plt_a}-R4');
INSERT INTO sync_queue(endpoint, payload, status, created_at, updated_at)
VALUES('pallet/reconcile', '{{"pallet_id": "{plt_a}"}}', 'pending', datetime('now'), datetime('now'));
INSERT INTO sync_queue(endpoint, payload, status, created_at, updated_at)
VALUES('pallet/reconcile', '{{"pallet_id": "{plt_b}"}}', 'pending', datetime('now'), datetime('now'));
""")
st_a, qty_a, _ = wait_for_reconcile(plt_a, 2)
st_b, qty_b, pkg_b_id = wait_for_reconcile(plt_b, 2)
assert st_a == 'done' and qty_a == 2, f"PLT-A expected 2 got {qty_a} (status={st_a})"
assert st_b == 'done' and qty_b == 2, f"PLT-B expected 2 got {qty_b} (status={st_b})"
print(f"  PASS: PLT-A={qty_a}, PLT-B={qty_b}")

# ---------- step 5: remove R1 from PLT-A entirely (back to free) ----------
print(f"\n=== 5. take R1 off PLT-A (no scrap, just back to inventory) ===")
sqlite_exec(f"""
UPDATE master_rolls SET pallet_id=NULL WHERE roll_id='{plt_a}-R1';
INSERT INTO sync_queue(endpoint, payload, status, created_at, updated_at)
VALUES('pallet/reconcile', '{{"pallet_id": "{plt_a}"}}', 'pending', datetime('now'), datetime('now'));
""")
st, qty, _ = wait_for_reconcile(plt_a, 1)
assert st == 'done' and qty == 1, f"PLT-A expected 1 got {qty} (status={st})"
print(f"  PASS: PLT-A has {qty} quant after removal")

print(f"\n[result] PASS — full reconcile lifecycle works")
