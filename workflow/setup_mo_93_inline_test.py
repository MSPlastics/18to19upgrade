"""
Setup for the inline single-step lot-tracking test on MO 93 / WH/MO/00094
(`In-line extrusion` on Line 6 6" Davis, work_center_id=5).

Materials in the BOM:
  45  Butene1-BF            (already set up by setup_mo_1583_lot_test.py)
   3  Frac1-A               (already set up)
  40  conANTIBLOCK clarity  (already set up)
  42  conSLIP slow          (NEW — needs lot tracking + test lot + stock)
  50  #2 BOX 17x11x4        (no lot — has stock)
  52  4x6 Label             (no lot — has stock)

We also seed a stock.lot + stock for product 579 (con-Antiblock/slip) and
load a SILO with it. That product is what the MES hoppers JSON references
(blend recipe data drift); it would otherwise hit the FIX_DATA emergency
lot path. Note: the blend-vs-BOM drift is an Odoo data issue worth fixing
separately — it makes record_roll think 2% goes to a single legacy
product but the BOM splits that into the two clarity/slow additives.

Idempotent. Reads ODOO_STAGING_* + MES_TEST_* from .env.
"""
import json
import os
import ssl
import sys
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

MES_URL = os.environ.get("MES_TEST_URL", "https://34.67.173.228.nip.io")
MES_KEY = os.environ["MES_TEST_API_KEY"]

LOT_PREFIX = "TEST-2026-05-09"

# New lot-tracked materials this MO needs (others already done by setup_mo_1583_lot_test.py)
NEW_LOT_MATERIALS = [
    (42,  "conSLIP slow",         "line",   2000.0),
    (579, "con-Antiblock-slip",   "silo",   2000.0),  # legacy combined additive ref'd by hoppers_json
]

WC_LINE_6_ID = 5  # work_center_id


def odoo():
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    return uid, xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)


def call(models, uid, model, method, args, kw=None):
    return models.execute_kw(DB, uid, KEY, model, method, args, kw or {})


def call_void(models, uid, model, method, args, kw=None):
    try:
        return models.execute_kw(DB, uid, KEY, model, method, args, kw or {})
    except xmlrpc.client.Fault as e:
        if "cannot marshal None" in str(e.faultString):
            return None
        raise


def ensure_lot_tracked(models, uid, pid, name):
    p = call(models, uid, "product.product", "read", [[pid]],
             {"fields": ["tracking", "qty_available", "product_tmpl_id"]})[0]
    if p["tracking"] == "lot":
        return
    print(f"  [{pid}] {name}: tracking={p['tracking']} qty={p['qty_available']:.2f} -> changing to 'lot'")
    if p["qty_available"] != 0:
        quants = call(models, uid, "stock.quant", "search",
                      [[["product_id", "=", pid], ["location_id", "=", 8]]], {})
        if quants:
            for qid in quants:
                try:
                    call(models, uid, "stock.quant", "write", [[qid], {"inventory_quantity": 0}])
                except Exception:
                    pass
            call_void(models, uid, "stock.quant", "action_apply_inventory", [quants])
    call(models, uid, "product.template", "write", [[p["product_tmpl_id"][0]], {"tracking": "lot"}])


def ensure_lot(models, uid, pid, lot_name):
    existing = call(models, uid, "stock.lot", "search",
                    [[["product_id", "=", pid], ["name", "=", lot_name]]], {"limit": 1})
    if existing:
        return existing[0]
    return call(models, uid, "stock.lot", "create",
                [{"product_id": pid, "name": lot_name}])


def ensure_stock(models, uid, pid, lot_id, qty, name):
    quants = call(models, uid, "stock.quant", "search_read",
                  [[["product_id", "=", pid], ["location_id", "=", 8],
                    ["lot_id", "=", lot_id]]],
                  {"fields": ["id", "quantity"]})
    current = sum(q["quantity"] for q in quants)
    if current >= qty:
        print(f"  [stock] {name}: already {current:.2f} >= target {qty:.2f}")
        return
    if quants:
        qid = quants[0]["id"]
        call(models, uid, "stock.quant", "write", [[qid], {"inventory_quantity": qty}])
        call_void(models, uid, "stock.quant", "action_apply_inventory", [[qid]])
    else:
        new_q = models.execute_kw(DB, uid, KEY, "stock.quant", "create",
                                  [{"product_id": pid, "location_id": 8,
                                    "lot_id": lot_id, "inventory_quantity": qty}],
                                  {"context": {"inventory_mode": True}})
        call_void(models, uid, "stock.quant", "action_apply_inventory", [[new_q]])
    print(f"  [stock] {name}: bumped to {qty:.2f}")


# MES helpers
def mes(method, path, payload=None):
    url = f"{MES_URL}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "X-API-KEY": MES_KEY})
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main():
    uid, models = odoo()
    print("=== Phase 1: Odoo lot tracking + lot + stock ===")
    new_lots = {}
    for pid, name, role, qty in NEW_LOT_MATERIALS:
        print(f"\n[{pid}] {name} ({role})")
        ensure_lot_tracked(models, uid, pid, name)
        slug = name.replace(" ", "-")
        lot_name = f"{LOT_PREFIX}-{slug}-001"
        lot_id = ensure_lot(models, uid, pid, lot_name)
        print(f"  [lot] '{lot_name}' (id={lot_id})")
        ensure_stock(models, uid, pid, lot_id, qty, name)
        new_lots[pid] = lot_name

    # Phase 2: MES silos for line 6 (silos are shared but we'll add con-Antiblock-slip)
    print("\n=== Phase 2: MES silo for legacy combined additive ===")
    silos = mes("GET", "/api/resin/silos")[1]
    by_name = {s["name"]: s for s in (silos if isinstance(silos, list) else [])}
    if "SILO-CONANTI-SLIP-579" not in by_name:
        s, b = mes("POST", "/api/resin/silos/add",
                   {"name": "SILO-CONANTI-SLIP-579", "capacity": 50000.0, "location": "Inside"})
        print(f"  add silo: {s} {b}")
        silos = mes("GET", "/api/resin/silos")[1]
        by_name = {s["name"]: s for s in silos}
    silo = by_name["SILO-CONANTI-SLIP-579"]
    s, b = mes("POST", "/api/resin/silos/update",
               {"silo_id": silo["id"], "material_name": "con-Antiblock/slip",
                "lot_number": new_lots[579], "quantity": 1500.0})
    print(f"  update silo: {s}")

    # Phase 3: MES line_inventory for Line 6 (wc_id=5) — add conSLIP slow lot
    print(f"\n=== Phase 3: MES line_inventory for Line 6 (wc {WC_LINE_6_ID}) ===")
    inv = mes("GET", f"/api/resin/inventory?work_center_id={WC_LINE_6_ID}")[1]
    inv_by_mat = {i["material_name"]: i for i in (inv if isinstance(inv, list) else [])}
    line_specs = [
        (42,  "conSLIP slow"),
        (40,  "conANTIBLOCK clarity"),  # also load on line 6 (already exists for line 1)
    ]
    for pid, mat_name in line_specs:
        # The existing line_inventory key is per (wc, material_name); this checks if for THIS line
        if mat_name in inv_by_mat and inv_by_mat[mat_name].get("lot_number"):
            print(f"  {mat_name} already loaded on line 6 (lot={inv_by_mat[mat_name]['lot_number']})")
            continue
        # For pid 40 we already have a lot from MO 1583's setup
        if pid == 40:
            slug = "conANTIBLOCK clarity".replace(" ", "-")
            lot_name = f"{LOT_PREFIX}-{slug}-001"
        else:
            lot_name = new_lots[pid]
        s, b = mes("POST", "/api/resin/inventory/add",
                   {"work_center_id": WC_LINE_6_ID, "material_name": mat_name,
                    "lot_number": lot_name, "quantity": 1500.0})
        print(f"  add {mat_name} lot={lot_name}: {s}")

    # Final summary
    print("\n=== state ===")
    silos = mes("GET", "/api/resin/silos")[1]
    inv = mes("GET", f"/api/resin/inventory?work_center_id={WC_LINE_6_ID}")[1]
    print("MES silos:")
    for s in silos:
        print(f"  {s['name']:<26} mat={s.get('material_name','-'):<26} lot={s.get('lot_number','-'):<42} qty={s.get('quantity',0)}")
    print(f"\nMES line_inventory for Line 6 (wc={WC_LINE_6_ID}):")
    for i in inv:
        print(f"  {i.get('material_name','-'):<26} lot={i.get('lot_number','-'):<42} qty={i.get('quantity',0)}")
    print("\n[done] ready for inline test on MO 93 / WH/MO/00094")


if __name__ == "__main__":
    main()
