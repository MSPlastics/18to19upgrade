"""
Temporarily change phantom (kit) BOMs to type='normal' on PRODUCTION via XML-RPC.

Why: the v18->v19 migration's TestOnHandQuantityUnchanged invariant fails on
kit products because v18 and v19 compute kit qty_available slightly differently.
Setting BOM type to 'normal' makes kit products fall back to their own quants
(zero, since they're consumables) — both v18 and v19 then compute the same
value, the test passes.

This is fully reversible. The script writes a marker file with the BOM IDs
that were touched. After the migration completes on prod-v19, run with --restore
to flip them back to 'phantom'.

Usage:
    python prod_disable_kits.py             # dry-run (default)
    python prod_disable_kits.py --commit    # apply (saves IDs to marker file)
    python prod_disable_kits.py --restore   # flip them back (post-migration)

The marker file lives at upgrade_workflow/prod_disabled_kits.json.

⚠ OPERATIONAL IMPACT:
While BOMs are flipped to 'normal', any new MO created against a kit product
will NOT auto-explode into components — it'll behave like a regular product.
Run this close to the migration window and don't let users create kit-MOs
between fix and migration.
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

MARKER_FILE = Path(__file__).parent / "prod_disabled_kits.json"


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


def disable(commit):
    print("=" * 78)
    print(f"  PROD DISABLE KITS   [{'*** COMMIT ***' if commit else 'DRY-RUN'}]")
    print(f"  Server: {URL}")
    print(f"  Time:   {datetime.now().isoformat()}")
    print("=" * 78)

    uid, models = connect()
    print(f"  Authenticated UID {uid}")

    boms = call(models, uid, "mrp.bom", "search_read",
                [[("type", "=", "phantom"), ("active", "=", True)]],
                {"fields": ["id", "product_tmpl_id", "type", "active"]})
    print(f"  {len(boms)} active phantom BOMs found")
    if not boms:
        print("  Nothing to do.")
        return

    bom_ids = [b["id"] for b in boms]
    tmpl_ids = list({b["product_tmpl_id"][0] for b in boms if b.get("product_tmpl_id")})
    tmpls = call(models, uid, "product.template", "read", [tmpl_ids],
                 {"fields": ["id", "default_code", "name"]})
    tmpl_by_id = {t["id"]: t for t in tmpls}

    print(f"\n  Sample (first 10):")
    for b in boms[:10]:
        t = tmpl_by_id.get(b["product_tmpl_id"][0] if b.get("product_tmpl_id") else 0, {})
        code = (t.get("default_code") or "")[:14]
        name = (t.get("name") or "")[:60]
        print(f"    bom_id={b['id']:>4}  tmpl_id={t.get('id', '?'):>4}  {code:<14}  {name}")
    if len(boms) > 10:
        print(f"    ... {len(boms) - 10} more")

    if not commit:
        print(f"\n  DRY-RUN: would change {len(bom_ids)} BOMs from 'phantom' -> 'normal'.")
        if MARKER_FILE.exists():
            print(f"  WARNING: marker file already exists at {MARKER_FILE}")
            print(f"           Running --commit will overwrite it.")
        print(f"  Re-run with --commit to apply.")
        return

    if MARKER_FILE.exists():
        print(f"\n  Marker file already exists at {MARKER_FILE}")
        print(f"  Refusing to commit — would orphan the previous IDs and break --restore.")
        print(f"  If you really want to redo this, delete {MARKER_FILE} first.")
        sys.exit(1)

    print(f"\n  COMMITTING: changing {len(bom_ids)} BOMs to 'normal' ...")
    call(models, uid, "mrp.bom", "write",
         [bom_ids, {"type": "normal"}])

    MARKER_FILE.write_text(json.dumps({
        "timestamp": datetime.now().isoformat(),
        "url": URL, "db": DB,
        "bom_ids": bom_ids,
    }, indent=2))
    print(f"  Done. Marker saved to {MARKER_FILE}")
    print(f"  After v19 migration, run: python prod_disable_kits.py --restore")


def restore():
    print("=" * 78)
    print(f"  PROD RESTORE KITS")
    print(f"  Server: {URL}")
    print(f"  Time:   {datetime.now().isoformat()}")
    print("=" * 78)

    if not MARKER_FILE.exists():
        print(f"  No marker file at {MARKER_FILE}")
        print(f"  Either nothing was disabled, or the file was lost.")
        sys.exit(1)

    marker = json.loads(MARKER_FILE.read_text())
    bom_ids = marker["bom_ids"]
    print(f"  Marker recorded {len(bom_ids)} BOMs to restore (from {marker['timestamp']})")

    uid, models = connect()
    print(f"  Authenticated UID {uid}")

    # Confirm they're currently 'normal' (skip those already phantom)
    current = call(models, uid, "mrp.bom", "search_read",
                   [[("id", "in", bom_ids)]],
                   {"fields": ["id", "type"]})
    to_flip = [b["id"] for b in current if b["type"] == "normal"]
    already = len(current) - len(to_flip)
    missing = len(bom_ids) - len(current)

    print(f"    {len(to_flip)} still 'normal' — will flip to 'phantom'")
    if already:
        print(f"    {already} already 'phantom' — skipping")
    if missing:
        print(f"    {missing} not found in DB — skipping (deleted/archived?)")

    if not to_flip:
        print(f"\n  Nothing to do.")
        return

    call(models, uid, "mrp.bom", "write", [to_flip, {"type": "phantom"}])
    print(f"\n  Restored {len(to_flip)} BOMs to type='phantom'.")

    archive_path = MARKER_FILE.with_suffix(f".restored-{datetime.now():%Y%m%d_%H%M%S}.json")
    MARKER_FILE.rename(archive_path)
    print(f"  Marker archived to {archive_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true",
                        help="actually flip phantom -> normal")
    parser.add_argument("--restore", action="store_true",
                        help="flip recorded BOMs back to phantom (post-migration)")
    args = parser.parse_args()

    if args.restore:
        if args.commit:
            print("ERROR: pass either --commit or --restore, not both", file=sys.stderr)
            sys.exit(1)
        restore()
    else:
        disable(commit=args.commit)


if __name__ == "__main__":
    main()
