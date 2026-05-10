"""Check what stock package-related models exist in v19 staging."""
import os, ssl, xmlrpc.client
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

ctx = ssl.create_default_context()
common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", context=ctx, allow_none=True)
uid = common.authenticate(DB, USER, KEY, {})
models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", context=ctx, allow_none=True)

def call(model, method, args, kw=None):
    return models.execute_kw(DB, uid, KEY, model, method, args, kw or {})

# Check ir.model for any "package" model
print("=== models with 'package' in name ===")
recs = call("ir.model", "search_read",
            [[("model", "ilike", "package")]],
            {"fields": ["model", "name"]})
for r in recs:
    print(f"  {r['model']:<40} {r['name']!r}")

# Check stock module state
print("\n=== stock module ===")
mods = call("ir.module.module", "search_read",
            [[("name", "in", ["stock", "stock_picking_batch", "stock_storage", "delivery"])]],
            {"fields": ["name", "state", "installed_version"]})
for m in mods:
    print(f"  {m['name']:<25} {m['state']:<15} {m['installed_version']}")

# Check what fields stock.move.line has for packaging
print("\n=== stock.move.line fields with 'package' ===")
fields = call("stock.move.line", "fields_get", [], {"attributes": ["string", "type", "relation"]})
for f, info in sorted(fields.items()):
    if "package" in f.lower() or (info.get("relation") and "package" in info["relation"].lower()):
        print(f"  {f}: {info['type']:<12} -> {info.get('relation')!r}  ({info['string']!r})")
