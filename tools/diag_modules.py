"""Diagnose state of all custom modules on staging or prod.

Reports:
- Each module's state, installed_version, latest_version
- Whether key custom-module models are loadable
- Custom field presence

Usage:
    python diag_modules.py [--target prod|staging]
"""
import argparse
from _common import connect, make_caller


CUSTOM_MODULES = [
    "advanced_web_domain_widget", "customer_group_pricelist", "eg_direct_print_report",
    "eq_cancel_mrp_orders", "gt_secondary_uom", "ksc_partner", "ksc_sale",
    "label_zebra_printer", "mrp_bom_selector", "msp_planning",
    "odoo_direct_print_or_download", "prevent_customer_po_duplicate",
    "product_customerinfo", "product_customerinfo_sale", "zpl_label_designer",
]

# (module_name, model_name) — model that should exist if module loaded properly
SANITY = [
    ("product_customerinfo", "product.customerinfo"),
    ("zpl_label_designer", "zld.label"),
    ("label_zebra_printer", "label.printer"),
    ("customer_group_pricelist", "customer.group"),
    ("prevent_customer_po_duplicate", "duplicate.po.message"),
]

# (model_name, field_name) — field added by a custom module
CUSTOM_FIELDS = [
    ("sale.order.line", "product_customer_code"),       # product_customerinfo_sale
    ("sale.order", "msp_drop_po"),                       # ksc_sale
    ("product.template", "manufacture_line_warn"),       # msp_planning
    ("product.template", "customer_ids"),                # product_customerinfo
    ("product.product", "customer_ids"),                 # related field we added
    ("res.partner", "customer_number"),                  # ksc_partner
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["prod", "staging"], default="staging")
    args = parser.parse_args()

    uid, models, db, api_key = connect(args.target)
    call = make_caller(uid, models, db, api_key)

    mods = call("ir.module.module", "search_read",
                [[("name", "in", CUSTOM_MODULES)]],
                {"fields": ["name", "state", "latest_version", "installed_version"]})
    by_name = {m["name"]: m for m in mods}
    print(f"\n=== Custom module states on {args.target} ===")
    print(f"{'Module':<36} {'State':<14} {'Installed':<14} {'Latest':<14}")
    print("-" * 80)
    for name in CUSTOM_MODULES:
        info = by_name.get(name)
        if not info:
            print(f"{name:<36} <NOT FOUND>")
            continue
        print(f"{info['name']:<36} {info['state']:<14} "
              f"{str(info['installed_version']):<14} {str(info['latest_version']):<14}")

    print("\n=== Sanity reads on key custom-module models ===")
    for module, model in SANITY:
        try:
            n = call(model, "search_count", [[]], {})
            print(f"  {module:<32} -> {model:<28} OK ({n} records)")
        except Exception as e:
            print(f"  {module:<32} -> {model:<28} FAIL: {str(e)[:120]}")

    print("\n=== Custom field presence ===")
    for model, field in CUSTOM_FIELDS:
        try:
            fg = call(model, "fields_get", [[field]], {"attributes": ["string", "type"]})
            print(f"  {model}.{field}: {fg.get(field, '<missing>')}")
        except Exception as e:
            print(f"  {model}.{field}: FAIL {str(e)[:100]}")


if __name__ == "__main__":
    main()
