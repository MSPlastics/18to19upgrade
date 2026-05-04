"""Fix Studio compute bodies that reference v18's procurement_group_id on mrp.production.

v19 removed mrp.production.procurement_group_id (replaced by reference_ids per
V19_UPGRADE_NOTES.md). Studio compute bodies whose Python reads
`record.procurement_group_id.sale_id` to walk to the originating sale order
raise AttributeError at compute time, surfacing as
'Error: 'mrp.production' object has no attribute 'procurement_group_id''
inside the computed value (caught by a bare except: in the compute body).

For MSP, the custom `mrp.production.sale_order_id` field is the v19 way to
reach the source SO (~73% of MOs are linked, and most affected computes
already have a `record.origin`-based search fallback for the rest). We
swap `record.procurement_group_id.sale_id` -> `record.sale_order_id`. Same
Recordset semantics, no data loss.

Other patterns of `procurement_group_id` (raw access without `.sale_id`,
non-`record` variable name, etc.) need case-by-case review — script reports
those as residual matches without auto-fixing.

Idempotent — only writes fields whose compute still contains the broken
pattern. Safe to re-run.

Usage:
    python fix_studio_procurement_group_compute.py --target staging         # dry-run
    python fix_studio_procurement_group_compute.py --target staging --commit
    python fix_studio_procurement_group_compute.py --target prod --commit
"""
import argparse
import os
import re
import ssl
import sys
import xmlrpc.client
from pathlib import Path

PATTERNS = [
    # `record.procurement_group_id.sale_id` -> `record.sale_order_id`
    # (with optional .sudo() on either side preserved by being outside the match).
    (re.compile(r"\brecord\.procurement_group_id\.sale_id\b"), "record.sale_order_id",
     "record.procurement_group_id.sale_id -> record.sale_order_id"),
]


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
                [[("model", "=", "mrp.production"),
                  ("state", "=", "manual"),
                  ("compute", "ilike", "procurement_group_id")]],
                {"fields": ["id", "name", "compute"]})
    if not flds:
        print("\nNothing to do — no manual fields on mrp.production with "
              "'procurement_group_id' in compute.")
        return

    patched = 0
    for f in flds:
        old = f["compute"] or ""
        new = old
        applied = []
        for pat, repl, label in PATTERNS:
            n = len(pat.findall(new))
            if n:
                new = pat.sub(repl, new)
                applied.append(f"{n}x {label}")

        residual = re.findall(r"\bprocurement_group_id\b", new)

        print(f"\n  id={f['id']} {f['name']!r}")
        for line in applied:
            print(f"    {line}")
        if residual:
            print(f"    WARNING: {len(residual)} unhandled procurement_group_id reference(s) remain — manual review needed")
        if new == old:
            print("    no patterns matched — manual review needed")
            continue

        if not args.commit:
            patched += 1
            continue
        try:
            call("ir.model.fields", "write", [[f["id"]], {"compute": new}])
            print("    write: OK")
            patched += 1
        except Exception as e:
            print(f"    write FAILED: {str(e)[-300:]}")

    if args.commit:
        print(f"\nDone. Patched {patched} field(s).")
    else:
        print(f"\nDRY-RUN. Would patch {patched} field(s). Re-run with --commit to apply.")


if __name__ == "__main__":
    main()
