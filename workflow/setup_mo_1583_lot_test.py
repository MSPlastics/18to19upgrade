"""
Set up the staging environment for the MO 1583 (WH/MO/01479) end-to-end
lot-tracking test:

  1. For each of the 7 raw materials that should be lot-tracked
     (Butene1-BF, Frac1-A, Exceed 1012RA, Color Repro, conANTIBLOCK clarity,
     con-brown1, conSLIP fast), enable lot tracking on the product if not
     already set, create a test stock.lot, and ensure positive stock at
     WH/Stock with that lot.
  2. For the packaging items (#7 BOX, 4x6 Label), leave tracking='none' and
     ensure positive stock so the move can be reserved.
  3. Configure MES silos (Butene1-BF, Frac1-A, Color Repro, Exceed 1012RA)
     with the lots we just created.
  4. Configure MES line_inventory on the 5 Layer line (wc id=1) for the
     three line-loaded additives (conANTIBLOCK clarity, con-brown1,
     conSLIP fast).

Idempotent — safe to re-run. Reads ODOO_STAGING_* and MES creds from
environment / .env.

Authorized for staging only.
"""
import os
import sys
import ssl
import json
import xmlrpc.client
from pathlib import Path
import urllib.request
import urllib.error


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


# Odoo connection
URL = os.environ.get("ODOO_STAGING_URL")
DB = os.environ.get("ODOO_STAGING_DB")
USER = os.environ.get("ODOO_STAGING_USER", "admin@mountainstatesplastics.com")
KEY = os.environ.get("ODOO_STAGING_API_KEY")
if not all([URL, DB, KEY]):
    sys.exit("Missing ODOO_STAGING_* env vars (set in 18to19upgrade/.env)")

# MES connection (cloud test MES)
MES_URL = os.environ.get("MES_TEST_URL", "https://34.57.35.195.nip.io")
MES_KEY = os.environ.get("MES_TEST_API_KEY")
if not MES_KEY:
    sys.exit("Missing MES_TEST_API_KEY env var (set in 18to19upgrade/.env)")

# Test naming convention
LOT_PREFIX = f"TEST-2026-05-09"

# Materials needing lot tracking + stock seed
LOT_MATERIALS = [
    # (product_id, friendly_name, silo_or_line, target_qty_lb)
    (45,  "Butene1-BF",            "silo",   10000.0),
    (3,   "Frac1-A",               "silo",   10000.0),
    (372, "Color Repro",           "silo",   5000.0),
    (99,  "Exceed 1012RA",         "silo",   5000.0),
    (40,  "conANTIBLOCK clarity",  "line",   2000.0),
    (451, "con-brown1",            "line",   2000.0),
    (43,  "conSLIP fast",          "line",   2000.0),
]

# Packaging items — no lot, just stock
PACKAGING_MATERIALS = [
    (107, "#7 BOX 24 1/4 x 14 x 4", 5000.0),
    (52,  "4x6 Label",              5000.0),
]

WC_5_LAYER_ID = 1     # MES work_center_id
WH_STOCK_LOC_ID = 8   # Odoo internal location id


def odoo_connect():
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    if not uid:
        sys.exit("Odoo authentication failed")
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)
    return uid, models


def call(models, uid, model, method, args, kw=None):
    return models.execute_kw(DB, uid, KEY, model, method, args, kw or {})


def call_void(models, uid, model, method, args, kw=None):
    """Like call() but tolerates the action returning None (XML-RPC marshaller
    server-side has allow_none=False; the action still ran)."""
    try:
        return models.execute_kw(DB, uid, KEY, model, method, args, kw or {})
    except xmlrpc.client.Fault as e:
        if "cannot marshal None" in str(e.faultString):
            return None
        raise


def ensure_tracking_lot(models, uid, pid, name):
    """Set tracking='lot' on the product variant. If it already is, no-op."""
    p = call(models, uid, 'product.product', 'read', [[pid]],
             {'fields': ['tracking', 'qty_available', 'product_tmpl_id']})[0]
    if p['tracking'] == 'lot':
        return False  # already
    print(f"  [tracking] {name}: was '{p['tracking']}', has {p['qty_available']:.2f} on-hand")

    # If existing stock without lot, zero it first so the conversion is clean.
    if p['qty_available'] != 0:
        print(f"  [tracking] zeroing existing untracked stock first ({p['qty_available']:.2f})")
        # Find existing quants (no lot)
        quant_ids = call(models, uid, 'stock.quant', 'search',
                         [[['product_id', '=', pid], ['location_id', '=', WH_STOCK_LOC_ID]]],
                         {})
        if quant_ids:
            # Set inventory_quantity=0 and apply
            for qid in quant_ids:
                try:
                    call(models, uid, 'stock.quant', 'write', [[qid], {'inventory_quantity': 0}])
                except Exception as e:
                    print(f"    write fail on quant {qid}: {e}")
            call_void(models, uid, 'stock.quant', 'action_apply_inventory', [quant_ids])

    # Now switch tracking on the template (lot tracking is template-level)
    tmpl_id = p['product_tmpl_id'][0]
    call(models, uid, 'product.template', 'write', [[tmpl_id], {'tracking': 'lot'}])
    print(f"  [tracking] -> 'lot' on template {tmpl_id}")
    return True


def ensure_lot(models, uid, pid, lot_name):
    """Find or create a stock.lot for the product with the given name."""
    existing = call(models, uid, 'stock.lot', 'search',
                    [[['product_id', '=', pid], ['name', '=', lot_name]]], {'limit': 1})
    if existing:
        return existing[0]
    new_id = call(models, uid, 'stock.lot', 'create', [{
        'product_id': pid,
        'name': lot_name,
    }])
    return new_id


def ensure_stock(models, uid, pid, lot_id, qty, name, with_lot=True):
    """Make sure the (product, lot, location) quant has at least `qty` available."""
    domain = [['product_id', '=', pid], ['location_id', '=', WH_STOCK_LOC_ID]]
    if with_lot and lot_id:
        domain.append(['lot_id', '=', lot_id])
    else:
        domain.append(['lot_id', '=', False])
    quants = call(models, uid, 'stock.quant', 'search_read', [domain],
                  {'fields': ['id', 'quantity', 'inventory_quantity', 'lot_id']})
    current = sum(q['quantity'] for q in quants)
    if current >= qty:
        print(f"  [stock] {name}: already at {current:.2f} (target {qty:.2f}) - OK")
        return
    if quants:
        # Adjust the first matching quant to the target qty
        qid = quants[0]['id']
        call(models, uid, 'stock.quant', 'write', [[qid], {'inventory_quantity': qty}])
        call_void(models, uid, 'stock.quant', 'action_apply_inventory', [[qid]])
    else:
        # Create a fresh quant via inventory_mode
        vals = {
            'product_id': pid,
            'location_id': WH_STOCK_LOC_ID,
            'inventory_quantity': qty,
        }
        if with_lot and lot_id:
            vals['lot_id'] = lot_id
        new_q = models.execute_kw(DB, uid, KEY, 'stock.quant', 'create',
                                  [vals], {'context': {'inventory_mode': True}})
        call_void(models, uid, 'stock.quant', 'action_apply_inventory', [[new_q]])
    print(f"  [stock] {name}: bumped to {qty:.2f} (was {current:.2f})")


# ------------------- MES side -------------------

def mes_request(method, path, payload=None):
    url = f"{MES_URL}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "X-API-KEY": MES_KEY})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def mes_get_silos():
    status, body = mes_request("GET", "/api/resin/silos")
    return body if status == 200 else []


def mes_add_silo(name, capacity=100000.0, location="Outside"):
    return mes_request("POST", "/api/resin/silos/add",
                       {"name": name, "capacity": capacity, "location": location})


def mes_update_silo(silo_id, material_name, lot_number, quantity):
    return mes_request("POST", "/api/resin/silos/update",
                       {"silo_id": silo_id, "material_name": material_name,
                        "lot_number": lot_number, "quantity": quantity})


def mes_get_inventory(wc_id):
    status, body = mes_request("GET", f"/api/resin/inventory?work_center_id={wc_id}")
    return body if status == 200 else []


def mes_add_inventory(wc_id, material_name, lot_number, quantity=2000.0):
    return mes_request("POST", "/api/resin/inventory/add",
                       {"work_center_id": wc_id, "material_name": material_name,
                        "lot_number": lot_number, "quantity": quantity})


def main():
    uid, models = odoo_connect()
    print(f"[start] connected to staging (uid={uid})")

    # ===== Phase 1: Odoo lot+stock setup =====
    lot_map = {}  # pid -> (lot_name, lot_id)
    print("\n=== Phase 1: Odoo lot+stock setup ===")
    for pid, name, role, target_qty in LOT_MATERIALS:
        print(f"\n[mat {pid}] {name} ({role}, target {target_qty} lb)")
        ensure_tracking_lot(models, uid, pid, name)
        # Lot name pattern keeps test data tidy
        slug = name.replace(' ', '-')
        lot_name = f"{LOT_PREFIX}-{slug}-001"
        lot_id = ensure_lot(models, uid, pid, lot_name)
        print(f"  [lot] '{lot_name}' (id={lot_id})")
        ensure_stock(models, uid, pid, lot_id, target_qty, name, with_lot=True)
        lot_map[pid] = (lot_name, lot_id, name)

    print("\n=== Phase 1b: packaging stock (no lot) ===")
    for pid, name, target_qty in PACKAGING_MATERIALS:
        print(f"\n[pkg {pid}] {name} (target {target_qty})")
        ensure_stock(models, uid, pid, None, target_qty, name, with_lot=False)

    # ===== Phase 2: MES silos =====
    print("\n=== Phase 2: MES silos ===")
    silo_specs = [
        ("SILO-BUTENE",    45,  "Butene1-BF",      8000.0),
        ("SILO-FRAC",       3,  "Frac1-A",         8000.0),
        ("SILO-COLOR",    372,  "Color Repro",     4000.0),
        ("SILO-EXCEED",    99,  "Exceed 1012RA",   4000.0),
    ]
    existing_silos = mes_get_silos()
    existing_by_name = {s['name']: s for s in existing_silos}
    for silo_name, pid, mat_name, qty in silo_specs:
        if silo_name not in existing_by_name:
            status, body = mes_add_silo(silo_name)
            print(f"  [silo] add {silo_name}: {status} {body}")
            existing_silos = mes_get_silos()
            existing_by_name = {s['name']: s for s in existing_silos}
        silo = existing_by_name.get(silo_name)
        if not silo:
            print(f"  [silo] FAILED to create {silo_name}, skipping")
            continue
        lot_name = lot_map[pid][0]
        status, body = mes_update_silo(silo['id'], mat_name, lot_name, qty)
        print(f"  [silo] update {silo_name}: material={mat_name} lot={lot_name} qty={qty} -> {status}")

    # ===== Phase 3: MES line_inventory for 5 Layer line =====
    print(f"\n=== Phase 3: MES line_inventory on 5 Layer (wc {WC_5_LAYER_ID}) ===")
    line_specs = [
        (40,  "conANTIBLOCK clarity"),
        (451, "con-brown1"),
        (43,  "conSLIP fast"),
    ]
    existing_inv = mes_get_inventory(WC_5_LAYER_ID)
    existing_inv_by_mat = {i['material_name']: i for i in existing_inv}
    for pid, mat_name in line_specs:
        lot_name = lot_map[pid][0]
        if mat_name in existing_inv_by_mat and existing_inv_by_mat[mat_name].get('lot_number') == lot_name:
            print(f"  [line-inv] {mat_name} lot={lot_name} - already present, skipping")
            continue
        status, body = mes_add_inventory(WC_5_LAYER_ID, mat_name, lot_name, 1500.0)
        print(f"  [line-inv] add {mat_name} lot={lot_name} -> {status} {body}")

    # ===== Final summary =====
    print("\n=== summary ===")
    print("Test lots created on staging Odoo:")
    for pid, (lot_name, lot_id, friendly) in lot_map.items():
        print(f"  product {pid:>4} ({friendly:<22}) lot id={lot_id:<6} name={lot_name}")
    print("\nMES silos:")
    for s in mes_get_silos():
        print(f"  {s['name']:<14} | mat={s.get('material_name','-'):<22} | lot={s.get('lot_number','-'):<40} | qty={s.get('quantity'):>8.1f}")
    print(f"\nMES line_inventory (5 Layer, wc={WC_5_LAYER_ID}):")
    for i in mes_get_inventory(WC_5_LAYER_ID):
        print(f"  {i.get('material_name','-'):<22} | lot={i.get('lot_number','-'):<40} | qty={i.get('quantity'):>6.1f}")
    print("\n[done] ready for forward-test (submit a roll against MO 1583 / WH/MO/01479)")


if __name__ == "__main__":
    main()
