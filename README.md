# MSPlastics Odoo 18 → 19 Upgrade

Documentation + tooling for migrating MSPlastics' Odoo Online instance from v18 to v19. **For the full story, read [V19_UPGRADE_NOTES.md](V19_UPGRADE_NOTES.md).**

## What's in here

| File / folder | Purpose |
|---|---|
| [V19_UPGRADE_NOTES.md](V19_UPGRADE_NOTES.md) | **Read this first.** Complete fix journal: every v19 break we found, the per-module fixes, the migration-level damage, the recovery process, and the post-cutover patches. |
| [PLAYBOOK.md](PLAYBOOK.md) | Original upgrade runbook (prod prep + cutover steps). |
| [tools/](tools/) | XML-RPC diagnostic scripts: check module state, force upgrade, uninstall, read logs. |
| [workflow/](workflow/) | Migration + post-cutover scripts (see below). |

### Cutover tooling (archived — already used 2026-05-03)

| File | Purpose |
|---|---|
| [workflow/post_migration_recovery.py](workflow/post_migration_recovery.py) | **Migration-only.** Already used 2026-05-03. Archived — re-running risks clobbering post-cutover Studio edits (Steps 4 + 5). |
| [workflow/snapshot_v18_data.py](workflow/snapshot_v18_data.py) | Pre-cutover snapshot of v18 prod data (242 packagings + 491 MO Studio qtys). Already captured. |
| [workflow/snapshots/](workflow/snapshots/) | Pre-cutover JSON dumps of v18 prod data (read by the recovery script). |
| [workflow/studio_arch/](workflow/studio_arch/) | Saved prod Studio view XML — the recovery script applied these during cutover. |

### v18 residue + external layout patchers (idempotent — re-run as new residue surfaces)

| File | Purpose |
|---|---|
| [workflow/fix_qweb_v18_residue.py](workflow/fix_qweb_v18_residue.py) | **Comprehensive patcher.** Idempotent fix for stale v18 field names in Studio QWeb reports (`product_uom`/`tax_id`/`taxes_id`/`notes`/`has_packages`/`sh_*`). Run with `--target prod --commit` whenever a new v18 residue surfaces. |
| [workflow/fix_qweb_uom_v18_residue.py](workflow/fix_qweb_uom_v18_residue.py) | Older standalone patcher for `line.product_uom` residue. Superseded by `fix_qweb_v18_residue.py`; kept for reference. |
| [workflow/fix_external_layout_logo.py](workflow/fix_external_layout_logo.py) | Restore dynamic `company.logo` binding in Studio-customized external layouts (replaces hardcoded `<img src="/web/image/{id}-..."/>` from old logo uploads). Caps logo at 60px. Idempotent. |

### Studio repair patchers (idempotent — re-run as new instances surface)

| File | Purpose |
|---|---|
| [workflow/fix_studio_variant_related.py](workflow/fix_studio_variant_related.py) | Strip the redundant `product_variant_id.` prefix from Studio related paths on `product.template`. Surfaced when editing customer drop part numbers (`customer_ids.product_name`) — v19's trigger machinery fails on the non-stored variant_id field. |
| [workflow/recover_partner_shipping_instructions.py](workflow/recover_partner_shipping_instructions.py) | Re-create the inherit view that places `x_studio_shipping_instructions` inside the ksc_partner Delivery Information tab on `res.partner`. Field + 205 partner records survived migration; only the view was deleted. |
| [workflow/fix_studio_procurement_group_compute.py](workflow/fix_studio_procurement_group_compute.py) | Rewrite `record.procurement_group_id.sale_id` → `record.sale_order_id` in manual computes on `mrp.production` (v19 removed `procurement_group_id`). |

### Custom MSP report builders (idempotent — re-run after editing the embedded QWEB_ARCH constant)

| File | Purpose |
|---|---|
| [workflow/create_msp_sale_report.py](workflow/create_msp_sale_report.py) | **Custom MSP sale order PDF.** Creates / updates `msp.report_saleorder_msp_v1` (ir.ui.view) + "Quotation / Order — MSP" (ir.actions.report). PDF filename = sale order number (e.g. `S01071.pdf`) via `print_report_name`. |
| [workflow/set_msp_report_on_email_templates.py](workflow/set_msp_report_on_email_templates.py) | Wire the MSP sale order report into the four standard sale.order email templates (Send Quotation, Order Confirmation, Order Confirmation copy, Payment Done). Pro Forma left alone. |
| [workflow/create_msp_invoice.py](workflow/create_msp_invoice.py) | **Custom MSP invoice PDF** on `account.move`. Same brand styling as the sale order report, plus a Lot Number column (comma-joined per invoice line). State-aware title (Invoice / Credit Note / Draft). |
| [workflow/route_invoice_pdf_to_msp.py](workflow/route_invoice_pdf_to_msp.py) | Replace stock `account.report_invoice_with_payments` with a one-line delegate to the MSP view (the Send Invoice wizard hardcodes that report when caching `invoice_pdf_report_id`). Empties `report_template_ids` on the Invoice / Credit Note send templates so only one MSP attachment goes out per send. `--restore` reverses both. |
| [workflow/set_msp_invoice_on_email_templates.py](workflow/set_msp_invoice_on_email_templates.py) | Earlier attempt to wire the MSP invoice via `report_template_ids`. **Superseded** by `route_invoice_pdf_to_msp.py` (it produced 2 attachments per send). Kept for reference. |
| [workflow/create_msp_pick_sheet.py](workflow/create_msp_pick_sheet.py) | **Warehouse pick sheet** on `stock.picking`. Landscape 8-col, one row per `stock.move.line` so multi-lot moves split per-lot. Pallets + Weight blank for write-in. Pick Qty uses `move.quantity`. Coexists with stock Odoo reports. |
| [workflow/create_msp_delivery_slip.py](workflow/create_msp_delivery_slip.py) | **Customer-facing delivery slip** on `stock.picking`. Portrait 6-col. Bottom POD block: Shipper signature/date + Received By signature/date. Coexists with stock Odoo reports. |

### Dashboard

| File | Purpose |
|---|---|
| [workflow/create_msp_dashboard.py](workflow/create_msp_dashboard.py) | Build the **MSP Open Sales Orders** dashboard programmatically (3 list sections: open SOs, open lines with qty ordered/delivered, MOs with produced + delivered + computed Balance column). Live-bound via ODOO.LIST formulas. Idempotent — looks up by name + group. |
| [workflow/create_dashboard_filters.py](workflow/create_dashboard_filters.py) | Saved favorite `ir.filters` records as starting points for the dashboard's source lists (open orders by due date, open order lines, MOs by origin sale line). |

### Addon install + smoke tests (staging-only)

| File | Purpose |
|---|---|
| [workflow/install_and_test_msppartialMO.py](workflow/install_and_test_msppartialMO.py) | Wait for the staging Odoo.sh rebuild → install or upgrade `msppartialMO` to `EXPECTED_VERSION` → smoke-test the two MES-facing methods (`action_increment_qty_producing` exercises the `lot_producing_ids` Many2many fix; `action_ship_partial_batch` exercises the `description_picking` fix). Reads `ODOO_STAGING_*` from `.env`. Bump `EXPECTED_VERSION` each time the addon's manifest version changes. |

### Other

| File | Purpose |
|---|---|
| [.env.example](.env.example) | Credential template. Copy to `.env` (gitignored), fill in `ODOO_PROD_*` and `ODOO_STAGING_*`. |

## Where the actual code lives

| Repo | Branch | Purpose |
|---|---|---|
| **MSPlastics/odoo18** | `msp_production` | Production target — LIVE on v19. Tip moves with deploys (currently `72abe9c` after the 2026-05-07 `eq_cancel_mrp_orders` Reset-to-Draft v19 fixes, cherry-picked from staging). |
| **MSPlastics/odoo18** | `19_upgradetest2` | Staging branch. Tip `8eaf317`. Diverges from prod via the `msppartialMO` vendor at `19.0.1.1.0` (staging-verified 2026-05-09, not yet rolled to prod) plus a same-content/different-SHA pairing on the msp_packaging Packaging-tab fix. |
| **MSPlastics/msppartialMO** | `19_upgrade` | Source-of-truth for the v19-ported `msppartialMO` addon (commit `15ee20f`, version `19.0.1.1.0`). Vendored into `MSPlastics/odoo18` on `19_upgradetest2` for Odoo.sh installable rebuild. `main` and `testserverv2` still at the v18 source `ce0519d`. |
| **MSPlastics/18to19upgrade** (this repo) | `main` | Tooling + docs. |

This repo does **not** contain Odoo module code — only the operational scripts and documentation.

## Staging-first rule

**Every change goes through staging before prod.** No exceptions.

| Type of change | Staging step | Prod step |
|---|---|---|
| Module code (`MSPlastics/odoo18`) | Push to `19_upgradetest2` → wait for Odoo.sh rebuild → verify in UI on `…dev.odoo.com` | After verification: `git push origin 19_upgradetest2:msp_production` (FF only) |
| Workflow scripts that write to Odoo | Run with `--target staging --commit` → verify in UI | After verification: `--target prod --commit` |
| Studio / dashboard / report design (`create_*.py` upserters) | Same as above — staging first, click through the affected UI / print the affected PDF | Same — only after staging is confirmed working |

The 2026-05-04 `NewId` fix is the canonical example: the `_calculate_date_by_sequence` AttributeError surfaced when producing a quantity on a workorder. We caught it on staging, verified the fix there, then fast-forwarded to prod. **Don't bundle the prod step into the same proposal as the staging step** — wait for explicit "this worked" before doing the prod push.

## Quick reference

- **Production URL**: `https://msplastics-odoo18.odoo.com` — live on Odoo 19.0+e since 2026-05-03
- **msp_packaging version on prod**: `19.0.1.4.0` (sale.order.line + purchase.order.line + stock.move packaging fields, plus the 2026-05-06 `product.packaging.create()` override that derives `product_id` from `product_tmpl_id` for the product.template Packaging tab)
- **Vendored on staging only (not yet on prod)**: `msppartialMO` `19.0.1.1.0` — required by the MES central server (`action_increment_qty_producing` / `action_ship_partial_batch` / `action_close_and_backorder`). Both increment + partial-shipment paths smoke-tested against staging on 2026-05-09; close-and-backorder is static-audit-only (its wizards exist on v19 but the path wasn't directly exercised).

## Status

- ✅ **Cutover complete** (2026-05-03). 242 packagings + 491 MO Studio qtys restored from snapshot. 164 phantom BOMs flipped back.
- ✅ **Post-cutover Studio QWeb fixes** (2026-05-04). Sale/purchase/delivery PDFs render — see [V19_UPGRADE_NOTES.md](V19_UPGRADE_NOTES.md) "Post-cutover fixes" section for the full rule table.
- ✅ **Custom MSP sale order PDF + email send** (2026-05-04). New report `msp.report_saleorder_msp_v1` deployed on prod, wired into Send-by-email templates (Send Quotation, Order Confirmation, etc.). PDFs named after the order number (`S01071.pdf`). Filename + design + field mapping documented in [V19_UPGRADE_NOTES.md](V19_UPGRADE_NOTES.md) "Custom MSP sale order report" section.
- ✅ **Custom MSP invoice PDF + Send-flow rebind** (2026-05-04). `msp.report_invoice_msp_v1` deployed on prod. The stock `account.report_invoice_with_payments` wrapper view now delegates to the MSP view (so the Send Invoice wizard's cached PDF is MSP-styled). Email templates' `report_template_ids` are emptied so only one MSP attachment goes out per send.
- ✅ **MSP warehouse pick sheet + customer delivery slip** (2026-05-04). Both bound to `stock.picking`, coexist with the stock Odoo reports.
- ✅ **Studio repair patchers** (2026-05-04). Three idempotent patchers shipped for v18→v19 Studio damage that surfaces during normal usage: variant-related rewrites on product.template, ksc_partner shipping-instructions view restore, and procurement_group_id rewrites on mrp.production manual computes.
- ✅ **MSP Open Sales Orders dashboard** (2026-05-05/06). Three live-bound list sections (open SOs, open lines with qty ordered/delivered, MOs with produced + delivered + computed Balance). Built programmatically via XML-RPC in [workflow/create_msp_dashboard.py](workflow/create_msp_dashboard.py).
- ✅ **`msp_packaging` 19.0.1.4.0** (2026-05-06). `product.packaging.create()` override supplies `product_id` from `product_tmpl_id` for rows added through the product.template Packaging tab. Fixes `ValidationError: Missing required value for the field 'Product'` from the form-side write path.
- ✅ **`eq_cancel_mrp_orders` 19.0.1.2.0** (2026-05-07). Two `action_reset_to_draft` v19 fixes: (1) workorder state `'pending'` → `'blocked'` (v19 dropped `pending` from the Selection); (2) `_onchange_product_id()` and the six `_compute_*()` calls wrapped in hasattr guards (those fields are no longer computes in v19). Surfaced when resetting WH/MO/01537 to draft after cancel.
- ✅ **`msppartialMO` 19.0.1.1.0 staging-verified** (2026-05-09). Two v19 ports landed: (1) `mo.lot_producing_id` (Many2one, removed) → `mo.lot_producing_ids[:1]` (Many2many) in `action_ship_partial_batch`; (2) `stock.move.name` (removed in v19, raises `ValueError: Invalid field 'name' in 'stock.move'`) → `description_picking` (the v19 successor Text field for the move's human-readable label) in the create-vals dict. Both `action_increment_qty_producing` and `action_ship_partial_batch` smoke-tested end-to-end against MO `WH/MO/00096` on staging — increment moved state confirmed → progress, ship_partial created `WH/INT/00001` in `state=done` with the right `description_picking` and `quantity`. Pending FF to prod (waiting on explicit user instruction).
- **Known deferred items**:
  - ZPL printing (`label_zebra_printer`) — UI loads but print path needs v19 session API migration
  - View 2442 (`studio_customization` sale order Studio report) references `doc.x_studio_*` fields wiped during migration. Inactive in render path; recreate Studio fields if MSP wants to use that variant
  - Cosmetic deprecation warnings on `product_customerinfo` (`odoo.osv` → `odoo.fields.Domain`) — works in v19, breaks in v20

## Re-running fixes

Use `workflow/fix_qweb_v18_residue.py --target prod --commit` whenever a new v18 residue surfaces (stale field name in a Studio QWeb report). It's idempotent — safe to re-run.

`workflow/post_migration_recovery.py` is **archived migration tooling**. Don't re-run on prod — Steps 4 + 5 would clobber post-cutover Studio edits and overwrite current MO data with the May 3 snapshot.
