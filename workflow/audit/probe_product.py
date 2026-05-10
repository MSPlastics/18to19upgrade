"""Probe a product on staging to capture its full BOM/UoM/packaging shape (v19).

Usage:
  python workflow/audit/probe_product.py 1195
  python workflow/audit/probe_product.py 11158        (treated as ID first, then code, then name ilike)

Read-only: no creates/writes to Odoo.
"""
import os, ssl, sys, xmlrpc.client
from pathlib import Path

def _load_dotenv():
    p = Path(__file__).resolve().parent.parent.parent / ".env"
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
_load_dotenv()

URL = os.environ["ODOO_STAGING_URL"]; DB = os.environ["ODOO_STAGING_DB"]
USER = os.environ.get("ODOO_STAGING_USER", "admin@mountainstatesplastics.com")
KEY = os.environ["ODOO_STAGING_API_KEY"]

ctx = ssl.create_default_context()
common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", context=ctx, allow_none=True)
uid = common.authenticate(DB, USER, KEY, {})
m = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", context=ctx, allow_none=True)
def call(model, method, args, kw=None):
    return m.execute_kw(DB, uid, KEY, model, method, args, kw or {})

if len(sys.argv) < 2:
    sys.exit("usage: probe_product.py <id_or_code_or_name>")
ARG = sys.argv[1]

PROD_FIELDS = ["id","name","code","barcode","uom_id","uom_ids",
               "categ_id","tracking","type","sale_ok","route_ids",
               "packaging_ids","qty_available","virtual_available",
               "incoming_qty","outgoing_qty"]

prod = None
try:
    pid = int(ARG)
    res = call("product.product", "read", [[pid]], {"fields": PROD_FIELDS})
    if res: prod = res[0]
except (ValueError, xmlrpc.client.Fault):
    pass
if not prod:
    res = call("product.product", "search_read",
               [[("code", "=", ARG)]], {"fields": PROD_FIELDS})
    if res: prod = res[0]
if not prod:
    res = call("product.product", "search_read",
               [[("barcode", "=", ARG)]], {"fields": PROD_FIELDS})
    if res: prod = res[0]
if not prod:
    res = call("product.product", "search_read",
               [[("name", "=", ARG)]], {"fields": PROD_FIELDS})
    if res: prod = res[0]
if not prod:
    sys.exit(f"no product matched {ARG!r}")
PROD_ID = prod["id"]

print(f"\n=== Product {PROD_ID} ===")
print(f"  name             {prod['name']}")
print(f"  code             {prod['code']}")
print(f"  barcode          {prod['barcode']}")
print(f"  uom (stock)      {prod['uom_id']}")
print(f"  uom_ids (alt)    {prod['uom_ids']}")
print(f"  category         {prod['categ_id']}")
print(f"  tracking         {prod['tracking']}")
print(f"  type             {prod['type']}")
print(f"  sale_ok          {prod['sale_ok']}")
print(f"  routes           {prod['route_ids']}")
print(f"  packaging_ids    {prod['packaging_ids']}")

# UoM details (v19 schema)
uom = call("uom.uom", "read", [[prod["uom_id"][0]]],
            {"fields": ["id","name","relative_uom_id","relative_factor","factor","rounding","package_type_id"]})[0]
print(f"\n=== Stock UoM (v19 schema) ===")
print(f"  id={uom['id']}  name={uom['name']}")
print(f"  relative_uom_id  {uom['relative_uom_id']}  (parent reference unit)")
print(f"  relative_factor  {uom['relative_factor']}  (how many of relative this contains)")
print(f"  factor (abs)     {uom['factor']}")
print(f"  rounding         {uom['rounding']}")
print(f"  package_type_id  {uom['package_type_id']}")

# All UoMs that this product can use (uom_ids)
if prod["uom_ids"]:
    sib_ids = prod["uom_ids"]
    sibs = call("uom.uom", "read", [sib_ids],
                {"fields": ["id","name","relative_uom_id","relative_factor","factor"]})
    print(f"\n=== Allowed alternate UoMs for this product ===")
    for s in sibs:
        print(f"  id={s['id']:>4}  {s['name']:<25}  relative_uom={s['relative_uom_id']}  rel_factor={s['relative_factor']}  factor={s['factor']}")

# Packaging
packs = call("product.packaging", "search_read",
             [[("product_id", "=", PROD_ID)]],
             {"fields": ["id","name","qty","product_uom_id","sales","purchase","barcode","sequence"]})
print(f"\n=== Packaging records ({len(packs)}) ===")
for pk in packs:
    uomname = pk['product_uom_id'][1] if pk['product_uom_id'] else '-'
    print(f"  id={pk['id']:>4}  seq={pk['sequence']:>3}  {pk['name']:<30}  qty={pk['qty']} of {uomname}  sales={pk['sales']}  purchase={pk['purchase']}")

# BOMs for this product
boms = call("mrp.bom", "search_read",
            [["|", ("product_id", "=", PROD_ID),
              "&", ("product_id", "=", False), ("product_tmpl_id.product_variant_ids", "in", [PROD_ID])]],
            {"fields": ["id","code","product_id","product_tmpl_id","product_qty","product_uom_id",
                        "type","consumption","operation_ids","bom_line_ids"],
             "order": "id ASC"})
print(f"\n=== BOMs ({len(boms)}) ===")
for b in boms:
    print(f"\n  --- BOM id={b['id']} ---")
    print(f"    code         {b['code']}")
    print(f"    product_id   {b['product_id']}")
    print(f"    product_tmpl {b['product_tmpl_id']}")
    print(f"    product_qty  {b['product_qty']}  uom={b['product_uom_id']}")
    print(f"    type         {b['type']}")
    print(f"    consumption  {b['consumption']}")
    print(f"    op count     {len(b['operation_ids'])}  line count {len(b['bom_line_ids'])}")
    if b["operation_ids"]:
        ops = call("mrp.routing.workcenter", "read", [b["operation_ids"]],
                   {"fields": ["sequence","name","workcenter_id","time_cycle_manual"]})
        print(f"    operations:")
        for op in sorted(ops, key=lambda o: o["sequence"]):
            wc = op['workcenter_id'][1] if op['workcenter_id'] else '-'
            print(f"      seq {op['sequence']:>3}  {op['name']:<30}  wc={wc}  t/cycle={op['time_cycle_manual']}")
    if b["bom_line_ids"]:
        lines = call("mrp.bom.line", "read", [b["bom_line_ids"]],
                     {"fields": ["product_id","product_qty","product_uom_id","operation_id"]})
        print(f"    components:")
        for ln in lines:
            opn = ln["operation_id"][1] if ln["operation_id"] else "(unassigned)"
            uomn = ln['product_uom_id'][1] if ln['product_uom_id'] else '-'
            print(f"      {ln['product_id'][1][:50]:<50}  qty={ln['product_qty']:<8}  uom={uomn:<10}  op={opn}")

# Recent successful MOs
print(f"\n=== Recent successful MOs (last 5 done) ===")
mos = call("mrp.production", "search_read",
    [[("product_id", "=", PROD_ID), ("state", "=", "done")]],
    {"fields": ["id","name","product_qty","qty_produced","date_finished","lot_producing_ids","bom_id"],
     "order": "id DESC", "limit": 5})
for mo in mos:
    print(f"  {mo['name']:<18}  qty={mo['product_qty']}/produced={mo['qty_produced']}  finished={mo['date_finished']}  bom={mo['bom_id']}  lots={mo['lot_producing_ids']}")

# Open MOs (in case there are recent ones to learn from)
print(f"\n=== Currently open MOs (last 5) ===")
mos_open = call("mrp.production", "search_read",
    [[("product_id", "=", PROD_ID), ("state", "in", ["confirmed","progress","to_close"])]],
    {"fields": ["id","name","state","product_qty","qty_producing","date_start"],
     "order": "id DESC", "limit": 5})
for mo in mos_open:
    print(f"  {mo['name']:<18}  state={mo['state']:<10}  qty={mo['product_qty']}  producing={mo['qty_producing']}  start={mo['date_start']}")

# Stock state
print(f"\n=== Stock state ===")
print(f"  qty_available    {prod['qty_available']}")
print(f"  virtual_available {prod['virtual_available']}")
print(f"  incoming         {prod['incoming_qty']}")
print(f"  outgoing         {prod['outgoing_qty']}")

print(f"\n--- end probe ---")
