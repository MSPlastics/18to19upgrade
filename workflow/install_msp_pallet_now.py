"""One-shot: trigger button_immediate_install on msp_pallet and surface errors."""
import os, ssl, sys, xmlrpc.client
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

call("ir.module.module", "update_list", [])
rec = call("ir.module.module", "search_read",
           [[("name", "=", "msp_pallet")]],
           {"fields": ["id", "state", "installed_version", "latest_version"]})
print(f"before: {rec}")
if not rec:
    sys.exit("msp_pallet not visible to Odoo — push hasn't deployed yet")

mod_id = rec[0]["id"]
print(f"calling button_immediate_install on id={mod_id}...")
try:
    call("ir.module.module", "button_immediate_install", [[mod_id]])
except xmlrpc.client.Fault as e:
    print(f"FAULT: {e.faultString[:3000]}")
    sys.exit(1)

rec = call("ir.module.module", "read", [[mod_id]],
           {"fields": ["state", "installed_version", "latest_version"]})
print(f"after: {rec}")
