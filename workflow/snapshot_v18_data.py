"""Snapshot v18 production data needed by post_migration_recovery.py.

Run this BEFORE the prod v18->v19 cutover. The v19 migration drops
product.packaging entirely (replaced by uom.uom alternates), so once prod
is upgraded there is no way to read those records back. We capture them
to JSON now, then the recovery script reads from the snapshot at cutover
instead of attempting a live XML-RPC call to a v18 prod that no longer
exists.

Captures:
  - All product.packaging records on prod-v18 (242 expected)
  - Joined product.product info (id + default_code) for v19 matching
  - All mrp.production records with non-zero x_studio_qtypkg or
    x_studio_finished_qtyplt (~491 expected)

Output:
  workflow/snapshots/v18_prod_snapshot.json

Usage:
  cd workflow
  python snapshot_v18_data.py
"""
import json
import os
import ssl
import sys
import xmlrpc.client
from datetime import datetime, timezone
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


def connect_prod():
    url = os.environ.get("ODOO_PROD_URL")
    db = os.environ.get("ODOO_PROD_DB")
    user = os.environ.get("ODOO_PROD_USER")
    api_key = os.environ.get("ODOO_PROD_API_KEY")
    if not all([url, db, user, api_key]):
        sys.exit("Missing ODOO_PROD_* env vars")
    ctx = ssl.create_default_context()
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", context=ctx, allow_none=True)
    uid = common.authenticate(db, user, api_key, {})
    if not uid:
        sys.exit("auth failed for prod")
    obj = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", context=ctx, allow_none=True)

    def call(model, method, args, kwargs=None):
        return obj.execute_kw(db, uid, api_key, model, method, args, kwargs or {})
    return url, db, call


def snapshot():
    url, db, call = connect_prod()
    print(f"Snapshot source: {url} ({db})")

    # 1. product.packaging records
    pkgs = call("product.packaging", "search_read", [[]],
                {"fields": ["id", "name", "product_id", "qty", "barcode",
                            "sequence", "sales", "purchase", "company_id"]})
    print(f"  product.packaging: {len(pkgs)} records")

    # 2. Resolve product default_codes for the products referenced (for v19 fallback matching)
    pids = sorted({p["product_id"][0] for p in pkgs if p.get("product_id")})
    products = call("product.product", "read", [pids],
                    {"fields": ["id", "default_code", "display_name"]})
    pp_by_id = {p["id"]: p for p in products}

    packagings = []
    for p in pkgs:
        prod_pid = p["product_id"][0] if p.get("product_id") else None
        meta = pp_by_id.get(prod_pid, {}) if prod_pid else {}
        packagings.append({
            "id": p["id"],
            "name": p["name"],
            "product_id": prod_pid,
            "product_default_code": meta.get("default_code") or None,
            "product_display_name": meta.get("display_name") or None,
            "qty": p["qty"],
            "barcode": p.get("barcode") or None,
            "sequence": p.get("sequence") or 1,
            "sales": bool(p.get("sales")),
            "purchase": bool(p.get("purchase")),
            "company_id": p["company_id"][0] if p.get("company_id") else None,
        })

    # 3. mrp.production Studio qty values
    mos = call("mrp.production", "search_read",
               [["|", ("x_studio_qtypkg", "!=", 0),
                 ("x_studio_finished_qtyplt", "!=", 0)]],
               {"fields": ["id", "name", "x_studio_qtypkg", "x_studio_finished_qtyplt"]})
    print(f"  mrp.production with Studio qty data: {len(mos)} records")

    studio_qtys = [
        {"id": m["id"], "name": m["name"],
         "x_studio_qtypkg": m["x_studio_qtypkg"],
         "x_studio_finished_qtyplt": m["x_studio_finished_qtyplt"]}
        for m in mos
    ]

    snapshot = {
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "source_url": url,
        "source_db": db,
        "product_packagings": packagings,
        "mrp_production_studio_qtys": studio_qtys,
    }

    out_dir = Path(__file__).parent / "snapshots"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "v18_prod_snapshot.json"
    out_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    print(f"\nWrote snapshot to {out_path}")
    print(f"  packagings: {len(packagings)}")
    print(f"  mo studio qtys: {len(studio_qtys)}")
    print(f"  size: {out_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    snapshot()
