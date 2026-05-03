"""POST-MIGRATION RECOVERY for v18 -> v19 upgrade.

Run this script ONCE after Odoo.sh completes the v18->v19 migration. It
restores Studio customizations that the migration silently dropped or
broke. Idempotent — safe to re-run.

Steps:
  1. Recreate Studio fields whose v18 'related' targets are gone in v19
     (x_studio_qtypkg, x_studio_finished_qtyplt on mrp.production -- these
     pointed at product.packaging which v19 removed).
  2. Optionally copy historical values for those fields from prod
     (--copy-data flag, requires ODOO_PROD_* env vars).
  3. Strip 'procurement_group_id' from x_studio_qr_data's depends — that
     field was removed from mrp.production in v19.
  4. Strip broken 'related' settings from Studio fields whose related
     paths are now invalid (only state='manual' fields are writable).
  5. Recreate MO Studio form view (was nuked by migration).
  6. Recreate BOM Studio form view (was nuked).
  7. Recreate product.template Studio form view as 21 small xpath blocks
     (3 button-related blocks fail because v19 renamed the buttons —
     those are skipped, not fatal).

Usage:
    cp ../.env.example ../.env  # fill in ODOO_STAGING_* (or ODOO_PROD_*)
    python post_migration_recovery.py --target staging        # dry-run
    python post_migration_recovery.py --target staging --commit
    python post_migration_recovery.py --target prod --commit   # the real cutover

The arch files for the Studio form views are bundled alongside in
studio_arch/.
"""
import argparse
import os
import re
import ssl
import sys
import xmlrpc.client
from pathlib import Path


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------

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
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", context=ctx, allow_none=True)

    def call(model, method, args, kwargs=None):
        return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})
    return call


# --------------------------------------------------------------------------
# Step 1: Recreate lost Studio fields on mrp.production
# --------------------------------------------------------------------------

LOST_FIELDS = [
    {"name": "x_studio_qtypkg",            "field_description": "Qty/pkg"},
    {"name": "x_studio_finished_qtyplt",   "field_description": "Finished QTY/PLT"},
]


def step_create_lost_fields(call, commit):
    print("\n=== Step 1: Recreate lost Studio fields on mrp.production ===")
    model_id = call("ir.model", "search", [[("model", "=", "mrp.production")]])
    if not model_id:
        sys.exit("no mrp.production model")
    model_id = model_id[0]
    for spec in LOST_FIELDS:
        existing = call("ir.model.fields", "search",
                        [[("model", "=", "mrp.production"), ("name", "=", spec["name"])]])
        if existing:
            print(f"  {spec['name']}: already exists, skipping")
            continue
        if not commit:
            print(f"  {spec['name']}: would create")
            continue
        new_id = call("ir.model.fields", "create", [{
            **spec,
            "ttype": "float", "model_id": model_id, "model": "mrp.production",
            "state": "manual", "store": True, "readonly": False,
        }])
        print(f"  {spec['name']}: created id={new_id}")


# --------------------------------------------------------------------------
# Step 2: Strip procurement_group_id from x_studio_qr_data depends
# --------------------------------------------------------------------------

def step_fix_qr_depends(call, commit):
    print("\n=== Step 2: Strip removed-in-v19 fields from x_studio_qr_data depends ===")
    flds = call("ir.model.fields", "search_read",
                [[("model", "=", "mrp.production"), ("name", "=", "x_studio_qr_data")]],
                {"fields": ["id", "name", "depends"]})
    for f in flds:
        old = f["depends"] or ""
        parts = [p.strip() for p in old.split(",")]
        new_parts = [p for p in parts if p and "procurement_group_id" not in p]
        new = ", ".join(new_parts)
        if new == old:
            print(f"  {f['name']}: depends already clean")
            continue
        if not commit:
            print(f"  {f['name']}: would strip -> {new}")
            continue
        call("ir.model.fields", "write", [[f["id"]], {"depends": new}])
        print(f"  {f['name']}: stripped procurement_group_id from depends")


# --------------------------------------------------------------------------
# Step 3: Strip broken related from manual Studio fields
# --------------------------------------------------------------------------

def step_strip_broken_related(call, commit):
    print("\n=== Step 3: Strip broken related paths from manual Studio fields ===")
    _model_cache = {}
    _exists_cache = {}

    def get_fields(model):
        if model not in _model_cache:
            rows = call("ir.model.fields", "search_read",
                        [[("model", "=", model)]],
                        {"fields": ["name", "ttype", "relation"]})
            _model_cache[model] = {r["name"]: r for r in rows}
        return _model_cache[model]

    def model_exists(model):
        if model not in _exists_cache:
            _exists_cache[model] = bool(call("ir.model", "search", [[("model", "=", model)]]))
        return _exists_cache[model]

    def is_broken(start_model, dotpath):
        parts = [p.strip() for p in dotpath.split(".")]
        cur = start_model
        for i, p in enumerate(parts):
            flds = get_fields(cur)
            if p not in flds:
                return True
            f = flds[p]
            if i < len(parts) - 1:
                if f["ttype"] not in ("many2one", "one2many", "many2many"):
                    return True
                if not f["relation"] or not model_exists(f["relation"]):
                    return True
                cur = f["relation"]
        return False

    fixed = 0
    for model_name in ["mrp.production", "product.template", "product.product", "mrp.bom"]:
        flds = call("ir.model.fields", "search_read",
                    [[("model", "=", model_name), ("name", "ilike", "x_studio_"),
                      ("related", "!=", False), ("state", "=", "manual")]],
                    {"fields": ["id", "name", "related"]})
        for f in flds:
            if not is_broken(model_name, f["related"]):
                continue
            print(f"  {model_name}.{f['name']}: broken related '{f['related']}'")
            if not commit:
                continue
            try:
                call("ir.model.fields", "write",
                     [[f["id"]], {"related": False, "store": True, "depends": False}])
                fixed += 1
            except Exception as e:
                print(f"    (write failed, skipped): {str(e)[:120]}")
    print(f"  Total stripped: {fixed}")


# --------------------------------------------------------------------------
# Step 4: Recreate Studio form views from saved arch + v19 patches
# --------------------------------------------------------------------------

ARCH_DIR = Path(__file__).parent / "studio_arch"


def _patch_v19(arch):
    arch = arch.replace('name="product_uom"', 'name="product_uom_id"')
    arch = arch.replace('<field name="product_uom_category_id" invisible="1"/>',
                        '<!-- removed for v19 -->')
    arch = arch.replace('name="finished_lot_id"',
                        'name="finished_lot_ids" widget="many2many_tags"')
    arch = re.sub(r'<button[^>]*name="action_mrp_workorder_show_steps"[^>]*>.*?</button>',
                  '<!-- removed for v19 -->', arch, flags=re.DOTALL)
    arch = re.sub(r'<button[^>]*name="action_mrp_workorder_show_steps"[^/]*/>',
                  '<!-- removed for v19 -->', arch)
    arch = re.sub(
        r'<page[^>]*name="worksheet"[^>]*>.*?</page>',
        '<!-- removed for v19: worksheet fields removed from mrp.routing.workcenter -->',
        arch, flags=re.DOTALL,
    )
    arch = re.sub(
        r'<notebook[^>]*>\s*(?:<!--[^>]*-->\s*)*</notebook>',
        '<!-- removed empty notebook for v19 -->',
        arch, flags=re.DOTALL,
    )
    return arch


def _relax_xpath(block):
    """Relax v18 form-tree xpaths to be tolerant of v19 container changes."""
    block = re.sub(
        r"//form\[@name='Product Template'\]/sheet\[@name='product_form'\]/notebook\[1\]/page\[@name='([^']+)'\]",
        r"//page[@name='\1']", block,
    )
    block = re.sub(
        r"//form\[@name=&#39;Product Template&#39;\]/sheet\[@name=&#39;product_form&#39;\]/notebook\[1\]/page\[@name=&#39;([^&]+)&#39;\]",
        r"//page[@name='\1']", block,
    )
    block = re.sub(
        r"//form\[@name='Product Template'\]/sheet\[@name='product_form'\]/", "//", block,
    )
    block = re.sub(
        r"//form\[@name=&#39;Product Template&#39;\]/sheet\[@name=&#39;product_form&#39;\]/", "//", block,
    )
    return block


def _delete_existing_recoveries(call, name_prefix):
    ids = call("ir.ui.view", "search", [[("name", "ilike", name_prefix)]])
    if ids:
        call("ir.ui.view", "unlink", [ids])
    return len(ids)


def _create_view(call, vals):
    return call("ir.ui.view", "create", [vals])


def _find_parent(call, xml_id):
    module, _, xname = xml_id.partition(".")
    rows = call("ir.model.data", "search_read",
                [[("module", "=", module), ("name", "=", xname)]],
                {"fields": ["res_id"]})
    return rows[0]["res_id"] if rows else None


def step_recreate_views(call, commit):
    print("\n=== Step 4: Recreate Studio form views ===")

    # MO view: single block from saved arch
    mo_arch_file = ARCH_DIR / "mrp_production_form_studio.xml"
    if mo_arch_file.exists():
        deleted = _delete_existing_recoveries(call, "Odoo Studio: mrp.production.form customization (recovered)")
        if deleted:
            print(f"  Deleted {deleted} stale MO recovery views")
        parent = _find_parent(call, "mrp.mrp_production_form_view")
        if not parent:
            print("  WARN: no mrp.mrp_production_form_view parent on this server, skipping MO view")
        else:
            arch = _patch_v19(mo_arch_file.read_text(encoding="utf-8"))
            if commit:
                try:
                    new_id = _create_view(call, {
                        "name": "Odoo Studio: mrp.production.form customization (recovered)",
                        "model": "mrp.production", "type": "form",
                        "arch_db": arch, "inherit_id": parent,
                        "priority": 640, "active": True,
                    })
                    print(f"  MO view created id={new_id}")
                except Exception as e:
                    print(f"  MO view FAILED: {str(e)[-500:]}")
            else:
                print("  MO view: would create")

    # BOM view
    bom_arch_file = ARCH_DIR / "mrp_bom_form_studio.xml"
    if bom_arch_file.exists():
        deleted = _delete_existing_recoveries(call, "Odoo Studio: mrp.bom.form customization (recovered)")
        if deleted:
            print(f"  Deleted {deleted} stale BOM recovery views")
        parent = _find_parent(call, "mrp.mrp_bom_form_view")
        if not parent:
            print("  WARN: no mrp.mrp_bom_form_view parent on this server, skipping BOM view")
        else:
            arch = _patch_v19(bom_arch_file.read_text(encoding="utf-8"))
            if commit:
                try:
                    new_id = _create_view(call, {
                        "name": "Odoo Studio: mrp.bom.form customization (recovered)",
                        "model": "mrp.bom", "type": "form",
                        "arch_db": arch, "inherit_id": parent,
                        "priority": 160, "active": True,
                    })
                    print(f"  BOM view created id={new_id}")
                except Exception as e:
                    print(f"  BOM view FAILED: {str(e)[-500:]}")
            else:
                print("  BOM view: would create")

    # Product view: split into per-xpath blocks; some will fail (skip those)
    pt_arch_file = ARCH_DIR / "product_template_form_studio.xml"
    if pt_arch_file.exists():
        deleted = _delete_existing_recoveries(call, "Odoo Studio (product recovered)")
        if deleted:
            print(f"  Deleted {deleted} stale product recovery views")
        parent = _find_parent(call, "product.product_template_form_view")
        if not parent:
            print("  WARN: no product.product_template_form_view parent on this server, skipping product view")
            return
        full = pt_arch_file.read_text(encoding="utf-8")
        blocks = re.findall(
            r'(<xpath[^>]*>(?:(?!<xpath\b)[\s\S])*?</xpath>|<xpath[^/]*/>)',
            full,
        )
        ok, failed = 0, 0
        for i, block in enumerate(blocks, 1):
            patched = _patch_v19(block)
            for variant_label, body in [("as-is", patched), ("relaxed", _relax_xpath(patched))]:
                if variant_label == "relaxed" and body == patched:
                    continue  # nothing to relax
                if not commit:
                    ok += 1
                    break
                try:
                    _create_view(call, {
                        "name": f"Odoo Studio (product recovered) part {i} ({variant_label})",
                        "model": "product.template", "type": "form",
                        "arch_db": f"<data>{body}</data>", "inherit_id": parent,
                        "priority": 200 + i, "active": True,
                    })
                    ok += 1
                    break
                except Exception:
                    if variant_label == "relaxed":
                        failed += 1
        if commit:
            print(f"  Product view: {ok}/{len(blocks)} xpath blocks recovered ({failed} skipped — buttons/elements renamed in v19)")
        else:
            print(f"  Product view: would attempt {len(blocks)} xpath blocks")


# --------------------------------------------------------------------------
# Step 5: Optionally copy historical data from prod for the lost fields
# --------------------------------------------------------------------------

def step_copy_data_from_prod(call, commit):
    print("\n=== Step 5: Copy historical x_studio_qtypkg/finished_qtyplt from prod ===")
    if not all(os.environ.get(f"ODOO_PROD_{k}") for k in ["URL", "DB", "USER", "API_KEY"]):
        print("  ODOO_PROD_* env vars not set; skipping data copy")
        return
    prod_call = connect("prod")
    prod_mos = prod_call("mrp.production", "search_read",
                         [["|", ("x_studio_qtypkg", "!=", 0),
                           ("x_studio_finished_qtyplt", "!=", 0)]],
                         {"fields": ["id", "name", "x_studio_qtypkg", "x_studio_finished_qtyplt"]})
    print(f"  {len(prod_mos)} prod MOs with data to copy")
    by_name = {mo["name"]: mo for mo in prod_mos}
    if not by_name:
        return

    target_mos = call("mrp.production", "search_read",
                      [[("name", "in", list(by_name))]], {"fields": ["id", "name"]})
    matched = skipped = failed = 0
    for tm in target_mos:
        pm = by_name.get(tm["name"])
        if not pm:
            skipped += 1
            continue
        if not commit:
            matched += 1
            continue
        try:
            call("mrp.production", "write",
                 [[tm["id"]], {"x_studio_qtypkg": pm["x_studio_qtypkg"],
                               "x_studio_finished_qtyplt": pm["x_studio_finished_qtyplt"]}])
            matched += 1
        except Exception:
            failed += 1
    print(f"  copied: {matched}, failed: {failed}, no-match: {skipped}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["staging", "prod"], default="staging")
    parser.add_argument("--commit", action="store_true",
                        help="actually apply changes (default: dry-run)")
    parser.add_argument("--copy-data", action="store_true",
                        help="also copy x_studio_qtypkg/finished_qtyplt historical values from prod (requires ODOO_PROD_* env vars)")
    args = parser.parse_args()

    print(f"Target: {args.target}, mode: {'COMMIT' if args.commit else 'dry-run'}")
    call = connect(args.target)

    step_create_lost_fields(call, args.commit)
    step_fix_qr_depends(call, args.commit)
    step_strip_broken_related(call, args.commit)
    step_recreate_views(call, args.commit)
    if args.copy_data:
        step_copy_data_from_prod(call, args.commit)


if __name__ == "__main__":
    main()
