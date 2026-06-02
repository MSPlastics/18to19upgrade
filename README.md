# MSPlastics Odoo 18 → 19 Upgrade

Documentation + tooling for migrating MSPlastics' Odoo Online instance from v18 to v19. **For the full story, read [V19_UPGRADE_NOTES.md](V19_UPGRADE_NOTES.md).**

## What's in here

| File / folder | Purpose |
|---|---|
| [V19_UPGRADE_NOTES.md](V19_UPGRADE_NOTES.md) | **Read this first.** Complete fix journal: every v19 break we found, the per-module fixes, the migration-level damage, the recovery process, and the post-cutover patches. |
| [PALLET_SHIPPING_PLAN.md](PALLET_SHIPPING_PLAN.md) | Pre-implementation plan (drafted 2026-05-09) for pallet-based shipping (`stock.package` + `msp_pallet` addon + MES kiosk-scale flow). Architecture, schema, decisions, phased build, open questions. Built + staging-verified end of "Implementation Status" section. |
| [PLAYBOOK.md](PLAYBOOK.md) | Original upgrade runbook (prod prep + cutover steps). |
| [STAGING_TO_PROD_RUNBOOK.md](STAGING_TO_PROD_RUNBOOK.md) | **Post-cutover deployment runbook.** Order-of-operations to move all the staging-verified MSP customizations (msp_pallet addon, msppartialMO v19 port, MES code, 6 QWeb reports, Clear Repro lot fix, MES silo re-binding) onto production. Each phase reversible, gated on prior phase verification. |
| [AUDIT_PROCEDURE.md](AUDIT_PROCEDURE.md) | End-to-end SO→invoice audit rubric. Drives the `workflow/audit/` pipeline. v19 schema rename cheat sheet + per-phase pass criteria + common gotchas. |
| [AUDIT_2026-05-09_11158.md](AUDIT_2026-05-09_11158.md) | Original lifecycle audit on product 11158. All 10 stages PASS. Surfaced 8 findings (silo lot drift, reservation strategy, blend expansion, etc.). |
| [AUDIT_2026-05-10_11158_fixverify.md](AUDIT_2026-05-10_11158_fixverify.md) | Fix-verification audit cycle confirming MES `ac919b1` resolves silo + reservation findings. All 10 stages PASS without manual workarounds. |
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

### Snapshot + drift safety net for MSP reports

The `create_msp_*.py` upserters embed `QWEB_ARCH` as a Python string constant — the **scripts in this repo are the source of truth for the QWeb arch**. But Odoo.sh treats staging branches as throw-away, so if someone hand-edits a view via Studio / dev mode on staging and forgets to mirror the change back into the script, that edit is lost when the staging branch dies.

Two tools mitigate this risk:

| File | Purpose |
|---|---|
| [workflow/snapshot_msp_reports.py](workflow/snapshot_msp_reports.py) | Pull the as-deployed `ir.ui.view` arch + `ir.actions.report` config for all 5 MSP reports from a target Odoo and write them to `workflow/snapshots/qweb_reports/<target>/`. Commit the JSON files — they're the disaster-recovery copy if Odoo.sh deletes the branch. Run `--target staging` after any deploy; run `--target prod` after each prod rollout. |
| [workflow/diff_msp_reports.py](workflow/diff_msp_reports.py) | Compare the script's `QWEB_ARCH` constant vs the live Odoo `ir.ui.view.arch_db` for each report. Uses lxml to canonicalize XML before comparing so it ignores benign Odoo serializer noise (`>` vs `&gt;`, `<x></x>` vs `<x/>`, attribute quote style). Flags REAL semantic drift only. Run before any deploy with `--target staging` to catch divergence. |

Workflow:

```bash
# Before deploying to prod, confirm staging matches our scripts:
python workflow/diff_msp_reports.py --target staging --summary-only
# If "no drift" -> safe to push scripts to prod.
# If drift -> someone hand-edited staging; either reconcile back into the script
#   or accept the live arch by snapshotting + manually copying into the script's
#   QWEB_ARCH constant before deploying.

# After a deploy, snapshot the new state:
python workflow/snapshot_msp_reports.py --target staging
git add workflow/snapshots/qweb_reports/
git commit -m "snapshot: MSP reports as-deployed on staging YYYY-MM-DD"
```

The snapshots also serve as a static fallback: if the `create_msp_*.py` script ever has a bug and corrupts the QWEB_ARCH, the snapshot JSON has the last-good arch you can paste back.

### Custom MSP report builders (idempotent — re-run after editing the embedded QWEB_ARCH constant)

| File | Purpose |
|---|---|
| [workflow/create_msp_sale_report.py](workflow/create_msp_sale_report.py) | **Custom MSP sale order PDF.** Creates / updates `msp.report_saleorder_msp_v1` (ir.ui.view) + "Quotation / Order — MSP" (ir.actions.report). PDF filename = sale order number (e.g. `S01071.pdf`) via `print_report_name`. |
| [workflow/set_msp_report_on_email_templates.py](workflow/set_msp_report_on_email_templates.py) | Wire the MSP sale order report into the four standard sale.order email templates (Send Quotation, Order Confirmation, Order Confirmation copy, Payment Done). Pro Forma left alone. |
| [workflow/create_msp_invoice.py](workflow/create_msp_invoice.py) | **Custom MSP invoice PDF** on `account.move`. Same brand styling as the sale order report, plus a Lot Number column (comma-joined per invoice line). State-aware title (Invoice / Credit Note / Draft). |
| [workflow/route_invoice_pdf_to_msp.py](workflow/route_invoice_pdf_to_msp.py) | Replace stock `account.report_invoice_with_payments` with a one-line delegate to the MSP view (the Send Invoice wizard hardcodes that report when caching `invoice_pdf_report_id`). Empties `report_template_ids` on the Invoice / Credit Note send templates so only one MSP attachment goes out per send. `--restore` reverses both. |
| [workflow/set_msp_invoice_on_email_templates.py](workflow/set_msp_invoice_on_email_templates.py) | Earlier attempt to wire the MSP invoice via `report_template_ids`. **Superseded** by `route_invoice_pdf_to_msp.py` (it produced 2 attachments per send). Kept for reference. |
| [workflow/create_msp_pick_sheet.py](workflow/create_msp_pick_sheet.py) | **Warehouse pick sheet** on `stock.picking`. Unified per-pallet checklist (sorted by `-PAL-N`), contents column shows `product x qty UoM | lot LOT_NAME` per row, packaging-aware UoM conversion (Thousands/Lbs → Roll/Case via product.packaging), Order Summary at bottom matching pick order, Grand Total per-UoM. Iterated heavily during the pallet shipping build. Coexists with stock Odoo reports. |
| [workflow/create_msp_delivery_slip.py](workflow/create_msp_delivery_slip.py) | **Customer-facing delivery slip** on `stock.picking`. Portrait 6-col. Bottom POD block: Shipper signature/date + Received By signature/date. Coexists with stock Odoo reports. |
| [workflow/create_msp_pallet_sheet.py](workflow/create_msp_pallet_sheet.py) | **Per-pallet sheet** on `stock.package`. One-page summary: pallet ID + QR, MO + product + dims, gross weight, contents (lot/qty per move_line), finalize timestamp. Phase 6 of `PALLET_SHIPPING_PLAN.md`. Operators print from the package form. |

### Dashboard

| File | Purpose |
|---|---|
| [workflow/create_msp_dashboard.py](workflow/create_msp_dashboard.py) | Build the **MSP Open Sales Orders** dashboard programmatically (3 list sections: open SOs, open lines with qty ordered/delivered, MOs with produced + delivered + computed Balance column). Live-bound via ODOO.LIST formulas. Idempotent — looks up by name + group. |
| [workflow/create_dashboard_filters.py](workflow/create_dashboard_filters.py) | Saved favorite `ir.filters` records as starting points for the dashboard's source lists (open orders by due date, open order lines, MOs by origin sale line). |

### Addon install + smoke tests (staging-only)

| File | Purpose |
|---|---|
| [workflow/install_and_test_msppartialMO.py](workflow/install_and_test_msppartialMO.py) | Wait for the staging Odoo.sh rebuild → install or upgrade `msppartialMO` to `EXPECTED_VERSION` → smoke-test the two MES-facing methods (`action_increment_qty_producing` exercises the `lot_producing_ids` Many2many fix; `action_ship_partial_batch` exercises the `description_picking` fix). Reads `ODOO_STAGING_*` from `.env`. Bump `EXPECTED_VERSION` each time the addon's manifest version changes. |
| [workflow/setup_mo_1583_lot_test.py](workflow/setup_mo_1583_lot_test.py) | Seed staging for the MO 1583 / `WH/MO/01479` (5-Layer extrusion, multi-step) end-to-end lot test: enable lot tracking on each raw material, create a fresh `TEST-2026-MM-DD-...` `stock.lot`, ensure positive `WH/Stock` quants, then load 4 MES silos (Butene1-BF / Frac1-A / Color Repro / Exceed 1012RA) and 3 line-inventory rows on the 5 Layer line (conANTIBLOCK clarity / con-brown1 / conSLIP fast). Idempotent. Reads `ODOO_STAGING_*` and `MES_TEST_*` from `.env`. |
| [workflow/test_mo_1583_forward.py](workflow/test_mo_1583_forward.py) | Forward-test the operatorUI -> MES -> Odoo consumption flow at the **extrusion step** (multi-step Step 1, master rolls = WIP). POST a 100 lb roll to the cloud test MES `/api/v1/production/roll`, poll Odoo until the 7 expected raw `stock.move.line` records appear, verify each material's aggregated qty + lot match the operator-reported case weight × blend ratios. |
| [workflow/test_mo_1583_converting.py](workflow/test_mo_1583_converting.py) | Convert-step test for MO 1583 (multi-step Step 2, Amutech BPA WC). Submits a converting roll with `current_step_seq=2` + `source_roll_id` pointing at one of the step-1 master rolls. Verifies BOX + Label consumed, `qty_producing` increments, and a new partial-shipment internal transfer (`Partial Shipment: <wo>`) lands in `state=done`. Resin moves are NOT re-consumed (already done in step 1). |
| [workflow/setup_mo_93_inline_test.py](workflow/setup_mo_93_inline_test.py) | Seed staging for MO 93 / `WH/MO/00094` (single-step Inline on Line 6 6" Davis). Picks up where `setup_mo_1583_lot_test.py` left off — enables lot tracking + creates a test lot for `conSLIP slow` (id 42) and `con-Antiblock/slip` (id 579, the legacy combined additive that the MES blend recipe still references), loads a silo for the legacy product, and sets up line_inventory on Line 6 (wc_id=5). |
| [workflow/test_mo_93_inline.py](workflow/test_mo_93_inline.py) | Inline single-step test (single workorder = produces FG directly from extrusion). Verifies that the `is_extrusion = (total_steps==1)` path fires, resin gets distributed by hopper percentages, BOX + Label consume in the SAME pass, and the FG block fires (`qty_producing` advances, partial-ship picking created). Flags blend-vs-BOM data drift if certain additives don't match. |
| [workflow/view_mo_consumption.py](workflow/view_mo_consumption.py) | Backward-verification viewer: given an MO id, prints the full raw-consumption history (move-by-move with timestamps and lots), the `lot_producing_ids` on the MO, the FG move state, and a `Material -> Lot` rollup so you can see exactly which raw lots fed the WO. The "open a work order, see what raw lot was consumed" check. |
| [workflow/test_mo_1583_outbound.py](workflow/test_mo_1583_outbound.py) | Outbound delivery / shipping verification. Submits N converting rolls (1 Case each) and confirms that as production grows: (a) the FG lot's `stock.quant` rows at WH/Stock grow, (b) the originating sale order's outgoing delivery picking auto-reserves the new stock (Odoo reservation engine), (c) the suggested `move_line.lot_id` on the delivery is the MO's FG lot, and (d) all available on-hand qty is reserved. |

### End-to-end audit pipeline ([workflow/audit/](workflow/audit/))

State-driven SO→MO→production→pick→ship→invoice→trace lifecycle audit. State persists in `audit_state.json` so each script picks up where the prior left off. Read the rubric at [AUDIT_PROCEDURE.md](AUDIT_PROCEDURE.md) before running. Successfully exercised twice on product 11158, ~1 hr per cycle. Reusable for any product category.

| File | Purpose |
|---|---|
| [workflow/audit/_common.py](workflow/audit/_common.py) | Shared XMLRPC + MES HTTP helpers, JSON-backed state, timestamped logging |
| [workflow/audit/probe_product.py](workflow/audit/probe_product.py) | Read-only product / BOM / UoM / packaging / route probe (v19 schema-aware) |
| [workflow/audit/find_product.py](workflow/audit/find_product.py) | Search Odoo product by partial code / name / barcode |
| [workflow/audit/setup_silos.py](workflow/audit/setup_silos.py) | Idempotently bind MES silos to **real** Odoo `stock.lot` records (creates Clear Repro lot via inventory adjustment if missing) |
| [workflow/audit/00_baseline.py](workflow/audit/00_baseline.py) | Lock product baseline + write expected-results block to per-run report |
| [workflow/audit/01_create_so.py](workflow/audit/01_create_so.py) | Create + confirm SO; auto-trigger MES `/api/sync` so the new MO is visible |
| [workflow/audit/02_verify_mo_sync.py](workflow/audit/02_verify_mo_sync.py) | Verify Odoo workorders + MES `/api/work-orders` see the MO with right metadata |
| [workflow/audit/drive_production.py](workflow/audit/drive_production.py) | Subcommands: `extrude` (post N MR rolls) / `advance` (button_finish MR WO) / `convert` (post FG rolls) / `finalize-mo` (mark MO done) / `build-pallets` (post + finalize via API) |
| [workflow/audit/03_observe_production.py](workflow/audit/03_observe_production.py) | One-shot snapshot, `--watch` polling, or `--finalize` to write Phase 4 PASS/FAIL |
| [workflow/audit/04_verify_pallets.py](workflow/audit/04_verify_pallets.py) | Verify Odoo `stock.package` records + `msp_*` fields + quants per pallet |
| [workflow/audit/wire_packages_to_picking.py](workflow/audit/wire_packages_to_picking.py) | **Workaround now obsolete** after MES `ac919b1`: manually re-wire outbound move_lines to packages. Reconcile sync now does this automatically. Kept as fallback. |
| [workflow/audit/05_verify_pick_sheet.py](workflow/audit/05_verify_pick_sheet.py) | Verify QWeb data the pick sheet would render (PDF render is manual; v19 made `_render_qweb_pdf` private to RPC) |
| [workflow/audit/06_verify_shipping.py](workflow/audit/06_verify_shipping.py) | `button_validate` outbound + verify FG lot persists, no FIFO substitution, optional backorder |
| [workflow/audit/07_verify_invoice.py](workflow/audit/07_verify_invoice.py) | Create invoice via `sale.advance.payment.inv` wizard, verify draft (left as draft per audit policy) |
| [workflow/audit/08_trace_lot.py](workflow/audit/08_trace_lot.py) | Bidirectional lot trace: FG lot → raw lots; raw lot → MO → FG lot → delivery → customer |
| [workflow/audit/99_finalize_report.py](workflow/audit/99_finalize_report.py) | Append summary section + duration + findings + recommendations |
| [workflow/audit/reset_audit_state.py](workflow/audit/reset_audit_state.py) | Cancel SO+MO + drop run-specific state keys + strip Phase blocks for clean re-run |

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
- ✅ **MSP invoice — payments + balance section** (2026-06-02). `msp.report_invoice_msp_v1` now prints each reconciled entry below TOTAL — "Paid on" (cash payments) / "Reversed on" (credit-note reversals), dated — plus an **Amount Due** line, so a settled invoice shows a `$0.00` balance for the accountant. Sourced from `account.move.invoice_payments_widget['content']` (v19 **removed** the old `_get_reconciled_info_JSON_values()` helper, and QWeb's eval sandbox has no `hasattr` — both surfaced as render errors during the staging iteration). Deployed + render-verified on staging (`32926658`) and **prod** (`16023658`); the prod API key was single-use and deleted right after.
- ✅ **MSP warehouse pick sheet + customer delivery slip** (2026-05-04). Both bound to `stock.picking`, coexist with the stock Odoo reports.
- ✅ **Studio repair patchers** (2026-05-04). Three idempotent patchers shipped for v18→v19 Studio damage that surfaces during normal usage: variant-related rewrites on product.template, ksc_partner shipping-instructions view restore, and procurement_group_id rewrites on mrp.production manual computes.
- ✅ **MSP Open Sales Orders dashboard** (2026-05-05/06). Three live-bound list sections (open SOs, open lines with qty ordered/delivered, MOs with produced + delivered + computed Balance). Built programmatically via XML-RPC in [workflow/create_msp_dashboard.py](workflow/create_msp_dashboard.py).
- ✅ **`msp_packaging` 19.0.1.4.0** (2026-05-06). `product.packaging.create()` override supplies `product_id` from `product_tmpl_id` for rows added through the product.template Packaging tab. Fixes `ValidationError: Missing required value for the field 'Product'` from the form-side write path.
- ✅ **`eq_cancel_mrp_orders` 19.0.1.2.0** (2026-05-07). Two `action_reset_to_draft` v19 fixes: (1) workorder state `'pending'` → `'blocked'` (v19 dropped `pending` from the Selection); (2) `_onchange_product_id()` and the six `_compute_*()` calls wrapped in hasattr guards (those fields are no longer computes in v19). Surfaced when resetting WH/MO/01537 to draft after cancel.
- ✅ **`msppartialMO` 19.0.1.1.0 staging-verified** (2026-05-09). Two v19 ports landed: (1) `mo.lot_producing_id` (Many2one, removed) → `mo.lot_producing_ids[:1]` (Many2many) in `action_ship_partial_batch`; (2) `stock.move.name` (removed in v19, raises `ValueError: Invalid field 'name' in 'stock.move'`) → `description_picking` (the v19 successor Text field for the move's human-readable label) in the create-vals dict. Both `action_increment_qty_producing` and `action_ship_partial_batch` smoke-tested end-to-end against MO `WH/MO/00096` on staging — increment moved state confirmed → progress, ship_partial created `WH/INT/00001` in `state=done` with the right `description_picking` and `quantity`. Pending FF to prod (waiting on explicit user instruction).
- ✅ **End-to-end lot tracking forward + backward + outbound chain verified on staging** (2026-05-09). 4 MES sync-path defects fixed (commits on `MSPlastics/MESv1.0:master`: `71998a8`, `67d81c5`, `98b5362`, `6a5bf3d`) — see V19_UPGRADE_NOTES.md "MES sync-path lot-tracking fixes" section for the full chain. Verified live on MO `WH/MO/01479` (5-Layer multi-step) and MO `WH/MO/00094` (single-step Inline): the operator-reported case weight × layer × hopper percentages drives raw consumption to Odoo with the correct silo/line lot per material; the FG produced lands at WH/Stock under the MO-level lot (`MO/01479-001`); the outgoing delivery picking auto-reserves the new stock with that lot. Verified split-delivery: original `WH/OUT/01241` shipped 7 Cases done, backorder `WH/OUT/01336` auto-reserved the next 3 Cases produced, both showing `MO/01479-001` as the suggested lot — no FIFO mistakes. See `workflow/test_mo_1583_*.py`, `test_mo_93_inline.py`, `view_mo_consumption.py`. **Pending: customer-paperwork PDFs not yet rendered post-test (delivery slip / pick sheet text contents have data, just haven't been visually printed).**
- **Known deferred items**:
  - ZPL printing (`label_zebra_printer`) — UI loads but print path needs v19 session API migration
  - View 2442 (`studio_customization` sale order Studio report) references `doc.x_studio_*` fields wiped during migration. Inactive in render path; recreate Studio fields if MSP wants to use that variant
  - Cosmetic deprecation warnings on `product_customerinfo` (`odoo.osv` → `odoo.fields.Domain`) — works in v19, breaks in v20

## Re-running fixes

Use `workflow/fix_qweb_v18_residue.py --target prod --commit` whenever a new v18 residue surfaces (stale field name in a Studio QWeb report). It's idempotent — safe to re-run.

`workflow/post_migration_recovery.py` is **archived migration tooling**. Don't re-run on prod — Steps 4 + 5 would clobber post-cutover Studio edits and overwrite current MO data with the May 3 snapshot.
