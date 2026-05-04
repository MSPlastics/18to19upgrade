"""Patch QWeb report views that still reference v18's `line.product_uom`
(renamed to `product_uom_id` in v19 on sale.order.line and purchase.order.line).

Standalone post-cutover fix — NOT part of the migration recovery. Use when
PDF report rendering fails with `KeyError: 'product_uom'`.

Scope: views matching the regex `\\bline\\.product_uom\\b(?!_)` only —
catches `line.product_uom`, `line.product_uom.name` etc. Does NOT touch
`bo_line.product_uom` or `raw_line.product_uom` (those refer to stock.move
records where `product_uom` is still a valid v19 field).

Idempotent: only writes views that actually contain the broken token.

Usage:
    python fix_qweb_uom_v18_residue.py --target staging         # dry-run
    python fix_qweb_uom_v18_residue.py --target staging --commit
    python fix_qweb_uom_v18_residue.py --target prod --commit   # post-test
"""
import argparse
import os
import re
import ssl
import sys
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


# Matches line.product_uom but NOT line.product_uom_id, line.product_uom_qty,
# bo_line.product_uom, raw_line.product_uom.
LINE_BAD = re.compile(r"\bline\.product_uom\b(?!_)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["staging", "prod"], default="staging")
    parser.add_argument("--commit", action="store_true",
                        help="actually write (default: dry-run)")
    args = parser.parse_args()

    url, call = connect(args.target)
    print(f"Target: {args.target}  ({url})  mode: {'COMMIT' if args.commit else 'dry-run'}")

    # SQL prefilter is broad so we don't miss patterns like
    # `line.product_uom.name` (no closing quote). Python regex is the
    # authoritative filter.
    candidates = call("ir.ui.view", "search_read",
                      [[("type", "=", "qweb"),
                        ("arch_db", "ilike", "line.product_uom")]],
                      {"fields": ["id", "key", "arch_db"]})
    print(f"SQL prefilter: {len(candidates)} candidate(s)")

    patched = skipped = failed = 0
    for v in candidates:
        old = v["arch_db"] or ""
        if not LINE_BAD.search(old):
            skipped += 1
            continue
        new = LINE_BAD.sub("line.product_uom_id", old)
        occ = len(LINE_BAD.findall(old))
        print(f"  id={v['id']} {v['key']!r}: {occ} occurrence(s) {'->' if args.commit else 'would patch ->'} line.product_uom_id")
        if not args.commit:
            patched += 1
            continue
        try:
            call("ir.ui.view", "write", [[v["id"]], {"arch_db": new}])
            patched += 1
        except Exception as e:
            print(f"    write FAILED: {str(e)[:200]}")
            failed += 1

    if args.commit:
        print(f"\nDone. Patched {patched}, untouched {skipped}, failed {failed}.")
    else:
        print(f"\nDRY-RUN. Would patch {patched}, untouched {skipped}.")
        print("Re-run with --commit to apply.")


if __name__ == "__main__":
    main()
