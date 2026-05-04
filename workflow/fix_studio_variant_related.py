"""Strip the redundant `product_variant_id.` prefix from Studio related fields on product.template.

When a manual Studio field on product.template has
`related='product_variant_id.<something>'`, Odoo's invalidation
trigger machinery in v19 will eventually try to run
`search([('product_variant_id', 'in', [...])], order='id')`
against product.template, which fails because
product.template.product_variant_id is non-stored:

    ValueError: Cannot convert product.template.product_variant_id
    to SQL because it is not stored

This surfaces when the user edits any related-target chain — most
notably `customer_ids.product_name` on product.template (i.e., a
customer's drop part number on the Customers tab of a product).

Fix: drop the `product_variant_id.` prefix. The same field is
addressable directly on product.template since the variant fields
that follow either exist on the template or are related back to it.

Idempotent — only writes fields whose related still starts with the
prefix, and only on product.template (where the rewrite is provably
equivalent — fields on other models with `something.product_variant_id.X`
need a per-case fix).

Usage:
    python fix_studio_variant_related.py --target staging         # dry-run
    python fix_studio_variant_related.py --target staging --commit
    python fix_studio_variant_related.py --target prod --commit
"""
import argparse
import os
import ssl
import sys
import xmlrpc.client
from pathlib import Path

PREFIX = "product_variant_id."


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

    flds = call("ir.model.fields", "search_read",
                [[("model", "=", "product.template"),
                  ("state", "=", "manual"),
                  ("related", "=like", PREFIX + "%")]],
                {"fields": ["id", "name", "related", "ttype", "store"]})
    if not flds:
        print("\nNothing to do — no manual product.template fields with "
              f"related starting with '{PREFIX}'.")
        return

    patched = 0
    for f in flds:
        old = f["related"]
        new = old[len(PREFIX):]  # strip the leading prefix
        print(f"\n  id={f['id']}  product.template.{f['name']}  "
              f"({f['ttype']}, store={f['store']})")
        print(f"    OLD: {old}")
        print(f"    NEW: {new}")
        if not args.commit:
            patched += 1
            continue
        try:
            call("ir.model.fields", "write", [[f["id"]], {"related": new}])
            print(f"    write: OK")
            patched += 1
        except Exception as e:
            print(f"    write FAILED: {str(e)[-300:]}")

    if args.commit:
        print(f"\nDone. Patched {patched} field(s).")
    else:
        print(f"\nDRY-RUN. Would patch {patched} field(s). Re-run with --commit to apply.")


if __name__ == "__main__":
    main()
