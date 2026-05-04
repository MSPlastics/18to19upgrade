# MSPlastics Odoo 18 → 19 Upgrade

Documentation + tooling for migrating MSPlastics' Odoo Online instance from v18 to v19. **For the full story, read [V19_UPGRADE_NOTES.md](V19_UPGRADE_NOTES.md).**

## What's in here

| File / folder | Purpose |
|---|---|
| [V19_UPGRADE_NOTES.md](V19_UPGRADE_NOTES.md) | **Read this first.** Complete fix journal: every v19 break we found, the per-module fixes, the migration-level damage, the recovery process, and the post-cutover patches. |
| [PLAYBOOK.md](PLAYBOOK.md) | Original upgrade runbook (prod prep + cutover steps). |
| [tools/](tools/) | XML-RPC diagnostic scripts: check module state, force upgrade, uninstall, read logs. |
| [workflow/](workflow/) | Migration + post-cutover scripts (see below). |
| [workflow/post_migration_recovery.py](workflow/post_migration_recovery.py) | **Migration-only.** Already used 2026-05-03. Archived — re-running risks clobbering post-cutover Studio edits (Steps 4 + 5). |
| [workflow/snapshot_v18_data.py](workflow/snapshot_v18_data.py) | Pre-cutover snapshot of v18 prod data (242 packagings + 491 MO Studio qtys). Already captured. |
| [workflow/fix_qweb_v18_residue.py](workflow/fix_qweb_v18_residue.py) | **Post-cutover patcher.** Targeted, idempotent fix for stale v18 field names in Studio QWeb reports (`product_uom`/`tax_id`/`taxes_id`/`notes`/`has_packages`/`sh_*`). Run with `--target prod --commit` whenever a new v18 residue surfaces. |
| [workflow/fix_external_layout_logo.py](workflow/fix_external_layout_logo.py) | Restore dynamic `company.logo` binding in Studio-customized external layouts (replaces hardcoded `<img src="/web/image/{id}-..."/>` from old logo uploads). Caps logo at 60px. Idempotent. |
| [workflow/create_msp_sale_report.py](workflow/create_msp_sale_report.py) | **Custom MSP sale order PDF.** Idempotent — creates or updates `msp.report_saleorder_msp_v1` (ir.ui.view) + "Quotation / Order — MSP" (ir.actions.report). Re-run after editing the embedded QWEB_ARCH constant to push design changes. PDF filename = sale order number (e.g. `S01071.pdf`) via `print_report_name`. |
| [workflow/set_msp_report_on_email_templates.py](workflow/set_msp_report_on_email_templates.py) | Wire the new MSP report into the four standard sale.order email templates (Send Quotation, Order Confirmation, Order Confirmation copy, Payment Done). Pro Forma left alone. |
| [workflow/studio_arch/](workflow/studio_arch/) | Saved prod Studio view XML — the recovery script applied these during cutover. |
| [workflow/snapshots/](workflow/snapshots/) | Pre-cutover JSON dumps of v18 prod data (read by the recovery script). |
| [.env.example](.env.example) | Credential template. Copy to `.env` (gitignored), fill in `ODOO_PROD_*` and `ODOO_STAGING_*`. |

## Where the actual code lives

| Repo | Branch | Purpose |
|---|---|---|
| **MSPlastics/odoo18** | `msp_production` | Production target — LIVE on v19. Tip moves with deploys (currently `ac09fbc` after the post-cutover msp_packaging extensions). |
| **MSPlastics/odoo18** | `19_upgradetest2` | Reference branch tracking the v19 fix work. Same tip as `msp_production`. |
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
- **msp_packaging version on prod**: `19.0.1.3.0` (sale.order.line + purchase.order.line + stock.move packaging fields)

## Status

- ✅ **Cutover complete** (2026-05-03). 242 packagings + 491 MO Studio qtys restored from snapshot. 164 phantom BOMs flipped back.
- ✅ **Post-cutover Studio QWeb fixes** (2026-05-04). Sale/purchase/delivery PDFs render — see [V19_UPGRADE_NOTES.md](V19_UPGRADE_NOTES.md) "Post-cutover fixes" section for the full rule table.
- ✅ **Custom MSP sale order PDF + email send** (2026-05-04). New report `msp.report_saleorder_msp_v1` deployed on prod, wired into Send-by-email templates (Send Quotation, Order Confirmation, etc.). PDFs named after the order number (`S01071.pdf`). Filename + design + field mapping documented in [V19_UPGRADE_NOTES.md](V19_UPGRADE_NOTES.md) "Custom MSP sale order report" section.
- **Known deferred items**:
  - ZPL printing (`label_zebra_printer`) — UI loads but print path needs v19 session API migration
  - View 2442 (`studio_customization` sale order Studio report) references `doc.x_studio_*` fields wiped during migration. Inactive in render path; recreate Studio fields if MSP wants to use that variant
  - Cosmetic deprecation warnings on `product_customerinfo` (`odoo.osv` → `odoo.fields.Domain`) — works in v19, breaks in v20

## Re-running fixes

Use `workflow/fix_qweb_v18_residue.py --target prod --commit` whenever a new v18 residue surfaces (stale field name in a Studio QWeb report). It's idempotent — safe to re-run.

`workflow/post_migration_recovery.py` is **archived migration tooling**. Don't re-run on prod — Steps 4 + 5 would clobber post-cutover Studio edits and overwrite current MO data with the May 3 snapshot.
