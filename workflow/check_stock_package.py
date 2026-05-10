"""Inspect stock.package fields + view names on v19 staging."""
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

print("=== stock.package fields ===")
fields = call("stock.package", "fields_get", [], {"attributes": ["string", "type", "relation"]})
for f, info in sorted(fields.items()):
    rel = f" -> {info.get('relation')}" if info.get('relation') else ""
    print(f"  {f}: {info['type']}{rel}  ({info['string']!r})")

print("\n=== stock.package form views (likely inherit targets) ===")
views = call("ir.ui.view", "search_read",
             [[("model", "=", "stock.package"), ("type", "=", "form")]],
             {"fields": ["xml_id", "name", "inherit_id"]})
for v in views:
    print(f"  {v['xml_id']:<60} {v['name']!r}")

print("\n=== stock.picking form view xml_ids ===")
views = call("ir.ui.view", "search_read",
             [[("model", "=", "stock.picking"), ("type", "=", "form"), ("inherit_id", "=", False)]],
             {"fields": ["xml_id", "name"]})
for v in views:
    print(f"  {v['xml_id']:<60} {v['name']!r}")
