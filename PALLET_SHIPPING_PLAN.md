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
