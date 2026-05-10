"""
Sanity-check that msp_pallet installed cleanly on staging:
- module is installed at expected version
- new fields exist on stock.quant.package and stock.picking
- 'MSP Pallet' stock.package.type record exists
- view extensions registered
"""
import os
import ssl
import sys
import xmlrpc.client
from pathlib import Path


def _load_dotenv():
    p = Path(__file__).parent.parent / ".env"
    if not p.exists(): return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
_load_dotenv()

URL = os.environ["ODOO_STAGING_URL"]; DB = os.environ["ODOO_STAGING_DB"]
USER = os.environ.get("ODOO_STAGING_USER", "admin@mountainstatesplastics.com")
KEY = os.environ["ODOO_STAGING_API_KEY"]

EXPECTED_FIELDS_PKG = {
    "msp_gross_weight_lb", "msp_length_in", "msp_width_in", "msp_height_in",
    "msp_unit_numbers_summary", "msp_finalized_at",
    "msp_dimensions_display", "msp_mo_ids", "msp_lot_ids",
}
EXPECTED_FIELDS_PICKING = {"msp_pallet_ids"}


def main():
    ctx = ssl.create_default_context()
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", context=ctx, allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    if not uid: sys.exit("auth failed")
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", context=ctx, allow_none=True)

    def call(model, method, args, kw=None):
        return models.execute_kw(DB, uid, KEY, model, method, args, kw or {})

    print("=== module state ===")
    rec = call("ir.module.module", "search_read",
               [[("name", "=", "msp_pallet")]],
               {"fields": ["state", "installed_version", "latest_version"]})
    print(f"  {rec}")
    assert rec and rec[0]["state"] == "installed", "msp_pallet not installed"

    print("\n=== stock.quant.package fields ===")
    fields = call("stock.package", "fields_get", [], {"attributes": ["string", "type"]})
    found_pkg = EXPECTED_FIELDS_PKG & set(fields.keys())
    missing_pkg = EXPECTED_FIELDS_PKG - set(fields.keys())
    for f in sorted(found_pkg):
        info = fields[f]
        print(f"  {f}: {info['type']:<12} {info['string']!r}")
    if missing_pkg:
        print(f"  MISSING: {missing_pkg}")
    assert not missing_pkg, "missing fields on stock.quant.package"

    print("\n=== stock.picking fields ===")
    fields = call("stock.picking", "fields_get", [], {"attributes": ["string", "type"]})
    for f in sorted(EXPECTED_FIELDS_PICKING):
        if f in fields:
            print(f"  {f}: {fields[f]['type']:<12} {fields[f]['string']!r}")
        else:
            print(f"  MISSING: {f}")
            sys.exit(1)

    print("\n=== MSP Pallet package_type ===")
    pts = call("stock.package.type", "search_read",
               [[("name", "=", "MSP Pallet")]],
               {"fields": ["id", "name"]})
    print(f"  {pts}")
    assert pts, "MSP Pallet stock.package.type not created"

    print("\n=== view extensions ===")
    views = call("ir.ui.view", "search_read",
                 [[("name", "in", [
                     "stock.package.form.msp",
                     "stock.picking.form.msp.pallets",
                 ])]],
                 {"fields": ["name", "model", "inherit_id"]})
    for v in views:
        print(f"  {v['name']} -> {v['model']} (inherits {v['inherit_id']})")
    assert len(views) == 2, f"expected 2 view extensions, found {len(views)}"

    print("\n=== smoke: create + read a package with new fields ===")
    pkg_type = pts[0]["id"]
    pkg_id = call("stock.package", "create",
                  [{"name": "PLT-SMOKE-TEST",
                    "package_type_id": pkg_type,
                    "msp_gross_weight_lb": 947.5,
                    "msp_length_in": 40, "msp_width_in": 48, "msp_height_in": 52,
                    "msp_unit_numbers_summary": "1-17, 19-20"}])
    print(f"  created id={pkg_id}")
    rec = call("stock.package", "read", [[pkg_id]],
               {"fields": ["name", "msp_gross_weight_lb", "msp_dimensions_display",
                           "msp_unit_numbers_summary", "msp_mo_ids", "msp_lot_ids"]})[0]
    print(f"  read back: {rec}")
    assert rec["msp_gross_weight_lb"] == 947.5
    assert rec["msp_dimensions_display"] == "40 x 48 x 52"

    # cleanup smoke pallet
    call("stock.package", "unlink", [[pkg_id]])
    print(f"  cleaned up smoke pallet {pkg_id}")

    print("\n[result] PASS - msp_pallet module fully functional")


if __name__ == "__main__":
    main()
