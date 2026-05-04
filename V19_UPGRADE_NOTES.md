# MSPlastics Odoo 18 → 19 Upgrade — Complete Fix Journal

**Source repos**:
- Odoo modules: `MSPlastics/odoo18` (branches: `msp_production` for prod, `19_upgradetest2` for v19 fixes)
- Recovery tooling + docs: `MSPlastics/18to19upgrade` (this repo)

**Current state**: Production COMPLETE. Cut over 2026-05-03; post-cutover Studio QWeb report fixes shipped 2026-05-04. All custom modules load, Studio views recovered, packaging behavior restored on sale.order.line + purchase.order.line + stock.move, sale/purchase/delivery PDFs render.

---

## TL;DR — what to do at cutover

```bash
# 0. PRE-CUTOVER (BEFORE clicking the upgrade button):
#    Snapshot v18 prod data — packagings + Studio qty values.
#    Once prod is upgraded the v19 schema drops product.packaging entirely;
#    there is no way to read these back. Run NOW while v18 is still live.
cd C:/Users/Anthony/Desktop/18to19upgrade/workflow
python snapshot_v18_data.py
# -> writes workflow/snapshots/v18_prod_snapshot.json (commit it).

# 1. Click "Upgrade to 19.0" on production stage in Odoo.sh
# 2. Push our v19 fix code to production:
cd /c/msp_backups/extracted/v19audit  # or any local clone
git fetch origin
git push origin 19_upgradetest2:msp_production    # fast-forward, no merge

# 3. Wait 15–60 min for migration

# 4. After upgrade completes, run the recovery script ONCE.
#    --from-snapshot is auto-detected from snapshots/v18_prod_snapshot.json.
cd C:/Users/Anthony/Desktop/18to19upgrade/workflow
python post_migration_recovery.py --target prod --commit \
       --copy-data --copy-packagings

# 5. Restore the kit BOMs back to phantom:
python prod_disable_kits.py --restore
```

That's the full cutover. The recovery script is idempotent and handles all the v19 quirks we discovered.

---

## High-level lessons learned

1. **Odoo.sh staging branches re-run the migration on every commit** — DB-level fixes don't survive rebuilds. Everything must be either in module code or in a re-runnable recovery script.
2. **v19 removed `product.packaging` model entirely** — replaced by alternate UoMs on `uom.uom`. We re-added v18's `product.packaging` via a custom `msp_packaging` module to preserve MSP's per-product packaging workflow + sale-order warning popup.
3. **Module manifest version must start with `<series>.`** — `19.0.x.x.x` works on v19; bare `1.1.2` works (Odoo prepends); `18.0.x.x` on v19 → marked "incompatible version, setting installable=False" → cascade failure.
4. **Many "errors" in upgrade logs are actually `WARNING py.warnings`** — Python warnings printed via `warnings.warn` include a stack trace. Cosmetic only.
5. **The actual error is always at the very end of the upgrade log**. Use Ctrl+F for `CRITICAL`, `ERROR`, `Traceback`, or `ParseError`.
6. **A blank UI on a successful migration** = a custom module's JS asset throwing at load time. Always F12 → Console to find which file.
7. **Studio views drop silently if their xpaths fail** — a view referencing a v19-renamed core field will be deleted by the migration. Recover by re-creating from prod's saved arch with v19 patches.

---

## Per-module v19 fixes (commits on `19_upgradetest2`)

| Module | Issue | Fix | Commit |
|---|---|---|---|
| `msp_planning` | Imported removed `WARNING_MESSAGE`/`WARNING_HELP` constants | Inlined them | `a8b7f94` |
| `msp_planning` | `<field name="category_id">` on `res.groups` | Removed (v19 renamed to `privilege_id`) | `8a44ec9` |
| `msp_planning` | `lot_producing_id` AttributeError on MO confirm | Renamed to `lot_producing_ids` Many2many | `08dd98e` |
| `zpl_label_designer` | `category_id` on `res.groups` (security XML) | Removed | `5e519bd` |
| `zpl_label_designer` | `<field name="target">inline</field>` on `ir.actions.act_window` | Changed to `'current'` | `4696d88` |
| `product_customerinfo` | `<group expand="0" string="Group By">` in search view | Plain `<group>` | `3066e55` |
| `product_customerinfo` | View references `product_uom` on supplierinfo | Renamed to `product_uom_id` | `4f8fded` |
| `product_customerinfo` | `customer_ids` not bridged through `_inherits` to `product.product` | Added related fields on `product.product` | `c253b7a` |
| `product_customerinfo` | `name_search(name, args=...)` signature | Renamed `args=` → `domain=` | `f92c4fc` |
| `product_customerinfo_sale` | `super()._onchange_product_id_warning()` on removed v19 method | Dropped super() call | `bedf0e3` |
| `eq_cancel_mrp_orders` | `pre_init_hook` hardcoded `if serie != '18.0': raise` | Removed hook entirely + bumped manifest | `7c97f62` |
| `label_zebra_printer` | `session.user_companies.current_company` threw at JS load | Optional chained — `?.current_company` | `213191c` |
| All custom modules | Manifest versions `18.0.x.x` rejected as "incompatible" on v19 | Bumped to `19.0.x.x.x` | `1031575`, `914d386` |
| **NEW**: `msp_packaging` | v19 deleted `product.packaging` model — broke MSP's per-product packaging quantities + sale order warning workflow | Created custom module redeclaring `product.packaging`, `packaging_ids` on product, `product_packaging_id`/`product_packaging_qty` + warning logic on sale.order.line. Faithful port of v18 behavior. | `b8ea7d0` |
| Removed | `bi_partial_mrp` (nested manifest, never loaded; unused per Anthony) | Deleted | `32b24a1` |
| Removed | Top-level orphan `model/` and `report/` dirs (no manifest) | Deleted | `c38c53e` |
| Stub | `odoo_direct_print_or_download` was Apps-store-installed, code missing | Added empty stub manifest so v19 loader stops choking on it. (Anthony also uninstalled it on prod-v18 via XML-RPC — `button_immediate_uninstall`.) | `2289173` |

---

## Migration-level damage that the recovery script restores

These are problems caused by upgrade.odoo.com itself (not by our code), so they recur on every fresh migration. The recovery script (`workflow/post_migration_recovery.py`) is idempotent and handles them all.

### Lost Studio fields (mrp.production only)

Two Studio fields had `related='product_id.packaging_ids.qty'`. v19 deleted `product.packaging`, breaking the related path → migration failed to recreate them on the v19 side.

- `x_studio_qtypkg` ("Qty/pkg")
- `x_studio_finished_qtyplt` ("Finished QTY/PLT")

Recovery (Steps 1 + 3b): recreate the field records (Step 1) and, once `msp_packaging` is installed, restore `related='product_id.packaging_ids.qty'` (Step 3b). Result: same v18 behavior — both fields auto-populate from the product's first packaging qty. Downstream Studio computes (`x_studio_rollcase_count` = `product_qty / x_studio_qtypkg`) work without modification. Historical values for the ~491 MOs that pre-date a packaging record are still copied over via `--copy-data`.

### Broken `depends` on Studio computed fields

`x_studio_qr_data.depends` listed `procurement_group_id`, removed in v19. Triggered `KeyError: procurement_group_id` on every MO read.

Recovery: strip the removed field name from the depends list.

### Broken `related` paths on manual Studio fields

One Studio field on mrp.production (`x_studio_related_field_vr_1igpf4lep`, labelled "New Related Field") had `related='product_id.packaging_ids.package_type_id.display_name'`. Our minimal `msp_packaging` model omits `package_type_id` (we never use it), so this path can't be restored verbatim.

Recovery: Step 3 clears the related setting. Field exists as a plain empty char. If MSP ever uses this field, add `package_type_id = fields.Many2one('stock.package.type')` to `msp_packaging` and add it to the restore list in Step 3b.

### Studio form views deleted entirely

The migration silently DELETED:
- `Odoo Studio: mrp.production.form customization` (entire MO Studio form)
- `Odoo Studio: mrp.bom.form customization` (entire BOM Studio form)
- Most of `Odoo Studio: product.template.product.form customization` (down to a 422-char stub from 21,386 chars)

Reasons: views referenced fields that don't exist in v19 (`product_uom_category_id` on `mrp.bom.line`, `worksheet_type` on `mrp.routing.workcenter`, `finished_lot_id` on `mrp.workorder`, etc.) — view validation failed at upgrade time, the migration deleted them.

Recovery: archive of prod's view archs is in `workflow/studio_arch/`, the recovery script reapplies them with v19 patches:
- `product_uom` → `product_uom_id`
- `product_uom_category_id` references removed
- `finished_lot_id` → `finished_lot_ids` (Many2many) with `widget="many2many_tags"`
- `action_mrp_workorder_show_steps` button references stripped
- `worksheet` page (with worksheet_type/worksheet/note/worksheet_google_slide) removed entirely
- For product form: split into 24 per-xpath blocks, ~21 install successfully via "as-is" or "relaxed xpath" fallback (rigid `//form[@name='Product Template']/sheet[@name='product_form']/...` paths relaxed to `//page[...]`)

3 product-view xpath blocks fail recovery (button references for `action_open_label_layout`, `open_pricelist_rules`, `button_box/t[2]` — v19 renamed these). These are cosmetic — Anthony can re-add them in v19 Studio in ~10 min.

### Lost product.packaging data

v19 migrated 227 of 242 v18 packagings to v19's `uom.uom` records. But these UoMs don't preserve the per-product structure MSP relied on (one packaging per product per type). The `msp_packaging` module redefines `product.packaging` exactly as v18, and `--copy-packagings` migrates all 242 records into the new model. Existing v19 UoMs are left intact (harmless coexistence).

---

## Recovery script (`workflow/post_migration_recovery.py`) steps

Idempotent — safe to re-run. Designed for both staging iteration and prod cutover.

| Step | Action |
|---|---|
| **0** | Install new v19-only modules (currently just `msp_packaging`) |
| **1** | Recreate lost Studio fields on `mrp.production` |
| **2** | Strip removed-in-v19 fields from Studio compute `depends` |
| **3** | Strip broken `related` settings on manual Studio fields (idempotent) |
| **3b** | Restore `related='product_id.packaging_ids.qty'` on `x_studio_qtypkg` + `x_studio_finished_qtyplt` (only after `msp_packaging` is installed). This is what makes Studio fields auto-populate from packagings exactly like v18. |
| **3c** | Patch Studio server actions that reference v18 `lot_producing_id` (Many2one, removed in v19) to use `lot_producing_ids` (Many2many). Without this, *any* write to a tracked-by-lot MO triggers `AttributeError`, including recompute cascades from product/packaging writes. Currently patches actions 880 ("Backorder Lot") + 891 ("Force Backorder LOT to MO Name"). |
| **4** | Recreate MO + BOM + product Studio form views from saved arch |
| **4b** | (`--copy-packagings`) Port `product.packaging` records from prod |
| **5** | (`--copy-data`) Copy historical `x_studio_qtypkg`/`finished_qtyplt` values from prod |

Run on staging:
```bash
cd workflow
python post_migration_recovery.py --target staging --commit \
       --copy-data --copy-packagings
```

Run on prod after cutover (same command, different target):
```bash
python post_migration_recovery.py --target prod --commit \
       --copy-data --copy-packagings
```

⚠ **`post_migration_recovery.py` is now archive material** — production is live on v19 since 2026-05-03 and the recovery has done its job. Step 4 (recreate Studio views) and Step 5 (copy historical qtys from snapshot) would clobber post-cutover edits if re-run. For any *post-cutover* fix, write a small targeted script (see `fix_qweb_v18_residue.py` for the canonical pattern).

---

## Post-cutover fixes (2026-05-04)

Studio-customized QWeb report templates kept several v18 field names that v19 had renamed or removed. PDF rendering blew up at print time — the migration didn't catch these because views render lazily, not at install. We discovered them one at a time as users tried to print, then wrote a single comprehensive patcher.

### `workflow/fix_qweb_v18_residue.py`

Idempotent patcher that runs over every active QWeb view and applies a small rule table:

| Context | Rule |
|---|---|
| sale.order.line / purchase.order.line | `line.product_uom` → `line.product_uom_id` |
| sale.order.line | `line.tax_id` → `line.tax_ids` |
| purchase.order.line | `line.taxes_id` → `line.tax_ids` |
| purchase.order | `o.notes` → `o.note` |
| stock.picking | `o.has_packages` → `o.packages_count` (truthy in `t-if`) |
| MSP-specific | `line.sh_line_customer_code` → `line.product_customer_code` (was a third-party `sh_product_customer_code` module that's gone in v19) |
| MSP-specific | `<span line.sh_line_customer_product_name/>` deleted entirely (no v19 equivalent — Anthony chose to drop the column) |

Verified on staging, then run on prod cleanly (4 views patched: 2010, 2315, 2398, 2418). Use the same command for any future v18 residue surface that gets discovered.

### msp_packaging extensions (2026-05-04, version `19.0.1.3.0`)

v19 dropped `product.packaging` everywhere. Our v19-only `msp_packaging` initially restored it on `sale.order.line` only; we extended to mirror v18 fully:
- **`purchase.order.line`** (`19.0.1.2.0`, commit `16b0ae3`): `product_packaging_id`, `product_packaging_qty`, plus the same forward+inverse compute pattern as sale.order.line
- **`stock.move`** (`19.0.1.3.0`, commit `ac09fbc`): `product_packaging_id`, `product_packaging_qty`, `product_packaging_quantity` (v18 alias). Propagated from `sale_line_id` or `purchase_line_id` so existing v18 Studio delivery slips render.

### Method-call false positives — DON'T patch these

While building `fix_qweb_v18_residue.py` we noticed several QWeb references that *look* like missing fields but are method calls. They still work in v19. Don't add rules for them:
- `o.should_print_delivery_address()` — method on stock.picking
- `o._get_report_lang()`, `o.with_context`, `o.sudo`, `o.env` — Python attributes/methods
- `move.name` — typically inherited from base, may not appear in `ir.model.fields` queries

### Heads-up: Studio fields on sale.order in view 2442

`studio_customization.studio_report_docume_b79dd625-...` references several `doc.x_studio_*` fields that don't exist on v19 sale.order (likely wiped by the migration like the mrp.production ones were). The view is active but no `ir.actions.report` matches it directly so it's not in any active render path. Left alone for now; if MSP later wants to use a Studio sale order report variant, those fields would need to be recreated similarly to how Step 1 of the recovery handled the MO ones.

---

## Custom MSP sale order report (post-cutover, 2026-05-04)

Built and shipped a fully custom modern QWeb sale order report. Lives entirely in DB records (ir.ui.view + ir.actions.report) created via XML-RPC — not a module. The script `workflow/create_msp_sale_report.py` is idempotent and can be re-run to update the design in place.

### Records on prod

| Type | Key / id | Name |
|---|---|---|
| `ir.ui.view` (qweb) | `msp.report_saleorder_msp_v1` (id 3038) | "MSP Quotation/Order Report" |
| `ir.actions.report` | id 1083 | "Quotation / Order — MSP" |

The action has `print_report_name = "object.name"` so attached PDFs are named e.g. `S01071.pdf`, not `report.pdf`.

### Layout

Source design: `option_4_readability.html` on Anthony's desktop (May 2026).

- Top section split 68/32: left = company logo + name + 3-up address columns (Bill To / Invoice, Sold To / Branch, Ship To); right = light navy-accented panel with order metadata (Order No, Date, Expected Delivery Date, Customer PO, Drop PO, Incoterm, Terms, Acct Mgr)
- Item table: MSP PN | Description | Shipping Info | Qty | Price | Amount, zebra striped, monospaced numerics
- Totals: 280px right-aligned panel — Untaxed Amount + Tax (when nonzero) + navy TOTAL bar
- Footer: payment terms note, fiscal position remark, terms & conditions, signature

Brand palette (sampled from MSP logo): navy `#0A182F`, panel `#f1f5f9`, zebra `#f8fafc`, border `#cbd5e1`, muted `#334155`.

### Field mapping (note these — they are MSP-specific)

| Mockup label | Odoo field |
|---|---|
| MSP PN column | `line.product_id.name` (NOT `default_code`, NOT `product_customer_code` — MSP stores the internal product number in `product.product.name` like "10853") |
| Description bold | first line of `line.name` (typically the customer SKU like "SEK26243803CGB") |
| Description sub | remaining lines of `line.name` (size + pack + cust PN echo) |
| Shipping Info | `line.x_studio_freight_terms` (Char) + `line.x_studio_item_specific_freight_instructions` (Text) |
| Drop PO | `doc.msp_drop_po` (Char, "Drop PO") |
| Customer PO | `doc.client_order_ref` |
| Expected Delivery Date | `doc.commitment_date.strftime('%m/%d/%Y')` (US format) |
| Terms | `doc.payment_term_id` (renders the term's name) |
| Acct Mgr | `doc.user_id` |
| Bill To address | falls back to `commercial_partner_id` when partner_invoice_id has no street (since MSP's invoice-address children carry only the contact name, parent has the actual street/city) |
| Logo | dynamic via `image_data_uri(company.logo)`, capped 70px |

### Email Send templates

`workflow/set_msp_report_on_email_templates.py` wires the new report into the Send-by-email flow on prod (`mail.template` records):

| id | Template | Now attaches |
|---|---|---|
| 12 | Sales: Send Quotation | `msp.report_saleorder_msp_v1` |
| 13 | Sales: Order Confirmation | `msp.report_saleorder_msp_v1` |
| 14 | Sales: Payment Done | `msp.report_saleorder_msp_v1` |
| 30 | Sales: Order Confirmation (copy) | `msp.report_saleorder_msp_v1` |
| 45 | Sales: Send Proforma | left as `sale.report_saleorder_pro_forma` (intentional — different flow) |

### QWeb gotchas learned (very useful for future report work)

1. **NBSP encoding corruption** — Odoo's `widget="monetary"` outputs `<currency_symbol>&nbsp;<amount>`. wkhtmltopdf misreads the UTF-8 NBSP (`0xC2 0xA0`) as Latin-1, rendering as `Â `. Fix: format amounts manually with a regular space and Python str format:
   ```xml
   <t t-out="cur_sym + ' ' + '{:,.2f}'.format(amount)"/>
   ```
   Skip the monetary widget entirely. The standard external_layout doesn't hit this because of how it wraps content; custom layouts using `web.html_container` directly do.

2. **XML attribute newline normalization** — When you put `t-value="line.name.split('\n', 1)"` in QWeb arch (stored as XML), the XML parser normalizes the embedded newline character to a single space *before* QWeb evaluates the Python expression. So your code ends up splitting on the first space, not the first newline. Use `.splitlines()` instead:
   ```xml
   <t t-set="lines" t-value="line.name.splitlines() or ['']"/>
   <div t-out="lines[0]"/>
   <t t-foreach="lines[1:]" t-as="ln"><div t-out="ln"/></t>
   ```

3. **`t-field` auto-wraps website fields in `<a href>`** — live links in PDF attachments increase spam-folder odds. Use `t-out="company.website"` (plain text) instead of `t-field="company.website"`.

4. **Address fallback for partner children** — when `partner_invoice_id` is a child contact (e.g., "SEK Enterprise, Invoice Address") with only its own name and no street, the contact widget renders `--<name>--` and looks broken. Fall back to `commercial_partner_id` for the actual address:
   ```xml
   <t t-set="bill_addr" t-value="doc.partner_invoice_id if doc.partner_invoice_id.street else doc.partner_invoice_id.commercial_partner_id"/>
   ```
   Then render street/city/state/zip from `bill_addr` field-by-field.

5. **`print_report_name`** — Python expression on `ir.actions.report` evaluated against `object` at render time. Set to `"object.name"` so emailed PDFs are named `S01071.pdf` instead of `report.pdf`.

6. **Method-call false positives in field scans** — when scanning views for stale field references, methods like `o.with_context`, `o.sudo`, `o.should_print_delivery_address()`, `o._get_report_lang()` look like missing fields but they're methods that still exist. Don't auto-fix them.

### Iteration workflow

`create_msp_sale_report.py` is idempotent (looks the view up by key `msp.report_saleorder_msp_v1` — updates if found, creates if not). Same for the email template script (looks up by template name). Edit the QWEB_ARCH constant in the create script, re-run with `--commit`, refresh + print to see the change. Used this loop ~10 times on staging to land the final design.

---

## Production cutover playbook (current target)

### Pre-cutover (already done — verify still in place)

- [ ] Prod prep applied: 47 negative quants zeroed (`workflow/prod_zero_negatives.py`)
- [ ] Prod prep applied: 164 phantom BOMs flipped to `normal` (`workflow/prod_disable_kits.py`)
- [ ] `odoo_direct_print_or_download` uninstalled on prod-v18 (verify with `tools/check_module_state.py odoo_direct_print_or_download --target prod`)

### Cutover steps

1. **In Odoo.sh dashboard** → production stage → click **"Upgrade to 19.0"**. Confirm. Platform enters "Awaiting user commit" mode.
2. **Push the v19 fix code** to `msp_production` (fast-forward only):
   ```bash
   cd /c/msp_backups/extracted/v19audit  # or any clone of MSPlastics/odoo18
   git fetch origin
   git push origin 19_upgradetest2:msp_production
   ```
3. Odoo.sh detects the commit → starts the upgrade workflow → migration runs (15–60 min depending on prod DB size).
4. After upgrade reports complete → log in to prod and verify it loads.
5. **Run the recovery script** to restore Studio customizations + packaging data:
   ```bash
   cd C:/Users/Anthony/Desktop/18to19upgrade/workflow
   # ensure ../.env has ODOO_PROD_* set with current API key
   python post_migration_recovery.py --target prod --commit \
          --copy-data --copy-packagings
   ```
6. **Smoke-test prod**:
   - Open a product → Studio fields render, Packaging tab shows packagings
   - Open an MO → Studio sections render
   - Open a BOM → Extrusion/Printing/Converting tabs render
   - Confirm a sale order with a manufactured product → MO auto-creates
   - Type a non-multiple qty on a sale line → packaging warning popup appears
7. **Restore phantom BOMs** (kit logic):
   ```bash
   cd workflow
   python prod_disable_kits.py --restore
   ```
8. **Re-enable any users you may have disabled** for the migration window.

### Rollback (if needed)

Odoo.sh keeps automatic backups. Restore from a pre-upgrade snapshot via the Odoo.sh dashboard. The migration is non-destructive on prod data — the original v18 backup is always recoverable.

---

## Known v19 issues NOT addressed (deferred / cosmetic)

| Issue | Module | Severity |
|---|---|---|
| ZPL printing broken (`session.user_companies` undefined) — UI loads but actual print fails | `label_zebra_printer` | Medium — needs follow-up v19 session API migration |
| `_sql_constraints` deprecation warnings | `ksc_partner` | Low — cosmetic |
| `from odoo.osv import expression` deprecation | `product_customerinfo` | Low — works, will break in v20 |
| Studio field label collisions (`x_studio_X_1`, `x_studio_X`, etc.) | Various Studio fields | None — pre-existing, just verbose log warnings |
| 170 product.product Studio fields with broken related (state='base') | Pre-existing v18 orphans | None — never had data, just orphans |
| 3 product Studio form xpath blocks (button-related) failed to recover | `product.template` form | Low — re-add in v19 Studio if needed |
| BOM operations form: "Work Sheet" page lost | `mrp.bom` form | Low — v19 removed worksheet fields, can rebuild differently if needed |

---

## Critical files & locations

| Where | What |
|---|---|
| `MSPlastics/odoo18` branch `19_upgradetest2` | All v19 module fix commits (currently at `b8ea7d0`) |
| `MSPlastics/odoo18` branch `msp_production` | The prod target branch (still at `124a8e1` — fast-forward at cutover) |
| `MSPlastics/18to19upgrade` (this repo) | Recovery tooling + docs |
| `workflow/post_migration_recovery.py` | The single command that restores Studio + packaging after migration |
| `workflow/studio_arch/*.xml` | Saved prod Studio view archs (the recovery script applies these) |
| `workflow/prod_disable_kits.py` | Phantom BOM flip + restore |
| `workflow/prod_zero_negatives.py` | Negative quant cleanup |
| `tools/diag_modules.py` | XML-RPC: state of all custom modules + fields |
| `tools/check_module_state.py` | XML-RPC: single module state check |
| `tools/force_module_upgrade.py` | XML-RPC: trigger button_immediate_upgrade |
| `tools/uninstall_module.py` | XML-RPC: trigger button_immediate_uninstall |
| `MSPlastics/odoo18/msp_packaging/` | NEW: redeclares v18's `product.packaging` model + sale.order.line warning popup |

---

## Glossary of v19 changes that affected us

| v18 thing | v19 status | Notes |
|---|---|---|
| `product.packaging` model | **Removed** | Replaced by `uom.uom` with `factor`, `relative_factor`, `package_type_id`. We re-added via `msp_packaging`. |
| `product_uom` field on `product.supplierinfo` | **Renamed** to `product_uom_id` | Cascading rename to `product.customerinfo` (which inherits) |
| `product_uom_category_id` on `mrp.bom.line` | **Removed** | UoM category is now reached via `product_uom_id.category_id` directly |
| `finished_lot_id` (Many2one) on `mrp.workorder` | **Renamed** to `finished_lot_ids` (Many2many) | Multiple lots per workorder now supported |
| `lot_producing_id` (Many2one) on `mrp.production` | **Renamed** to `lot_producing_ids` (Many2many) | Same change at the MO level |
| `procurement_group_id` on `mrp.production` | **Removed** | Replaced by `reference_ids` |
| `worksheet_type`, `worksheet`, `worksheet_google_slide`, `note` on `mrp.routing.workcenter` | **Removed** | Worksheet flow restructured in v19 |
| `action_mrp_workorder_show_steps` method on `mrp.routing.workcenter` | **Removed** | Stat button no longer functional |
| `action_open_label_layout` button on product form | **Renamed/moved** | xpath fails on v19 |
| `open_pricelist_rules` button on product form | **Renamed/moved** | Same |
| `target='inline'` on `ir.actions.act_window` | **Removed Selection value** | Valid: `current/new/fullscreen/main` |
| `<group expand="0" string="Group By">` in search views | **Stricter** | Plain `<group>` now |
| `<field name="category_id">` on `res.groups` records | **Renamed** to `privilege_id` | New `res.groups.privilege` model |
| `_onchange_product_id_warning` on `sale.order.line` | **Renamed** to `_onchange_product_id` (no `_warning` suffix) | Don't `super()` to old name |
| `name_search(name, args=...)` signature | **`args` → `domain`** | Both call site and signature need updating |
| `res.groups.category_id` references in XML | **Removed** | Just delete those lines |
| `WARNING_MESSAGE`/`WARNING_HELP` from `odoo.addons.base.models.res_partner` | **Removed exports** | Inline the constants where needed |
| Deprecated `odoo.osv.expression` | Still works, deprecated | Use `odoo.fields.Domain` for v20 |
