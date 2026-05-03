"""
Zero out negative stock.quant rows on internal locations on PRODUCTION via XML-RPC.

This is the prod equivalent of zero_negatives.py (which operates on the local
Postgres DB). On prod we must use XML-RPC against Odoo Online — direct DB
access isn't available.

Process per quant:
  1. Set inventory_quantity = 0
  2. Call action_apply_inventory() to create a proper inventory adjustment
     move (auditable, reversible)

Filters out kit (phantom-BOM) products — those don't have real stock and
aren't relevant to the migration test.

Usage:
    python prod_zero_negatives.py            # dry-run (default)
    python prod_zero_negatives.py --commit   # actually apply

Credentials: edit SERVER CONFIGURATION at the top.
"""

import argparse
import json
import ssl
import sys
import xmlrpc.client
from datetime import datetime
from pathlib import Path

# ============================================================================
# SERVER CONFIGURATION — reads from env vars (copy ../.env.example to ../.env)
# ============================================================================
import os as _os


def _load_dotenv():
    p = Path(__file__).parent.parent / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        _os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()
URL      = _os.environ.get("ODOO_PROD_URL")
DB       = _os.environ.get("ODOO_PROD_DB")
USERNAME = _os.environ.get("ODOO_PROD_USER")
API_KEY  = _os.environ.get("ODOO_PROD_API_KEY")
if not all([URL, DB, USERNAME, API_KEY]):
    raise SystemExit("Missing ODOO_PROD_* env vars. See ../.env.example.")
# ============================================================================


def connect():
    ctx = ssl.create_default_context()
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", context=ctx, allow_none=True)
    uid = common.authenticate(DB, USERNAME, API_KEY, {})
    if not uid:
        print("ERROR: authentication failed", file=sys.stderr)
        sys.exit(1)
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", context=ctx, allow_none=True)
    return uid, models


def call(models, uid, model, method, args, kwargs=None):
    return models.execute_kw(DB, uid, API_KEY, model, method, args, kwargs or {})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true",
                        help="actually apply the zeroing (default: dry-run)")
    args = parser.parse_args()

    print("=" * 78)
    print(f"  PROD ZERO-NEGATIVES   [{'*** COMMIT ***' if args.commit else 'DRY-RUN'}]")
    print(f"  Server: {URL}")
    print(f"  Time:   {datetime.now().isoformat()}")
    print("=" * 78)

    uid, models = connect()
    print(f"  Authenticated UID {uid}")

    # 1. Internal location IDs
    int_locs = call(models, uid, "stock.location", "search",
                    [[("usage", "=", "internal")]])
    print(f"  {len(int_locs)} internal locations")

    # 2. Negative quants on internal
    quants = call(models, uid, "stock.quant", "search_read",
                  [[("location_id", "in", int_locs), ("quantity", "<", 0)]],
                  {"fields": ["id", "product_id", "location_id", "quantity",
                              "reserved_quantity", "lot_id"],
                   "order": "quantity asc"})
    print(f"  {len(quants)} negative quants found")
    if not quants:
        print("  Nothing to do.")
        return

    # 3. Identify kits — products on a phantom BOM
    pids = list({q["product_id"][0] for q in quants if q.get("product_id")})
    prods = call(models, uid, "product.product", "read", [pids],
                 {"fields": ["id", "default_code", "name", "product_tmpl_id"]})
    tmpl_ids = list({p["product_tmpl_id"][0] for p in prods if p.get("product_tmpl_id")})
    phantom_boms = call(models, uid, "mrp.bom", "search_read",
                        [[("type", "=", "phantom"),
                          ("product_tmpl_id", "in", tmpl_ids)]],
                        {"fields": ["product_tmpl_id"]})
    kit_tmpl_ids = {b["product_tmpl_id"][0] for b in phantom_boms if b.get("product_tmpl_id")}
    kit_pids = {p["id"] for p in prods if p.get("product_tmpl_id") and p["product_tmpl_id"][0] in kit_tmpl_ids}

    prod_by_id = {p["id"]: p for p in prods}
    target_quants = []
    skipped_kits = []
    for q in quants:
        pid = q["product_id"][0] if q.get("product_id") else None
        if pid in kit_pids:
            skipped_kits.append(q)
        else:
            target_quants.append(q)

    # 4. Preview
    print(f"\n  {len(target_quants)} on real products (will be zeroed)")
    print(f"  {len(skipped_kits)} on kits (skipped — kits have no real stock)")
    print()
    print(f"  {'qid':>6} {'pid':>5} {'code':<14} {'name':<32} {'qty':>12}")
    for q in target_quants[:20]:
        pid = q["product_id"][0] if q.get("product_id") else 0
        p = prod_by_id.get(pid, {})
        code = (p.get("default_code") or "")[:14]
        name = (p.get("name") or "")[:32]
        print(f"  {q['id']:>6} {pid:>5} {code:<14} {name:<32} {q['quantity']:>12.2f}")
    if len(target_quants) > 20:
        print(f"  ... {len(target_quants) - 20} more")

    if not args.commit:
        print(f"\n  DRY-RUN: would zero {len(target_quants)} quants via inventory adjustment.")
        print(f"           Re-run with --commit to apply.")
        return

    # 5. Commit — apply per quant via inventory_quantity + action_apply_inventory
    log_path = Path(__file__).parent / "logs" / f"prod_zero_negatives_{datetime.now():%Y%m%d_%H%M%S}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_records = []
    print(f"\n  COMMITTING ...")
    success = 0
    failed = 0
    for q in target_quants:
        try:
            # Set inventory_quantity to 0 — Odoo treats this as the "user-counted" qty
            call(models, uid, "stock.quant", "write",
                 [[q["id"]], {"inventory_quantity": 0.0}])
            # Then apply the adjustment to make it stick
            try:
                call(models, uid, "stock.quant", "action_apply_inventory", [[q["id"]]])
            except Exception as e:
                # Some Odoo Online quirks return None marshal errors here even on success
                if "marshal None" not in str(e):
                    raise
            success += 1
            log_records.append({"quant_id": q["id"], "product_id": q["product_id"][0],
                                "old_qty": q["quantity"], "status": "ok"})
        except Exception as e:
            failed += 1
            log_records.append({"quant_id": q["id"], "product_id": q["product_id"][0],
                                "old_qty": q["quantity"], "status": "ERROR",
                                "error": str(e)[:200]})
            print(f"    ! quant {q['id']}: {str(e)[:100]}")

    log_path.write_text(json.dumps({
        "timestamp": datetime.now().isoformat(),
        "url": URL, "db": DB,
        "total": len(target_quants),
        "success": success, "failed": failed,
        "records": log_records,
    }, indent=2))
    print(f"\n  Done: {success} succeeded, {failed} failed")
    print(f"  Log: {log_path}")

    # 6. Verify
    remaining = call(models, uid, "stock.quant", "search_count",
                     [[("location_id", "in", int_locs), ("quantity", "<", 0)]])
    print(f"  Negative quants remaining (incl. kits): {remaining}")


if __name__ == "__main__":
    main()
