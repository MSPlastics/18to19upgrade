"""Comprehensive patcher for v18 field references in Studio QWeb reports
that v19 renamed or removed.

Handles renames discovered during v19 cutover smoke-testing:
  sale.order.line:
    line.product_uom        -> line.product_uom_id
    line.tax_id             -> line.tax_ids
  purchase.order.line:
    line.product_uom        -> line.product_uom_id  (caught by same regex)
    line.taxes_id           -> line.tax_ids
  purchase.order:
    o.notes                 -> o.note
  stock.picking:
    o.has_packages          -> o.packages_count   (truthy in t-if)
  Custom (MSP-specific) — reflects Anthony's choice in 2026-05-04 session:
    line.sh_line_customer_code         -> line.product_customer_code
    line.sh_line_customer_product_name -> (span deleted)

Each patch is a precompiled regex pair. Idempotent — only writes views
where at least one regex actually matches.

Usage:
    python fix_qweb_v18_residue.py --target staging         # dry-run
    python fix_qweb_v18_residue.py --target staging --commit
    python fix_qweb_v18_residue.py --target prod --commit
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


# Each rule: (pattern, replacement, label).
# Patterns use word boundaries so they don't catch substring matches.
RULES = [
    # sale.order.line + purchase.order.line: product_uom -> product_uom_id
    (re.compile(r"\bline\.product_uom\b(?!_)"), "line.product_uom_id",
     "line.product_uom -> line.product_uom_id"),
    # sale.order.line: tax_id -> tax_ids
    (re.compile(r"\bline\.tax_id\b(?!s)"), "line.tax_ids",
     "line.tax_id -> line.tax_ids"),
    # purchase.order.line: taxes_id -> tax_ids
    (re.compile(r"\bline\.taxes_id\b"), "line.tax_ids",
     "line.taxes_id -> line.tax_ids"),
    # purchase.order: notes -> note
    (re.compile(r"\bo\.notes\b"), "o.note",
     "o.notes -> o.note"),
    # stock.picking: has_packages -> packages_count (truthy)
    (re.compile(r"\bo\.has_packages\b"), "o.packages_count",
     "o.has_packages -> o.packages_count"),
    # MSP-specific: third-party sh_* fields replaced with product_customerinfo equivalents
    (re.compile(r"\bline\.sh_line_customer_code\b"), "line.product_customer_code",
     "line.sh_line_customer_code -> line.product_customer_code"),
    # MSP-specific: drop the span entirely (no v19 equivalent)
    (re.compile(r'\s*<span\s+t-field="line\.sh_line_customer_product_name"\s*/>\s*'), "",
     "<span line.sh_line_customer_product_name/> deleted"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["staging", "prod"], default="staging")
    parser.add_argument("--commit", action="store_true",
                        help="actually write (default: dry-run)")
    args = parser.parse_args()

    url, call = connect(args.target)
    print(f"Target: {args.target}  ({url})  mode: {'COMMIT' if args.commit else 'dry-run'}")

    # Broad SQL prefilter: pull every active QWeb view, regex-filter Python-side.
    candidates = call("ir.ui.view", "search_read",
                      [[("type", "=", "qweb"), ("active", "=", True)]],
                      {"fields": ["id", "key", "arch_db"]})
    print(f"Loaded {len(candidates)} active QWeb views")

    patched = skipped = failed = 0
    for v in candidates:
        old = v["arch_db"] or ""
        new = old
        applied = []
        for pat, repl, label in RULES:
            n = len(pat.findall(new))
            if n:
                new = pat.sub(repl, new)
                applied.append((label, n))
        if new == old:
            skipped += 1
            continue
        print(f"\n  id={v['id']} {v['key']!r}")
        for label, n in applied:
            print(f"    {n}x  {label}")
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
