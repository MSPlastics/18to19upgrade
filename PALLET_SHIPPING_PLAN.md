# Pallet shipping speedup — plan

Drafted 2026-05-09. Pre-implementation. Intended to survive context compacting.

## Problem

Pickers ship by pallet, not individual case. Today the SO delivery picking shows N individual `stock.move.line` rows (one per Case). For 25+ pallet orders that's hundreds of rows the picker has to confirm one-at-a-time. Need to group cases into pallets that map to scannable physical pallets.

## Architecture decision: hybrid

Lean on Odoo's built-in `stock.quant.package` as the data backbone, with a small custom module on top for MSP-specific pallet metadata. Reasoning:
- Odoo's package model already integrates with picker UX, scanning, reservation engine. Reinventing that means giving up standard behavior on every Odoo upgrade.
- Pure `stock.quant.package` doesn't carry MSP's pallet metadata (gross weight measured at scale, dimensions, MO origin link, unit-number ranges with gaps).
- Hybrid: standard Odoo behavior + MSP custom fields.

## Settled decisions (2026-05-09)

| # | Decision | Why |
|---|---|---|
| 1 | Pallet QR encodes raw `pallet_id` (e.g. `"PLT-00081"`). Reuse the existing operator-UI generated QR (already on the printed pallet sheet). | Existing code at `operatorUI/app.py:820+` does `qrcode.make(pallet_id)`. |
| 2 | Kiosk lives on **MES** (central command, port 5000), NOT operatorUI. | The scale station is plant-wide shared infra; the Pallet record is in MES local DB; operatorUIs are per-station edge nodes. |
| 3 | Weight sanity check: warn if `abs(entered_weight - expected) > 10%` of expected, where `expected ≈ (BOM pallet weight from MO) + ~50 lb tare`. Don't block, just warn. | Catches typos / wrong-pallet scans without halting workflow. |
| 4 | Re-scaling: allow update if `result_package_id` move_lines are not yet `state=done`. Block with "already shipped" if shipped. | Operationally fine; preserves audit trail. |
| 5 | Single default `stock.package.type` "MSP Pallet". Dimensions per-package, not per-type. | MSP runs lots of dimension variants; per-package is right granularity. |
| 6 | Mixed pallets supported (Cases from multiple MOs on one pallet). `msp_mo_ids` / `msp_lot_ids` are M2m computed from contents. | User confirmed this happens. |
| 7 | Pallet sheet ported to Odoo QWeb. Single design source. Mirrors `create_msp_pick_sheet.py` pattern. | User said "stay consistent — copy the existing MES design". |

## Schema — `msp_pallet` Odoo module

New module `msp_pallet` on `MSPlastics/odoo18` 19_upgradetest2.

Custom fields on `stock.quant.package`:

| Field | Type | Source / population |
|---|---|---|
| `msp_gross_weight_lb` | Float | scale-measured at finalize |
| `msp_length_in` | Float | from operator UI palletization (already captured) |
| `msp_width_in` | Float | same |
| `msp_height_in` | Float | same |
| `msp_unit_numbers_summary` | Char | MES-formatted gap-aware range (e.g. `"1-17, 19-20"` when unit 18 was scrapped). MES computes in Python; Odoo stores literal string. |
| `msp_mo_ids` | M2m → mrp.production (computed) | derived from move_lines.move_id.production_id |
| `msp_lot_ids` | M2m → stock.lot (computed) | derived from move_lines.lot_id |
| `msp_finalized_at` | Datetime | when scale-finalize happened |

Plus:
- One `stock.package.type` record "MSP Pallet" (default) — created via XML-RPC like other one-off Odoo data, or in module `data/` XML.
- View extension on `stock.picking` adding a "Pallets" tab. Tab shows tree view of distinct `result_package_id` on this picking's move_lines, columns: `name`, `msp_lot_ids`, `msp_unit_numbers_summary`, dims (LxWxH formatted), `msp_gross_weight_lb`.

Manifest version: `19.0.1.0.0`. Standard MSP module pattern.

## Schema — MES `Pallet` model

Add column to existing `Pallet` model in `MESv1.0/db_models.py`:
- `gross_weight_lb` Float, nullable. Set when scale-kiosk confirms.

Schema migration via the existing auto-migration helper (it adds columns automatically on engine init when missing — same pattern as prior column adds).

Optionally also add `is_finalized` Boolean. Already-stored fields like `dimensions` (JSON) and the relationship to rolls/cases are already there.

## MES kiosk view — `/kiosk/pallet-scale`

New route + template on **MESv1.0** (cloud test VM, central command).

Files:
- `templates/pallet_scale.html` — kiosk page
- `app.py`: `@app.route('/kiosk/pallet-scale')` GET (login_required for staff role) — renders the kiosk
- `app.py`: `@app.route('/api/v1/pallet/lookup/<pallet_id>')` GET — returns pallet summary for display before weight entry
- `app.py`: `@app.route('/api/v1/production/pallet/finalize')` POST — body `{ pallet_id, gross_weight_lb }` — updates Pallet.gross_weight_lb, queues sync job

UX flow:
```
[ Scan pallet barcode ____________ ]      ← scanner-focused input on page load

After scan:
   PLT-00081  ─  WO WH/MO/01479
   ──────────────────────────────
   20 cases on this pallet
   Units 1-17, 19-20         (unit 18 was scrapped, range auto-skips)
   Dimensions: 40 × 48 × 52
   Expected weight ~ 950 lb
   ──────────────────────────────
   Weight (lb): [_______]
                  [ Confirm ]

After Confirm:
   ✓ PLT-00081 finalized at 947 lb. Synced to Odoo.
   (page resets to scan input after 2s)
```

Sanity-check: if `|entered - expected| > 10%`, show yellow warning "Weight differs from expected by N%", but allow Confirm. If a typo (e.g. 9477 lb on a 950-lb-expected pallet), this catches it.

## MES sync — `sync_pallet_to_odoo`

New method on `OdooDataManager`. Called from `sync_engine.process_sync_queue` for jobs with `endpoint='pallet/finalize'`.

Flow:
1. Read MES `Pallet` record by `pallet_id`. Collect: `gross_weight_lb`, `dimensions` (parse JSON), unit/case roll_ids, MO numbers.
2. Compute `msp_unit_numbers_summary` (gap-aware range string) in Python from the unit numbers.
3. Upsert `stock.quant.package` on Odoo:
   - Search by `name = pallet_id`.
   - If exists: write fields. If absent: create with `package_type_id` = "MSP Pallet" type, `name = pallet_id`.
4. Selector: `stock.move.line` with `lot_id IN (FG lot for this MO) AND product_id = FG_product AND result_package_id = False AND state IN ('partially_available', 'assigned', 'confirmed', 'done') AND picking_id.state != 'done'`.
   - This naturally routes new pallets to whichever delivery picking currently reserves the relevant move_lines (original, backorder, etc.).
   - Order by `id ASC` and take first N matching the case count.
5. Write `result_package_id = package_id` on those move_lines.
6. Done.

Key invariant: `result_package_id IS NULL` cleanly identifies "Cases not yet on an Odoo package". Once set, those move_lines belong to that package and won't be re-claimed.

Re-scaling: if `Pallet.gross_weight_lb` is being updated and the package already exists with non-shipped move_lines, just update fields. If any of those move_lines are `state=done` (already shipped), block the update with an API error response so the kiosk can surface "already shipped — contact warehouse".

## Pallet sheet — Odoo QWeb port

Source: `operatorUI/templates/pallet_report_pdf.html`. Design: pallet ID, pallet#, dimensions, WO, product, description, customer, units list (with weights), total_count, total_weight, print_date, QR (encoding pallet_id).

New file `18to19upgrade/workflow/create_msp_pallet_sheet.py` — idempotent upserter pattern same as `create_msp_pick_sheet.py`. `ir.actions.report` bound to `stock.quant.package`. `print_report_name = "object.name"` so PDFs are `PLT-00081.pdf`.

Data binding inside the QWeb:
- Header: `o.name`, `o.msp_mo_ids[0].name`, `o.msp_mo_ids[0].product_id`, `o.msp_mo_ids[0].x_studio_customer`, etc.
- Units list: iterate `o.quant_ids` or the related `stock.move.line` records; show `lot_id.name`, qty, weight (could pull from MES via custom field if needed; standard Odoo move_lines don't carry per-unit weight — see open question below).
- Dimensions: format from `o.msp_length_in × o.msp_width_in × o.msp_height_in`.
- Total weight: `o.msp_gross_weight_lb`.
- QR: render via QWeb `<img t-att-src="image_data_uri(...)" />` from a barcode service in module, OR generate at upsert time and store on package.

## Picker view — Pallets tab on `stock.picking`

XML view extension in `msp_pallet` module:
- New page (tab) on `stock.picking` form view: "Pallets"
- Inside: tree view of distinct packages on this picking's move_lines, columns:
  - `name` (Pallet ID)
  - `msp_lot_ids` (joined names)
  - `msp_unit_numbers_summary`
  - dims combined: e.g. "40 × 48 × 52"
  - `msp_gross_weight_lb`
- Backed by a computed M2m `stock.picking.msp_pallet_ids` derived from `move_line_ids.result_package_id`.

For 25+ pallet orders, this is the speedup: picker reads 25 rows of pallet summary instead of hundreds of move_lines.

## Phase order

| Phase | Deliverable | Where |
|---|---|---|
| 1 | `msp_pallet` Odoo module — fields, package_type, view extension | `MSPlastics/odoo18` `19_upgradetest2` |
| 2 | MES `Pallet.gross_weight_lb` column | `MSPlastics/MESv1.0` `master` |
| 3 | MES `/kiosk/pallet-scale` template + route | MESv1.0 |
| 4 | MES `POST /api/v1/production/pallet/finalize` endpoint | MESv1.0 |
| 5 | MES `sync_pallet_to_odoo` worker method | MESv1.0 |
| 6 | `workflow/create_msp_pallet_sheet.py` (port HTML → QWeb) | `MSPlastics/18to19upgrade` `main` |
| 7 | E2E test on staging — `workflow/test_pallet_finalize.py` | 18to19upgrade |

Phases 1+2 in parallel (both schema). Phases 3-5 sequential. Phase 6 mostly mechanical port. Phase 7 end-to-end.

## Open questions for next session (none blocking)

- **Per-unit weight on the pallet sheet**: current MES sheet shows individual case weights. Standard Odoo `stock.move.line` doesn't carry that — the per-unit weight lives in MES on the `MasterRoll` record. Two options: (a) add custom field `stock.move.line.msp_unit_weight_lb` and have MES populate it during partial-ship sync, (b) on print, fetch unit weights from MES via a side-channel API call. (a) is simpler.
- **Backorder pallet routing edge case**: if a single pallet is finalized BEFORE any of its Cases have been partial-shipped to WH/Stock, the move_line selector returns 0 results and the package gets created but is empty. Handle by: pallet finalize blocks if no matching move_lines yet, with clear error. Or queue the sync to retry until move_lines appear.

## What's already in place to leverage

- MES Pallet model exists in `MESv1.0/db_models.py`: `pallet_id`, `wo_number`, `work_order_id`, dimensions, `total_weight` (computed property summing roll weights), `created_at`, link to rolls.
- operatorUI prompts for dimensions during palletization (`prompt_pallet_dimensions=true` in station_config) — already captured.
- operatorUI prints the pallet sheet with QR (encodes raw `pallet_id`) on pallet completion — already there.
- `record_roll_production` and the SyncQueue worker already handle the FG → partial-ship → Odoo move_line creation. The new pallet-finalize sync just adds a downstream `result_package_id` write to those existing move_lines.
- All v19 sync-path defects fixed (commits `71998a8`, `67d81c5`, `98b5362`, `6a5bf3d` on MES master) — the sync infrastructure that pallet-finalize will piggyback on is verified working.

---

# Implementation Status (2026-05-10) — what actually shipped

The original plan above is preserved for history. The actual build pivoted significantly in Phase 5 from the "claim move_line.result_package_id at finalize time" model to a real-time reconciliation sync that puts cases physically IN packages (`quant.package_id`) the moment they're stacked. Everything else followed from that change.

## What's built and on staging

### `msp_pallet` Odoo addon — `MSPlastics/odoo18` `19_upgradetest2`, version 19.0.1.0.3
- Custom fields on `stock.package` (renamed from `stock.quant.package` in v19): `msp_gross_weight_lb`, `msp_length_in/width_in/height_in`, `msp_dimensions_display` (computed), `msp_unit_numbers_summary`, `msp_finalized_at`, computed M2m `msp_mo_ids` and `msp_lot_ids` (derived via `lot.lot_producing_ids → mrp.production` since outbound delivery move_lines don't carry the MO link directly).
- Default `stock.package.type` "MSP Pallet".
- Form view extension on `stock.package`: MSP Pallet Info group (gross weight, dims, unit numbers, finalized timestamp) + Origins group (MOs/lots m2m_tags) + Reserved Cases group (move_line_ids list — pre-shipment view since standard CONTENT only shows quants after picking validation).
- Pallets tab on `stock.picking` form showing distinct `result_package_id` packages.

### MES sync architecture — `MSPlastics/MESv1.0` `master`, deployed to test VM only
- `Pallet.gross_weight_lb`, `is_finalized`, `finalized_at` columns added to `pallets` table. Migration script `migrate_pallet_finalize.py` (idempotent ALTER TABLE pattern matching `migrate_pallet_dims.py`).
- **Architectural pivot — reconciliation sync**: the original plan was to claim `result_package_id` on delivery move_lines at kiosk finalize time. That treats the pallet as a delivery-time grouping, mismatching MSP's reality where the pallet IS the unit of stock from the moment cases are stacked. Pivoted to:
  - `OdooDataManager.sync_pallet_reconcile_to_odoo(payload)` — a reconciliation pass that mirrors MES `Pallet → rolls` to Odoo `stock.package → quants`. Per-lot diff: pack missing free quants in via 0-distance internal `stock.picking` (WH/Stock → WH/Stock, validated via public `button_validate`), unpack excess quants out the same way. Idempotent.
  - Trigger: `record_roll_production` and `record_pallet_production` enqueue `pallet/reconcile` whenever a roll's `pallet_id` is set. Each reconcile creates one Odoo `stock.picking` audit row.
  - Handles all four ops cleanly: stack roll on pallet (pack 1), take case off (unpack 1), combine pallets A→B (unpack from A + pack into B in two reconciles via the free-quant pool), mixed-MO pallets (per-lot grouping in the reconcile).
- `OdooDataManager.sync_pallet_to_odoo(payload)` — the kiosk finalize handler is now metadata-only: writes `msp_gross_weight_lb` + `msp_finalized_at` on the existing package. If reconcile hasn't created the package yet, raises so the job retries.
- Wrap-and-scale kiosk at `/kiosk/pallet-scale` (login-gated, MES central command) — three-state UI (scan → weigh → done), 10% expected-weight sanity check requires second-Confirm, already-finalized banner shows when re-scaling allowed.
- `POST /api/v1/production/pallet/finalize` and `GET /api/v1/pallet/lookup/<pallet_id>` API endpoints.
- Helpers: `_format_unit_ranges([1,2,3,5,7,8,10]) → '1-3, 5, 7-8, 10'` (gap-aware), `PALLET_TARE_LB = 50.0`, `_pallet_lookup_payload`.

### Warehouse Pick Sheet — `msp.report_pick_sheet_v1` view 3039 on staging
Fully redesigned over multiple iterations:
- **Top: unified Pick Checklist** — one row per pallet, sorted by trailing `-PAL-N` ASC. Contents column shows per-line breakdown (`product x qty UoM | lot LOT_NAME`) — same style for pure and mixed pallets so lot is visible on every row.
- **UoM labeling everywhere** with packaging-aware conversion. Effective packaging per line = `move.product_packaging_id` OR fallback to `product.packaging_ids[:1]`. Per-pallet Units cell and Grand Total aggregate by UoM, so a pallet with `24 Thousands` (stock UoM) and product packaging `Case qty=0.25` displays as `96 Case`. Mixed-UoM pallets and grand total render multi-line per-UoM totals (`12 Roll / 40 Case`).
- **Pallet ID display** strips the verbose `WH/MO/` prefix — `WH/MO/01206-PAL-1` shows as `01206-PAL-1`. Sorted by parsed pallet number.
- **Bottom: Order Summary** table — compact per-(product, lot) row with MSP PN, description, lot, total units (packaging-converted), pallet count. Order matches the Pick Checklist (first-appearance per sorted pallet).
- **wkhtmltopdf charset gotchas** to remember: U+2014 em dash (`—`), U+00B7 middle dot (`·`), U+00D7 multiplication sign (`×`) all render as `Â…` / `Ã…` artifacts. Replaced with plain ASCII (`-`, `|`, `x`).
- **QWeb safe_eval gotchas**: `next()`, `dict()`, `dict.fromkeys()` are NOT in the allowlist. Used `list(set)[0]` and list-of-tuples + manual order-preserving dedupe instead.

### Workflow scripts under `18to19upgrade/workflow/`
- `install_msp_pallet.py` / `install_msp_pallet_now.py` — Odoo.sh upgrade poller (`button_immediate_install/upgrade`)
- `verify_msp_pallet.py` — fields/views/package_type smoke test on staging
- `check_pkg_models.py` / `check_stock_package.py` — schema-discovery probes (used to find v19 model rename)
- `smoke_kiosk_lookup.py` — `GET /api/v1/pallet/lookup/<id>`
- `smoke_kiosk_finalize.py` — `POST /api/v1/production/pallet/finalize` happy path + 400/404 + re-finalize
- `probe_open_deliveries.py` — finds (lot, picking) candidates with unassigned FG move_lines
- `test_pallet_e2e.py` — original Phase 5 e2e (deprecated by reconcile flow but still passes)
- `test_pallet_reconcile_e2e.py` — full lifecycle: pack at first roll → incremental add → combine A→B → unpack-to-free
- `setup_25_test_pallets.py` — generates 25 packages on `WH/OUT/01338` for visual layout validation
- `setup_multiproduct_mixed_test.py` — 12 pure A + 8 pure B + 2 mixed pallets, demonstrates 3-section pick sheet
- `create_msp_pallet_sheet.py` — Phase 6 QWeb pallet sheet upserter (still pre-Phase 7 design — manually verified on staging only since `_render_qweb_pdf` is private to RPC in v19)
- `render_pallet_sheet.py` — PDF render helper (blocked on session auth — see "Known limitations" below)

## Verified end-to-end on staging
- `test_pallet_reconcile_e2e.py`: pack-at-first-roll, incremental add, combine pallets, take-off-pallet — all 4 ops complete in ~8s per sync cycle. PASS.
- Pick sheet rendered repeatedly through the user's manual print testing on `WH/OUT/01338` — current 25-pallet single-product test data and the 22-pallet multi-product+mixed test data both render correctly.

## Known limitations / follow-ups
1. **Odoo's reservation strategy doesn't auto-prefer packaged quants**. When a fresh SO confirms, Odoo reserves loose WH/Stock quants by FIFO instead of grabbing whole pallets. Currently worked around by manually wiring move_lines to packages on `WH/OUT/01338`. Real prod flow needs one of: (a) custom removal strategy, (b) operator-driven package selection in the picking UI, (c) extend `sync_pallet_reconcile_to_odoo` to swap loose-quant reservations on open delivery pickings for the affected lot.
2. **Per-unit weight + unit-number on the pick sheet contents column** — operatorUI's pallet sheet shows individual case weights / unit numbers. Standard Odoo `stock.move.line` doesn't carry these (lives in MES on `MasterRoll`). Need `msp_unit_weight_lb` + `msp_unit_number` custom fields on `stock.move.line`, populated by `sync_pallet_reconcile_to_odoo` when packing.
3. **PDF render via XMLRPC is blocked** — v19 made `_render_qweb_pdf` private to RPC, and the API key isn't accepted as `/web/login` form password. Manual print via Odoo UI is the only path. Used `render_pallet_sheet.py` is non-functional; verification has been manual.
4. **Production rollout pending** — `msp_pallet` addon, the reconcile sync, and the QWeb pick sheet are all on staging. Not on production. Requires explicit user authorization per the strict no-prod rule.
5. **Mixed pallets architectural quirk** — packaging conversion when both products on a mixed pallet have the same effective UoM (e.g., both Case) collapses cleanly. When they differ (Roll vs Case), Units cell renders two stacked lines — works but visually busier than pure pallets.
