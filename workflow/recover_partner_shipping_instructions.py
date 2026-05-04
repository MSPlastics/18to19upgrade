"""Restore the Shipping Instructions field on the partner Delivery Information tab.

The field `x_studio_shipping_instructions` (Char, id=14636) and its
production data (205 partners with values) survived the v18->v19
migration. What got deleted was the inheriting form view that placed
the field on the partner form — same pattern as the mrp.production /
mrp.bom / product.template Studio form views that the migration
silently dropped (see V19_UPGRADE_NOTES.md "Studio form views deleted
entirely"). The data is recoverable; we just need a small inherit view
that re-injects the field into the existing "Delivery Information" page
that ksc_partner provides.

Idempotent — looks up by view name, updates if found, creates if not.

Usage:
    python recover_partner_shipping_instructions.py --target staging         # dry-run
    python recover_partner_shipping_instructions.py --target staging --commit
    python recover_partner_shipping_instructions.py --target prod --commit
"""
import argparse
import os
import ssl
import sys
import xmlrpc.client
from pathlib import Path

VIEW_NAME = "MSP res.partner Shipping Instructions (recovered)"

# The ksc_partner module's view (priority=16) creates `<page name="delivery_info">`
# on the res.partner form. Our priority must be > 16 so the page exists when our
# xpath runs (Odoo applies inherit views in ascending priority order).
PRIORITY = 30

ARCH = '''<data>
    <xpath expr="//page[@name='delivery_info']" position="inside">
        <group string="Customer Shipping Instructions">
            <field name="x_studio_shipping_instructions" nolabel="1"/>
        </group>
    </xpath>
</data>'''


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


def connect(target):
    prefix = f"ODOO_{target.upper()}_"
    url = os.environ.get(prefix + "URL")
    db = os.environ.get(prefix + "DB")
    user = os.environ.get(prefix + "USER")
    api_key = os.environ.get(prefix + "API_KEY")
    if not all([url, db, user, api_key]):
        sys.exit(f"Missing {prefix}* env vars")
    ctx = ssl.create_default_context()
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", context=ctx, allow_none=True)
    uid = common.authenticate(db, user, api_key, {})
    if not uid:
        sys.exit(f"auth failed for {target}")
    obj = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", context=ctx, allow_none=True)

    def call(model, method, args, kwargs=None):
        return obj.execute_kw(db, uid, api_key, model, method, args, kwargs or {})
    return url, call


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["staging", "prod"], default="staging")
    parser.add_argument("--commit", action="store_true",
                        help="actually write (default: dry-run)")
    args = parser.parse_args()

    url, call = connect(args.target)
    print(f"Target: {args.target}  ({url})  mode: {'COMMIT' if args.commit else 'dry-run'}")

    # Sanity check: field must exist on res.partner before the view tries to use it.
    fid = call("ir.model.fields", "search",
               [[("model", "=", "res.partner"),
                 ("name", "=", "x_studio_shipping_instructions")]])
    if not fid:
        sys.exit("res.partner.x_studio_shipping_instructions field MISSING — nothing to display.")
    print(f"  field x_studio_shipping_instructions exists (id={fid[0]})")

    # Resolve the parent view xml id -> id (stable across builds)
    parent = call("ir.model.data", "search_read",
                  [[("module", "=", "base"), ("name", "=", "view_partner_form")]],
                  {"fields": ["res_id"]})
    if not parent:
        sys.exit("base.view_partner_form xml id not found")
    parent_id = parent[0]["res_id"]
    print(f"  parent view base.view_partner_form -> id={parent_id}")

    # Sanity check: ksc_partner's delivery_info page should be present
    ksc_views = call("ir.ui.view", "search_read",
                     [[("model", "=", "res.partner"),
                       ("inherit_id", "=", parent_id),
                       ("arch_db", "ilike", 'name="delivery_info"')]],
                     {"fields": ["id", "name", "priority"]})
    if not ksc_views:
        print("  WARNING: no inheriting view defines page name='delivery_info'.")
        print("  ksc_partner may not be installed; xpath will fail at view validation.")
    else:
        for v in ksc_views:
            print(f"  delivery_info page provided by view id={v['id']} name={v['name']!r} priority={v['priority']}")

    # Upsert by name
    existing = call("ir.ui.view", "search_read",
                    [[("name", "=", VIEW_NAME), ("model", "=", "res.partner")]],
                    {"fields": ["id", "active", "priority"]})
    vals = {
        "name": VIEW_NAME,
        "model": "res.partner",
        "type": "form",
        "inherit_id": parent_id,
        "arch_db": ARCH,
        "priority": PRIORITY,
        "active": True,
    }
    if existing:
        vid = existing[0]["id"]
        if not args.commit:
            print(f"  view {vid}: would UPDATE")
            return
        call("ir.ui.view", "write", [[vid], vals])
        print(f"  view {vid}: UPDATED")
        return

    if not args.commit:
        print(f"  view (recovered): would CREATE")
        return
    new_id = call("ir.ui.view", "create", [vals])
    print(f"  view {new_id}: CREATED")


if __name__ == "__main__":
    main()
