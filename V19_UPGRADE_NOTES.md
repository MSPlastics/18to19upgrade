# MSPlastics Odoo 18 → 19 Upgrade — Complete Fix Journal

**Source repos**:
- Odoo modules: `MSPlastics/odoo18` (branches: `msp_production` for prod, `19_upgradetest2` for v19 fixes)
- Recovery tooling + docs: `MSPlastics/18to19upgrade` (this repo)

**Current state**: Production LIVE on Odoo 19 since 2026-05-03. All custom modules load, Studio views recovered, packaging behavior restored on sale.order.line + purchase.order.line + stock.move. Custom MSP report suite shipped 2026-05-04 (sale order, invoice with Send-flow rebind, pick sheet, delivery slip). Three idempotent Studio repair patchers shipped 2026-05-04 (variant-related rewrites, ksc_partner shipping-instructions view, procurement_group_id rewrites). MSP Open Sales Orders dashboard built programmatically (2026-05-05/06). `msp_packaging` bumped to 19.0.1.4.0 on 2026-05-06 to fix the product.template Packaging tab create-flow. `msppartialMO` vendored into `19_upgradetest2` on 2026-05-04 — staging-only pending verification.

**Branch tips (as of 2026-05-07)**: `msp_production` = `72abe9c` (today's cherry-picked eq_cancel_mrp_orders fixes); `19_upgradetest2` = `a82afb9`. Branches still diverge on the `67b22e5` msppartialMO vendor (staging-only, pending verification) and on the SHA of the msp_packaging Packaging-tab fix (`8d0f838` on staging, `c2c8218` on prod — same content, different SHA from earlier separate commits).

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
| `msp_planning` | `_calculate_date_by_sequence` AttributeError on workorder produce — `module 'odoo.models' has no attribute 'NewId'`. v19 reorganized the ORM and dropped the `NewId` re-export from `odoo.models`. | Switched to `not isinstance(self.id, int)` (persisted records have int ids, NewId records don't) — no new import needed. Caught on staging via the produce-quantity flow before mirroring to prod. | `d967681` |
| `msp_packaging` | Adding a row through the product.template Packaging tab raised `ValidationError: Missing required value for the field 'Product'`. `packaging_ids` on `product.template` is a One2many keyed on `product_tmpl_id`, but on `product.packaging` `product_tmpl_id` is a stored *related* (resolves from `product_id.product_tmpl_id`). The form write path populated only `product_tmpl_id`, so the required-check on `product_id` fired before the related could resolve. | Override `product.packaging.create()` to derive `product_id` from `product_tmpl_id`'s first variant when missing. Safe because MSP's whole catalog is single-variant. Manifest 19.0.1.3.0 → 19.0.1.4.0. | `8d0f838` (staging) / `c2c8218` (prod) |
| **NEW**: `msppartialMO` | Vendored into `19_upgradetest2` for Odoo.sh installable rebuild. Source-of-truth lives at `MSPlastics/msppartialMO@19_upgrade` (commit `15ee20f`, version `19.0.1.1.0`). The MES central server depends on three methods this addon provides — `action_increment_qty_producing`, `action_ship_partial_batch`, `action_close_and_backorder` — which were missing on the freshly-built v19 staging branch, breaking every production-update RPC. v19 deltas vs the v18 source `ce0519d`: (1) manifest `1.0.0 → 19.0.1.0.0`; (2) `mo.lot_producing_id` (Many2one, removed) → `mo.lot_producing_ids[:1]` (Many2many, single-lot semantic preserved) in `action_ship_partial_batch`; (3) `stock.move.name` (Char, fully removed in v19 — both read and write raise `ValueError: Invalid field 'name' in 'stock.move'`) → `description_picking` (Text, the v19 successor for the move's human-readable label) in the create-vals dict. Manifest bumped 19.0.1.0.0 → 19.0.1.1.0 with the `description_picking` fix. Verified end-to-end on staging 2026-05-09 via [workflow/install_and_test_msppartialMO.py](workflow/install_and_test_msppartialMO.py): `action_increment_qty_producing(MO 95, +5.0)` moved state confirmed → progress with the lot preserved; `action_ship_partial_batch(MO 95, +2.0)` created internal-transfer picking `WH/INT/00001` in `state=done` with `description_picking='Partial Shipment: WH/MO/00096'` and `quantity=2.0`. `action_close_and_backorder` is static-audit-only — its wizards (`mrp.consumption.warning.action_confirm`, `mrp.production.backorder.action_backorder`) exist on v19 but the path wasn't directly exercised. | `67b22e5` (initial vendor) / `8eaf317` (description_picking bump) |
| `eq_cancel_mrp_orders` | `action_reset_to_draft` wrote `state='pending'` to cancelled workorders before unlinking them. v19 dropped `'pending'` from `mrp.workorder.state` — valid values are now `blocked / ready / progress / done / cancel`. Surfaced as `ValueError: Wrong value for mrp.workorder.state: 'pending'` when resetting WH/MO/01537 to draft. | Switched to `'blocked'` (closest semantic to the original `'pending'`; the records are unlinked on the next line so the chosen state just needs to be valid). Manifest 19.0.1.0 → 19.0.1.1.0. | `994abeb` (staging) / `8d3e358` (prod cherry-pick) |
| `eq_cancel_mrp_orders` | After fixing the workorder-state error, the next click hit `AttributeError: 'mrp.production' object has no attribute '_onchange_product_id'`. Verified via `fields_get` that the six fields `product_id` / `picking_type_id` / `bom_id` / `move_raw_ids` / `workorder_ids` / `move_finished_ids` are no longer compute fields in v19, so the six `_compute_*()` calls following the onchange call would also fail one at a time on subsequent clicks. | Wrapped all 7 onchange/compute calls in `hasattr` / `getattr` guards so the code stays compatible across versions. The `product_qty = bom_id.product_qty or 1` assignment is preserved at its original position. Manifest 19.0.1.1.0 → 19.0.1.2.0. | `a82afb9` (staging) / `72abe9c` (prod cherry-pick) |

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

### msp_packaging extensions (2026-05-04 → 2026-05-06, current version `19.0.1.4.0`)

v19 dropped `product.packaging` everywhere. Our v19-only `msp_packaging` initially restored it on `sale.order.line` only; we extended to mirror v18 fully:
- **`purchase.order.line`** (`19.0.1.2.0`, commit `16b0ae3`): `product_packaging_id`, `product_packaging_qty`, plus the same forward+inverse compute pattern as sale.order.line
- **`stock.move`** (`19.0.1.3.0`, commit `ac09fbc`): `product_packaging_id`, `product_packaging_qty`, `product_packaging_quantity` (v18 alias). Propagated from `sale_line_id` or `purchase_line_id` so existing v18 Studio delivery slips render.
- **product.template Packaging tab create() override** (`19.0.1.4.0`, commit `8d0f838` on staging / `c2c8218` on prod, 2026-05-06): adding a row through the product.template Packaging tab raised `ValidationError: Missing required value for the field 'Product'` — `packaging_ids` is a One2many keyed on `product_tmpl_id`, but on `product.packaging` `product_tmpl_id` is a stored *related* (resolves from `product_id.product_tmpl_id`); the form write path populated only `product_tmpl_id` and the required-check on `product_id` fired before the related could resolve. Override `create()` to derive `product_id` from `product_tmpl_id`'s first variant when missing. Safe — MSP's whole catalog is single-variant. Explicit `product_id` in vals is left untouched, and rows missing both fields still raise the original required error.

### Method-call false positives — DON'T patch these

While building `fix_qweb_v18_residue.py` we noticed several QWeb references that *look* like missing fields but are method calls. They still work in v19. Don't add rules for them:
- `o.should_print_delivery_address()` — method on stock.picking
- `o._get_report_lang()`, `o.with_context`, `o.sudo`, `o.env` — Python attributes/methods
- `move.name` — typically inherited from base, may not appear in `ir.model.fields` queries

### Heads-up: Studio fields on sale.order in view 2442

`studio_customization.studio_report_docume_b79dd625-...` references several `doc.x_studio_*` fields that don't exist on v19 sale.order (likely wiped by the migration like the mrp.production ones were). The view is active but no `ir.actions.report` matches it directly so it's not in any active render path. Left alone for now; if MSP later wants to use a Studio sale order report variant, those fields would need to be recreated similarly to how Step 1 of the recovery handled the MO ones.

---

## MES sync-path lot-tracking fixes (2026-05-09)

Four defects in the MES → Odoo outbound sync path surfaced while testing end-to-end lot consumption on the rebuilt v19 staging. All four are fixed in `MSPlastics/MESv1.0:master`. They sit on top of the `msppartialMO` 19.0.1.1.0 vendor that was already verified earlier in the day.

| Order | Commit | What was wrong |
|---|---|---|
| 1 | `71998a8` | Three related fixes: (a) `sync_production_to_odoo`'s outermost `try/except` swallowed every exception, so `process_sync_queue`'s contract (raise → invalidate cache → retry) was broken; cached `OdooDataManager` pointing at a defunct staging URL survived forever, every roll silently no-op'd against a dead host. (b) `/api/settings` POST didn't invalidate `sync_engine._cached_odoo`, so URL changes never took effect for the worker. (c) FG-attach path wrote `{'lot_producing_id': fg_lot_id}` (v18 Many2one, removed in v19) — fault was being hidden by defect (a). |
| 2 | `67d81c5` | Architecture rule: master rolls produced in the **extrusion step** of a multi-step MO are **WIP, not finished goods**. They must NOT advance `qty_producing` or trigger a partial shipment. Calling `action_increment_qty_producing(MO, 1.0)` on a Case-UOM MO at extrusion-time triggered Odoo's `_set_qty_producing` inverse, which rebalances raw `move_line.quantity` per BOM-demand-per-Case ratio — overwriting the per-roll values MES had just computed from `case_weight × layer × hopper_percent`. Now the FG block is gated on `is_last_step or not is_multi_step`. Also: `picked=True` is set on every raw move after consumption write, defending against future _set_qty_producing triggers via other code paths. |
| 3 | `98b5362` | The matching loop in `sync_production_to_odoo` summed `consumed_qty` per material across `consumed_lots[]` entries but **threw away the `lot_number`** on every entry. The lot lookup then fell through to `_get_or_create_lot(pid, lot_name=None)` which returns the first FIFO-positive lot Odoo finds — usually a pre-existing lot unrelated to what the operator actually loaded into the silo or line. Now the loop captures `matched_lot_name` from the first matched entry and the lot lookup prefers it. Result: the lot the operator put into the silo / line on the MES `/resin` page lands on `stock.move.line.lot_id`, matching the actual material that physically went into the roll. |
| 4 | `6a5bf3d` | For **single-step Inline orders**, the FG block does need to fire (so `qty_producing` advances and a partial-shipment is generated), but `_set_qty_producing`'s rebalance was still overwriting the per-roll resin/additive quantities we'd written. `picked=True` on the move preserves the lot but does NOT prevent the qty rebalance. Fix: track every move_line written during raw consumption, then after the FG block runs, restore the original quantities. If `_set_qty_producing` deleted any of them outright, recreate with the saved vals. |

### End-to-end verification on staging (2026-05-09)

Tested both flow shapes with reproducible scripts in `workflow/`:

**Multi-step MO** `WH/MO/01479` (5-Layer extrusion, BOM `[MSPL 4MILBRN]`):
- Step 1 extrusion: 100 lb roll → 7 raw materials consumed at correct `case_weight × layer × hopper%` qty (Butene1-BF 50.40, Frac1-A 6.00, Color Repro 38.00, Exceed 1012RA 1.00, conANTIBLOCK clarity 0.60, con-brown1 2.00, conSLIP fast 2.00 = 100.00 lb total). Each `stock.move.line` carries the silo / line-inventory lot (`TEST-2026-05-09-<material>-001`). No FG sequencing (master rolls = WIP). Reproducible: `workflow/test_mo_1583_forward.py`.
- Step 2 converting: BOX + Label consumed (1 unit each per Case), `qty_producing` advances by 1, `msppartialMO.action_ship_partial_batch` creates an internal transfer (`Partial Shipment: WH/MO/01479` → done) moving the FG to `WH/Stock` under the MO-level lot `MO/01479-001`. Reproducible: `workflow/test_mo_1583_converting.py`.
- Backward verification: `workflow/view_mo_consumption.py` prints the full raw-lot lineage per move for the MO, plus a `Material → Lot` rollup. The "open a work order, see what raw lot was consumed" view.

**Single-step Inline MO** `WH/MO/00094` (Line 6 6" Davis):
- 100 lb roll → resin distributed by hopper percentages (Butene1-BF 83.00, Frac1-A 15.00 with TEST lots), BOX + Label consumed in the same pass (qty=1 each), FG block fires (qty_producing 0→1, partial-ship `WH/INT/00006` done). Reproducible: `workflow/test_mo_93_inline.py`.

**Outbound chain** (FG → delivery, including split deliveries):
- Producing N Cases against MO `WH/MO/01479` → SO `S01029`'s outgoing delivery `WH/OUT/01241` auto-reserved each Case as it arrived in `WH/Stock`, suggested lot `MO/01479-001` (the MO-level lot, NOT a FIFO pick from any other lot). Validated: 7 Cases shipped done, backorder `WH/OUT/01336` created for 65 remaining. Producing 3 more Cases auto-reserved them on the backorder (state confirmed → assigned, qty 0 → 3, lot still `MO/01479-001`). Reproducible: `workflow/test_mo_1583_outbound.py` and `test_mo_1583_backorder.py`.

### Known issue surfaced during testing (Odoo data-side, not code)

**Blend ↔ BOM data drift in Odoo Studio.** Some blend recipes (`x_blends` records read by MES into `WorkOrder.hoppers_json`) reference legacy combined products that the BOM has since split into multiple modern products. Example: blend `4001 - CLR - 73/25` lists `con-Antiblock/slip` (product 579) at 2%, but the BOM splits this into `conANTIBLOCK clarity` (40) + `conSLIP slow` (42) at 1% each. MES `record_roll` builds `consumed_lots` from the hoppers JSON (= legacy product), so the substring-match in `sync_production_to_odoo` fails against the BOM products, and the "MES provided blend but resin not in it" gate skips them. **Fix is on the Odoo side** — refresh blend recipes to match the current BOM products. Surfaced on `WH/MO/00094` during today's inline test; likely affects more MOs whose blend recipe predates the additive split.

### Other open items (2026-05-09)

- `action_close_and_backorder` (the third `msppartialMO` method) is still **static-audit-only** — its wizards exist on v19 but the path itself wasn't directly exercised. To exercise: bring an MO close to its target, then call `action_close_and_backorder` and confirm a backorder MO is created with the residual qty.
- **Customer-paperwork PDFs** (delivery slip, pick sheet) — the data is correct (verified via XML-RPC), but the actual PDF rendering wasn't visually printed on staging post-test. Worth a quick render of `WH/OUT/01241` and `WH/OUT/01336` to confirm the layout shows the FG lot cleanly and doesn't leak raw lots.

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

## Custom MSP invoice report + Send-flow rebind (post-cutover, 2026-05-04)

Built a fully custom MSP-styled invoice report on `account.move` to match the sale order PDF (same logo block, brand palette, address layout, totals panel) plus a Lot Number column. Lots are sourced from `account.move.line.sale_line_ids` → `stock.move` (matched by `sale_line_id`) → `move_line_ids.lot_id`, **comma-joined onto one row per invoice line** (per accounting's preference — they don't want multiple rows just because picking split across lots). State-aware title (Invoice / Credit Note / Draft variants). Amount Due row surfaces only for partial payments.

### Records on prod

| Type | Key / id | Name |
|---|---|---|
| `ir.ui.view` (qweb) | `msp.report_invoice_msp_v1` | "MSP Invoice Report" |
| `ir.actions.report` | (looked up by `report_name`) | "Invoice — MSP" |
| `ir.ui.view` (rewritten) | `account.report_invoice_with_payments` | One-line delegate to the MSP view |

### The Send-flow gotcha

The Send Invoice wizard in v17+ **caches** a PDF on `account.move.invoice_pdf_report_id` using a **hardcoded** report (`account.report_invoice_with_payments`) — and the email template's `report_template_ids` then layers on top of that cached PDF. So just wiring the email template to our MSP report produced **two attachments per send**: the standard PDF (from the cache) and the MSP PDF (from the template).

The fix is in [workflow/route_invoice_pdf_to_msp.py](workflow/route_invoice_pdf_to_msp.py):

1. The stock `account.report_invoice_with_payments` wrapper view is just 191 chars and nothing inherits from it (the four inheriting views all hang off the inner `account.report_invoice_document`, untouched). Replace its `arch_db` with a one-line `<t t-call="msp.report_invoice_msp_v1"/>`.
2. The cached PDF then **is** the MSP report.
3. Empty `report_template_ids` on the Invoice / Credit Note send templates so nothing extra layers on top — single clickable MSP attachment per Send.

The script is idempotent (detects current state by string-matching `arch_db` against the original stock arch) and ships with `--restore` to put the original Odoo arch back if needed.

The earlier [workflow/set_msp_invoice_on_email_templates.py](workflow/set_msp_invoice_on_email_templates.py) was the first attempt — wire MSP via `report_template_ids` only. Superseded; kept in the tree as the "thing we tried first that didn't work" reference.

### Lot-resolution pattern (account.move.line → stock lots)

Useful pattern for any future report needing lot info on an invoice:

```python
# pseudo-Python — actually expressed in QWeb via t-set
move_lines = line.sale_line_ids.move_ids \
    .filtered(lambda m: m.state == 'done' and m.sale_line_id == line.sale_line_ids[:1])
lots = move_lines.move_line_ids.mapped('lot_id.name')
lot_text = ', '.join(lots) if lots else ''
```

Worth knowing: `sale_line_ids` is a Many2many on invoice lines (one invoice line can come from multiple sale lines via grouping). MSP's invoicing pattern is one-to-one in practice.

---

## MSP warehouse pick sheet + customer delivery slip (post-cutover, 2026-05-04)

Two QWeb reports bound to `stock.picking`, both coexist with Odoo's standard delivery slip + picking operations report (Print menu still shows all of them).

| Report | File | Use case | Layout |
|---|---|---|---|
| Pick sheet | [workflow/create_msp_pick_sheet.py](workflow/create_msp_pick_sheet.py) | Floor team pulls product from inventory | Landscape, 8-col. **One row per `stock.move.line`** so multi-lot moves split per-lot. Pallets + Weight blank for write-in. Pick Qty uses `move.quantity` (matches the Operations UI), not the demand qty. |
| Delivery slip | [workflow/create_msp_delivery_slip.py](workflow/create_msp_delivery_slip.py) | Customer-facing copy that ships with the goods | Portrait, 6-col. Same header treatment as the sale order PDF (Sold To + Ship To 2-col, meta panel). Shipped Qty uses `move.quantity`. Bottom **POD block**: Shipper signature/date + Received By signature/date. |

Both are idempotent upserters keyed by view key + report name.

### move.quantity vs product_uom_qty (both reports)

The pick sheet and delivery slip both use `move.quantity` for the per-row qty, **not** `move.product_uom_qty`. `product_uom_qty` is the demand (what was originally requested); `quantity` is the actually-picked / actually-shipped value that the warehouse staff entered in the Operations UI. The reports need to reflect what's physically going on the truck, so always use `quantity`.

### Per-move-line splitting (pick sheet only)

The pick sheet iterates `move.move_line_ids` instead of `move_ids` so multi-lot moves naturally split into one row per lot. The floor team needed this — they pull from physical pallets keyed on lot numbers, and a 100-piece move spread across 3 lots needs 3 separate pick lines, not one combined line.

### 2026-07-02: pick sheet crashed on print — TWO v19 breakers stacked in one lambda

The pick sheet (`msp.report_pick_sheet_v1`, live view **3039**) threw `RPC_ERROR` on every print. Two independent Odoo-18→19 incompatibilities in the `pieces_of` lambda, both hidden by lazy QWeb compile/render until someone printed. (First presented as a stale-session **"invalid CSRF token"** — a red herring from the ~17:00Z deploy; a hard-refresh cleared *that* and exposed the real errors below.)

**Breaker 1 — closure opcodes forbidden in QWeb expressions (COMPILE error → breaks every render).**
v19 `ir_qweb._compile_expr` runs `assert_valid_codeobj(_SAFE_QWEB_OPCODES, compile(expr,'<>','eval'))` on every `t-set`/`t-value` and forbids `LOAD_CLOSURE`, `LOAD_DEREF`, `MAKE_CELL`. So **no lambda/genexpr/comprehension may close over an enclosing lambda's local**. Ours:
```python
lambda ml: ... any(w in (ml.product_uom_id.name or '').lower() for w in ('lb','kg',...)) ...
```
The genexpr closes over the lambda param `ml` → cell → `ValueError: forbidden opcode(s) in 'lambda': LOAD_CLOSURE, LOAD_DEREF, MAKE_CELL`. **Fix:** rewrite the `any(...)` as an `or`-chain (`'lb' in name or 'kg' in name or ...`) — no nested scope, no cell. Plain lambdas (`filtered(lambda x:...)`, `sorted(key=lambda x:...)`) are fine; only *closures* are rejected. ⚠️ `ast.parse` does NOT catch this (valid syntax) — you must `compile()` and inspect `co_cellvars`/`co_freevars` recursively.

**Breaker 2 — `uom.uom.category_id` removed (RENDER error).**
`ml.product_uom_id.category_id.name` → `AttributeError: 'uom.uom' object has no attribute 'category_id'`. The whole category concept is gone in v19 (see the corrected glossary row above). **Fix:** drop the category branch; detect weight/length by UoM name (MSP's are `lb`/`lbs`/`ft`).

**Diagnosis + deploy tooling (in the MES repo `_reports/`, run on the prod MES VM):**
- `_diag_pick_compile.py` — pulls the LIVE arch, ast-checks it, and reproduces Odoo's real traceback via a throwaway `ir.actions.server`. ⚠️ v19 server-action `safe_eval` forbids `import` (`forbidden opcode IMPORT_NAME`) — don't `import traceback`; let the render raise and read the traceback out of the XML-RPC `Fault.faultString`.
- `_probe_uom.py` — dumps `uom.uom` schema + records (confirmed no `category_id`).
- `_apply_pick_fix.py` — pushes the corrected canonical arch: temp-view render PROOF → backup (`/tmp/pick_sheet_arch.bak-<ts>.xml`) → write view 3039 → live re-render → AUTO-ROLLBACK on failure.
- `_verify_pick_ids.py` — renders live across pallet/loose/recent pickings (all clean post-fix).

Scanned all 7 `create_msp_*.py` QWEB_ARCH: **only the pick sheet had either breaker.** Canonical `workflow/create_msp_pick_sheet.py` (`QWEB_ARCH`) edited with both fixes — **not yet git-committed** (same pattern as the other post-cutover report edits).

**Same day, separate class — delivery slip `â€"` mojibake (view 3040).** The no-pallet placeholder in the delivery slip's Total-Pallets column was a literal em-dash `—` (U+2014); wkhtmltopdf mis-decodes its UTF-8 bytes as Latin-1 → `â€"` (same class as the NBSP `Â ` gotcha under "Custom MSP sale order report"). ⚠️ Numeric entities give **no** protection — Odoo resolves `&#8212;` → literal `—` in `arch_db` on save, so it still mojibakes. Fixed to plain ASCII `-` (canonical `create_msp_delivery_slip.py` + live view 3040 via `_reports/_apply_del_fix.py`). Rule: **use plain ASCII in rendered placeholders**; entities in comments / the action name don't render and are harmless.

---

## Studio repair patchers (post-cutover, 2026-05-04)

Three idempotent patchers shipped for v18→v19 Studio damage that surfaces during normal usage (each runs whenever a new instance of the underlying pattern is found). Same script discipline as `fix_qweb_v18_residue.py` — narrow rules, dry-run by default, `--target staging|prod --commit`.

### `workflow/fix_studio_variant_related.py`

When a manual Studio field on `product.template` has `related='product_variant_id.<something>'`, Odoo's invalidation trigger machinery in v19 will eventually try to run `search([('product_variant_id', 'in', [...])], order='id')` against `product.template`, which fails because `product.template.product_variant_id` is non-stored:

```
ValueError: Cannot convert product.template.product_variant_id to SQL
because it is not stored
```

This surfaces when the user edits any related-target chain — most notably `customer_ids.product_name` on `product.template` (i.e., a customer's drop part number on the Customers tab of a product).

**Fix:** drop the `product_variant_id.` prefix. The same field is addressable directly on `product.template` since the variant fields that follow either exist on the template or are related back to it. Idempotent — only writes fields whose `related` still starts with the prefix, and only on `product.template` (where the rewrite is provably equivalent — fields on other models with `something.product_variant_id.X` need a per-case fix).

### `workflow/recover_partner_shipping_instructions.py`

Re-creates the inherit view that places `x_studio_shipping_instructions` inside the ksc_partner Delivery Information tab on `res.partner`. The field + 205 partner records survived migration; only the view was deleted. Same recovery shape as the post-cutover Studio form view restores — just narrower scope (one view, one inherit).

### `workflow/fix_studio_procurement_group_compute.py`

Rewrites `record.procurement_group_id.sale_id` → `record.sale_order_id` in manual computes on `mrp.production`. v19 removed `procurement_group_id` (replaced by `reference_ids`), and the bare `except` in the compute body was masking the AttributeError as the field's stored value — meaning the field looked "fine" until you tried to read it from a different code path.

---

## MSP Open Sales Orders dashboard (post-cutover, 2026-05-05/06)

Built programmatically via [workflow/create_msp_dashboard.py](workflow/create_msp_dashboard.py) — a `spreadsheet.dashboard` record with a fully populated spreadsheet JSON, no UI clicks. Three live-bound list sections:

| Section | Source model | Columns |
|---|---|---|
| 1. Open sales orders | `sale.order` | name, partner_id, commitment_date, msp_drop_po, client_order_ref, amount_total, user_id |
| 2. Order lines (qty ordered vs delivered) | `sale.order.line` | order_id, product_id, name, product_uom_qty, qty_delivered, x_studio_freight_terms |
| 3. MOs (linked SO still open) | `mrp.production` | name, sale_order_id, sale_order_line_id, product_id, state, date_finished, product_qty, qty_produced, sale_order_line_id.qty_delivered, **Balance** (computed `qty_produced - qty_delivered`) |

Idempotent — looks up by name + group ("Open Sales Orders" in dashboard group "Open orders"), updates if found, creates if not.

### MSP-specific filter rules learned (very useful for future dashboard / reporting work)

1. **"Open" sale order** is **not** `state='sale'` — Odoo doesn't auto-close orders post-delivery, so most `state='sale'` orders are actually fully shipped. The real "open" gate is `state='sale' AND delivery_status != 'full'`. Same applies to filtering open order lines (`order_id.delivery_status != 'full'`) and to filtering MOs whose linked SO is still open.

2. **MO ↔ Sale Order link**: MSP populates `mrp.production.sale_order_line_id` and `sale_order_id` (likely a custom module override). The standard `sale_line_id` is **unreliable** — only ~73% of MOs are linked there. Always use `sale_order_line_id` for MO→SO joins.

3. **`spreadsheet.dashboard` engine version**: Odoo 19 ships o-spreadsheet engine `18.5.10`. Storing a different version (e.g. `1`) makes the engine refuse to render cells. The `version` key in the JSON is the o-spreadsheet engine version, not Odoo's version.

4. **Domain syntax in spreadsheet JSON**: domains are nested arrays of triples (Python-list-as-JSON), not the prefix-notation strings used in `ir.filters.domain`. Easy gotcha when copy-pasting from a saved filter.

### Saved favorite filters

[workflow/create_dashboard_filters.py](workflow/create_dashboard_filters.py) creates the equivalent `ir.filters` records as alternate starting points for users who want to build dashboards from the cog-menu "Insert list in Spreadsheet" path. Idempotent — looks up by name+model+user_id.

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

### Repos and branch tips (as of 2026-05-06)

| Where | What |
|---|---|
| `MSPlastics/odoo18` branch `msp_production` | Prod target — **LIVE** on v19 since 2026-05-03. Tip `72abe9c` (today's eq_cancel_mrp_orders v19 fixes, cherry-picked from staging). |
| `MSPlastics/odoo18` branch `19_upgradetest2` | Staging branch. Tip `a82afb9`. Diverges from prod via the `67b22e5` msppartialMO vendor (staging-only, pending verification) plus a same-content/different-SHA pairing on the msp_packaging Packaging-tab fix. |
| `MSPlastics/msppartialMO` branch `19_upgrade` | Source-of-truth for the `msppartialMO` addon (commit `1d2264d`). |
| `MSPlastics/18to19upgrade` (this repo) | Recovery tooling + docs only — no Odoo module code. |
| `MSPlastics/odoo18/msp_packaging/` | v19-only module: redeclares v18's `product.packaging` model + sale.order.line warning popup + propagation to purchase.order.line / stock.move + product.template Packaging-tab create() override. |
| `MSPlastics/odoo18/msppartialMO/` | (staging only, 19_upgradetest2) MES central server's required addon — `action_increment_qty_producing` / `action_ship_partial_batch` / `action_close_and_backorder`. |

### Local clones on Anthony's machine

| Path | What |
|---|---|
| `C:/Users/Anthony/Desktop/18to19upgrade/` | This repo — recovery tooling + docs. |
| `/c/msp_backups/extracted/v19audit/` | Working clone of `MSPlastics/odoo18`. Use this for any `git log/diff/push` on module code. |

### Workflow scripts (this repo)

| Script | Era | Purpose |
|---|---|---|
| `workflow/post_migration_recovery.py` | Cutover (archived) | The single command that restored Studio + packaging after migration. **Don't re-run on prod.** |
| `workflow/snapshot_v18_data.py` | Cutover (archived) | Pre-cutover dump of 242 packagings + 491 MO Studio qtys. |
| `workflow/studio_arch/*.xml` | Cutover (archived) | Saved prod Studio view archs that the recovery script applied. |
| `workflow/prod_disable_kits.py` | Cutover (archived) | Phantom BOM flip + restore. |
| `workflow/prod_zero_negatives.py` | Cutover (archived) | Negative quant cleanup. |
| `workflow/fix_qweb_v18_residue.py` | Post-cutover (re-runnable) | Comprehensive QWeb v18-residue patcher. |
| `workflow/fix_qweb_uom_v18_residue.py` | Post-cutover (superseded) | Older standalone `line.product_uom` patcher. Replaced by `fix_qweb_v18_residue.py`. |
| `workflow/fix_external_layout_logo.py` | Post-cutover (re-runnable) | Restore dynamic `company.logo` binding in Studio external layouts. |
| `workflow/fix_studio_variant_related.py` | Post-cutover (re-runnable) | Strip redundant `product_variant_id.` prefix from product.template Studio related paths. |
| `workflow/recover_partner_shipping_instructions.py` | Post-cutover (re-runnable) | Re-create the inherit view that shows `x_studio_shipping_instructions` on the partner Delivery Information tab. |
| `workflow/fix_studio_procurement_group_compute.py` | Post-cutover (re-runnable) | Rewrite `procurement_group_id.sale_id` → `sale_order_id` in mrp.production manual computes. |
| `workflow/create_msp_sale_report.py` | Custom report (re-runnable) | MSP sale order PDF (`msp.report_saleorder_msp_v1`) + report action ("Quotation / Order — MSP"). |
| `workflow/set_msp_report_on_email_templates.py` | Custom report (re-runnable) | Wire MSP sale order report into the four standard sale.order email templates. |
| `workflow/create_msp_invoice.py` | Custom report (re-runnable) | MSP invoice PDF (`msp.report_invoice_msp_v1`) on account.move. |
| `workflow/route_invoice_pdf_to_msp.py` | Custom report (re-runnable, supports `--restore`) | Rewrite `account.report_invoice_with_payments` to delegate to MSP view + empty Send-template `report_template_ids` to dedupe attachments. |
| `workflow/set_msp_invoice_on_email_templates.py` | **Superseded** | Earlier attempt to wire MSP invoice via `report_template_ids`. Kept for reference. |
| `workflow/create_msp_pick_sheet.py` | Custom report (re-runnable) | MSP warehouse pick sheet on stock.picking. Per-move-line splitting. |
| `workflow/create_msp_delivery_slip.py` | Custom report (re-runnable) | MSP customer-facing delivery slip on stock.picking with POD signature block. |
| `workflow/create_msp_dashboard.py` | Dashboard (re-runnable) | MSP Open Sales Orders spreadsheet dashboard. |
| `workflow/create_dashboard_filters.py` | Dashboard (re-runnable) | Saved favorite ir.filters records as dashboard starting points. |
| `tools/diag_modules.py` | Diagnostic | XML-RPC: state of all custom modules + fields. |
| `tools/check_module_state.py` | Diagnostic | XML-RPC: single module state check. |
| `tools/force_module_upgrade.py` | Diagnostic | XML-RPC: trigger button_immediate_upgrade. |
| `tools/uninstall_module.py` | Diagnostic | XML-RPC: trigger button_immediate_uninstall. |
| `tools/read_logs.py` | Diagnostic | XML-RPC: read recent server logs. |

---

## Glossary of v19 changes that affected us

| v18 thing | v19 status | Notes |
|---|---|---|
| `product.packaging` model | **Removed** | Replaced by `uom.uom` with `factor`, `relative_factor`, `package_type_id`. We re-added via `msp_packaging`. |
| `product_uom` field on `product.supplierinfo` | **Renamed** to `product_uom_id` | Cascading rename to `product.customerinfo` (which inherits) |
| `product_uom_category_id` on `mrp.bom.line` | **Removed** | ⚠️ **CORRECTION (2026-07-02):** `uom.uom.category_id` is **also gone** in v19 — the entire UoM *category* concept was removed (units now relate via `relative_uom_id`; `uom.uom` has no `category_id` and no `measure_type`). `product_uom_id.category_id` raises `AttributeError` at render. Detect weight/length UoMs by **name substring** instead. See "2026-07-02: pick sheet crashed on print" below. |
| `finished_lot_id` (Many2one) on `mrp.workorder` | **Renamed** to `finished_lot_ids` (Many2many) | Multiple lots per workorder now supported |
| `lot_producing_id` (Many2one) on `mrp.production` | **Renamed** to `lot_producing_ids` (Many2many) | Same change at the MO level |
| `procurement_group_id` on `mrp.production` | **Removed** | Replaced by `reference_ids` |
| `name` (Char) on `stock.move` | **Removed** | Both read and write raise `ValueError: Invalid field 'name' in 'stock.move'`. Use `description_picking` (Text) for the human-readable per-move label. Other still-present `*name*` fields on stock.move are `description_picking_manual` (manual override), `inventory_name` (for inventory adjustments), `display_name` (computed-only). Surfaced in `msppartialMO.action_ship_partial_batch` 2026-05-09. |
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
