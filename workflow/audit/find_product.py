"""Search products by partial code/name match."""
import os, ssl, sys, xmlrpc.client
from pathlib import Path

def _load():
    p = Path(__file__).resolve().parent.parent.parent / ".env"
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
_load()

URL = os.environ["ODOO_STAGING_URL"]; DB = os.environ["ODOO_STAGING_DB"]
USER = os.environ.get("ODOO_STAGING_USER", "admin@mountainstatesplastics.com")
KEY = os.environ["ODOO_STAGING_API_KEY"]
ctx = ssl.create_default_context()
common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", context=ctx, allow_none=True)
uid = common.authenticate(DB, USER, KEY, {})
m = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", context=ctx, allow_none=True)
def call(model, method, args, kw=None):
    return m.execute_kw(DB, uid, KEY, model, method, args, kw or {})

q = sys.argv[1]
print(f"\n--- exact default_code match for {q!r} ---")
exact = call("product.product", "search_read", [[("default_code","=",q)]],
    {"fields":["id","default_code","name","uom_id","sale_ok"]})
for r in exact: print(f"  id={r['id']:>5}  {r['default_code']:<15}  {r['name'][:60]:<60}  uom={r['uom_id'][1] if r['uom_id'] else '-'}  sale={r['sale_ok']}")

print(f"\n--- contains {q!r} in default_code ---")
res = call("product.product", "search_read", [[("default_code","ilike",q)]],
    {"fields":["id","default_code","name","uom_id","sale_ok"], "limit":30})
for r in res: print(f"  id={r['id']:>5}  {r['default_code']:<15}  {r['name'][:60]:<60}  uom={r['uom_id'][1] if r['uom_id'] else '-'}  sale={r['sale_ok']}")

print(f"\n--- contains {q!r} in name ---")
res = call("product.product", "search_read", [[("name","ilike",q)]],
    {"fields":["id","default_code","name","uom_id","sale_ok"], "limit":30})
for r in res: print(f"  id={r['id']:>5}  {(r['default_code'] or '-'):<15}  {r['name'][:60]:<60}  uom={r['uom_id'][1] if r['uom_id'] else '-'}  sale={r['sale_ok']}")
