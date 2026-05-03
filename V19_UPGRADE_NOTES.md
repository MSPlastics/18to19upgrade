# MSPlastics Odoo 18 → 19 Upgrade — Fix Journal

**Source repo**: `MSPlastics/odoo18`, branch `19_upgradetest2` (all fixes), branch `msp_production` (prod target)
**Date range**: 2026-05-02 → 2026-05-03
**Outcome**: Staging upgrade GREEN, prod upgrade pending re-trigger
**Last commit**: `213191c` (label_zebra_printer JS fix)

---

## High-level lessons learned

1. **`gt_secondary_uom` was uninstalled before we started** — it's the only custom module not in any failure list. Don't delete it from the repo.
2. **Each Odoo.sh "Upgrade" iteration uses a snapshot of the prod backup taken at trigger time.** If you uninstall a module on prod between iterations, the next iteration may still use an older backup that has it as installed. Solution we used: empty stub module.
3. **Module version format must start with `<series>.`** — manifest version `19.0.1.1.2` works on v19; bare `1.1.2` works (Odoo prepends); `18.0.x.x` on v19 → marked "incompatible version, setting installable=False" → cascading failure.
4. **The really long stack-trace logs that look like errors are actually `WARNING py.warnings`** — Python warnings printed via `warnings.warn` include a stack trace. They're not fatal.
5. **The actual error is always at the very end of the upgrade log**, often a single line like `ParseError: ...` or `ValueError: ...`. Use Ctrl+F for `CRITICAL`, `ERROR`, `Traceback`, or `ParseError`.
6. **A successful migration test boot ends with**: `Modules loaded.` → `Registry loaded in X.Xs` → `Initiating shutdown`. That's not a crash — it's the normal end of `--stop-after-init`.
7. **A blank UI on a migration that succeeded** = JS asset bundle failed to compile due to a custom module's JS. Always F12 → Console → look for the throwing import.
8. **Prod upgrade rolled back on first try** because the migration ran with code that hadn't been tested on a fresh v18→v19 path. Lesson: always iterate on a staging branch with the *exact same* code that will be on `msp_production` at trigger time.

---

## Per-module fixes

### `msp_planning`

**Issue 1**: Imported `WARNING_MESSAGE`, `WARNING_HELP` from `odoo.addons.base.models.res_partner` — removed in v19.
**Fix**: Inlined the constants into [msp_planning/models/product_template.py](https://github.com/MSPlastics/odoo18/blob/19_upgradetest2/msp_planning/models/product_template.py).
**Commit**: `a8b7f94`

**Issue 2**: `<field name="category_id" ref="base.module_category_hidden"/>` on `res.groups` — `category_id` removed/renamed in v19.
**Fix**: Removed the line from `msp_planning/security/mrp_security.xml`.
**Commit**: `8a44ec9`

---

### `zpl_label_designer`

**Issue 1**: Same `category_id` on `res.groups` issue in `security/security.xml`.
**Fix**: Removed the two `<field name="category_id">` lines.
**Commit**: `5e519bd`

**Issue 2**: View used `<field name="target">inline</field>` on `ir.actions.act_window` — `'inline'` is no longer a valid Selection value in v19. Valid: `current/new/fullscreen/main`.
**Fix**: Changed to `current` in `views/res_config_settings_views.xml`.
**Commit**: `4696d88`

---

### `product_customerinfo`

**Issue 1**: Search view in `<group>` had `expand="0"` and `string="Group By"` attributes — removed in v19 search view groups.
**Fix**: Plain `<group>` in `views/product_views.xml`.
**Commit**: `3066e55`

**Issue 2**: Field renamed v18 `product_uom` → v19 `product_uom_id` (via `product.supplierinfo` inheritance). View still referenced old name.
**Fix**: Renamed in form view at `views/product_views.xml`.
**Commit**: `4f8fded`

**Issue 3**: `customer_ids` and `variant_customer_ids` are One2many fields on `product.template`. v19 stopped auto-bridging through `_inherits` to `product.product`. Search/form views referencing them on `product.product` failed with OwlError.
**Fix**: Added related fields on `product.product`:
```python
customer_ids = fields.One2many(related="product_tmpl_id.customer_ids", string="Customer")
variant_customer_ids = fields.One2many(related="product_tmpl_id.variant_customer_ids", string="Variant Customer")
```
**Commit**: `c253b7a`

**Manifest bumps**: `535f9d9`, `0c61265`, `4a83601` (each retrying upgrade trigger)

---

### `product_customerinfo_sale`

**Issue**: After `product_customerinfo` was upgraded, dependent module needed manifest bump to re-load.
**Fix**: Bumped to `19.0.1.0.0` in `__manifest__.py`.
**Commit**: `124a8e1`

---

### `eq_cancel_mrp_orders`

**Issue**: `__init__.py` had a `pre_init_hook` that hardcoded `if serie != '18.0': raise ValidationError("This module support in odoo version 18.")`. Manifest referenced this hook, so module was unloadable on v19.
**Fix**:
1. Removed the v18-only check from `__init__.py`.
2. Removed `'pre_init_hook': 'module_install_hook'` from manifest.
3. Bumped manifest version to `19.0.1.0`.
**Commit**: `7c97f62`

---

### Manifest version bumps (chore)

Multiple modules had manifests still on `18.0.x.x` which v19 marks as "incompatible version → installable=False". Bumped:
- `advanced_web_domain_widget` → `19.0.1.1.2`
- `label_zebra_printer` → `19.0.1.0`
- `mrp_bom_selector` → `19.0.1.1.0`
- `prevent_customer_po_duplicate` → `19.0.0.1` (also added missing `'installable': True`)
- `zpl_label_designer` → `19.0.1.3.3` (and later bumps to force iteration retries)

**Commits**: `1031575`, `914d386`, `cdd3a17`, `4de3c4d`, `007bfc3`

---

### `label_zebra_printer`

**Issue**: `static/src/js/utils.js` line 10 — `var company_id = session.user_companies.current_company` threw `TypeError: Cannot read properties of undefined (reading 'current_company')` at JS module load time on v19. This broke the entire JS asset bundle → blank UI even after a successful migration.
**Fix**: Added optional chaining: `session.user_companies?.current_company`.
**Commit**: `213191c`
**Caveat**: ZPL printing functionality is broken on v19 because `company_id` will be `undefined`. Need separate v19-API migration for the print path (likely use `@web/core/user`'s `user.activeCompany.id`).

---

### `odoo_direct_print_or_download` (third-party Apps store module)

**Issue**: Module was installed via Apps store on prod-v18 but no source code in our repo. v19 migration kept choking on "module not installable, skipped" → "Some modules have inconsistent states" → caused prod upgrade rollback.
**Fix attempts**:
1. Uninstalled from prod-v18 via XML-RPC `button_immediate_uninstall` (works going forward)
2. Odoo.sh's older backups still had it installed → built an empty stub module at `odoo_direct_print_or_download/` with just a manifest and empty `__init__.py` to satisfy the loader.
**Commits**: prod uninstall (no commit, XML-RPC), `2289173` (stub).
**Caveat**: Stub can be removed from the repo once prod's automatic backup propagates the uninstalled state through Odoo.sh's backup rotation.

---

### Removed modules (cleanup)

- **`bi_partial_mrp/`** — was nested at `bi_partial_mrp-18.0.0.0/bi_partial_mrp/` so Odoo never actually loaded it. Anthony confirmed unused. Deleted. Commit: `32b24a1`.
- **Top-level `model/` and `report/` dirs** — orphan code from an older zpl_label_designer install, no manifest, not loaded by any module. Deleted. Commit: `c38c53e`.

---

## v19 issues NOT yet addressed (deferred / non-blocking)

### `ksc_partner` — model registry warnings (cosmetic)

```
WARNING: Model attribute '_sql_constraints' is no longer supported, please define models.Constraint on the model.
WARNING: The model receiving.weekday.days has no _description
```

Module loads fine, just emits warnings. Should fix for code hygiene but not blocking.

### `ksc_sale` — old `view_type: 'form'` action keys

Action dicts in `models/product.py` and `models/product_supplierinfo.py` return `'view_type': 'form'` — deprecated since v15, ignored in v19. Should clean up.
Also has `'auto_install': True` which is risky.

### `product_customerinfo` — deprecated `odoo.osv` import

```
DeprecationWarning: Since 19.0, odoo.osv is deprecated use odoo.fields.Domain
```

Used in `models/product_product.py` and `models/product_template.py` (`from odoo.osv import expression`). Still works in v19, but should migrate to `odoo.fields.Domain` for v20.

### `eg_direct_print_report` — manifest version `18.3` (no leading `<series>.`)

Worked in v19 because Odoo prepended `19.0.` to make `19.0.18.3`. Cosmetic only.

### `label_zebra_printer` — print functionality

The optional-chain fix gets the UI to load, but `company_id` is `undefined` so the actual print flow needs migration to v19 session API.

### Studio field label collisions

Lots of WARNINGs like `Two fields (x_studio_X_1, x_studio_X) of product.template() have the same label`. These are from old Studio customizations creating duplicate fields. They don't break anything — just confusing in the UI. Can be cleaned via Settings → Technical → Database Structure → Models.

---

## Production upgrade re-trigger plan

When ready (after staging smoke-test confirms all critical paths work):

1. **In Odoo.sh dashboard → Production stage → click "Upgrade to 19.0"**
2. **Push `19_upgradetest2` to `msp_production`**:
   ```
   cd /c/msp_backups/extracted/v19audit
   git push origin 19_upgradetest2:msp_production
   ```
   This will be a fast-forward (no merge conflicts). msp_production goes from `124a8e1` → `213191c`.
3. **Wait** for migration (~15-60 min depending on prod DB size)
4. **After staging URL works**, smoke-test prod
5. **Run** `python upgrade_workflow/prod_disable_kits.py --restore` to flip the 164 phantom BOMs back

## Pre-prod-cutover checklist

- [ ] Verify prod prep is still applied: `python upgrade_workflow/prod_disable_kits.py` (should show 0 active phantom BOMs)
- [ ] Verify zero negatives: `python upgrade_workflow/prod_zero_negatives.py` (should show 0 negative quants)
- [ ] Verify direct_print is uninstalled on prod: `python tools/check_prod_module.py odoo_direct_print_or_download` (should show state=uninstalled)
- [ ] Verify staging URL is responsive and you can navigate Products / Sales / MOs / Inventory
- [ ] Have `prod_disable_kits.py --restore` ready to run after migration
