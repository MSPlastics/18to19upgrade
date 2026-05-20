# MSP End-to-End Lifecycle Audit Procedure

Repeatable, scripted full-system audit covering: SO -> MO sync to MES -> production on operator UI -> raw consumption with lots -> case/roll/pallet recording -> reconcile to Odoo -> picking -> shipping -> invoicing -> bidirectional lot traceability.

## Scope rule

- **Staging Odoo only**: `https://msplastics-odoo18-19-upgradetest2-31982255.dev.odoo.com`
- **Cloud test MES only**: `https://34.67.173.228.nip.io` (the `mes-testing` GCP VM)
- **Never touch production Odoo or production MES VM** unless the user gives explicit per-turn authorization.

## Layout

```
18to19upgrade/
  AUDIT_PROCEDURE.md                  this file (the rubric)
  AUDIT_<date>_<product>.md           per-run report
  workflow/audit/
    _common.py                        shared XMLRPC + MES helpers
    audit_state.json                  cross-script state (SO id, MO id, lot id, ...)
    audit_run.log                     timestamped log of every script invocation
    probe_product.py                  read-only product/BOM probe
    find_product.py                   product search by partial code/name/barcode
    00_baseline.py                    write expected-result block to current report
    01_create_so.py                   create the SO + confirm
    02_verify_mo_sync.py              MO landed on Odoo + MES with correct steps/BOM
    03_observe_production.py          live state poll during operator UI runs
    04_verify_pallets.py              pallet build + reconcile state correctness
    05_verify_pick_sheet.py           pick sheet contents + UoM math
    06_verify_shipping.py             delivery + FG lot persistence + backorder
    07_verify_invoice.py              invoice draft correctness
    08_trace_lot.py                   backward + forward lot trace
```

Every script reads/writes `audit_state.json` so chains are scriptable. Every script appends a structured block to the current per-run report file.

## Lifecycle stages

For each stage the table below lists: scripted check, manual action (if any), pass criteria, and known failure modes worth checking first when something breaks.

### Phase 0 - Lock the expected-result baseline

| | |
|---|---|
| Script | `00_baseline.py <product_id_or_code>` |
| Action | Probe product + BOM + recent successful MO. Write the expected per-stage outcome table to `AUDIT_<date>_<product>.md`. |
| Pass | Baseline file exists with: product card, BOM tree, expected raw lot consumption, expected FG lot pattern, expected pallet count, expected pick sheet UoM, expected delivery+invoice line shape. |
| Failure modes | (1) v19 schema renames - probe will throw `Invalid field` -> add to v19 rename list in this doc. (2) BOM `product_id` is False -> it is template-level; search by `product_tmpl_id`. |

### Phase 1+2 - Create the SO

| | |
|---|---|
| Script | `01_create_so.py` (reads `target_product_id`, `target_qty`, `target_partner_id` from `audit_state.json`) |
| Action | Create `sale.order` with one line, confirm it, save SO id + auto-generated MO id(s) to state. |
| Pass | SO state == `sale`, MOs auto-created via Manufacture+MTO route, MO product+qty+UoM matches SO line. |
| Failure modes | Routes misconfigured -> no MO. MTO fallback supplier instead of Manufacture -> MO created on wrong route. |

### Phase 3 - Verify MO sync to MES

| | |
|---|---|
| Script | `02_verify_mo_sync.py` |
| Action | Poll MES `/api/v1/production/orders` for the MO; compare BOM expansion, op count, raw materials, and `produces_fg` flag per step against expected. |
| Pass | MES has the MO with correct steps; raw lot prompts present; `produces_fg` true ONLY on last step of multi-step OR single-step inline. |
| Failure modes | MES sync_engine queue stuck -> check `sync_queue` table. Studio fields missing on Odoo product -> blank fields on MES side. Blend-data drift -> additives skip via the "MES provided blend but resin not in it" gate. |

### Phase 4 - Production on operator UI

| | |
|---|---|
| Script | `03_observe_production.py --watch` (polls every 5s, logs delta) |
| Action | **Manual on cloud test MES**: operator selects silo/line lots, records master roll(s) (extrusion step), records FG roll/case (converting step). Watcher captures state after each event. |
| Pass | Per step: MES `Roll`/`Pallet` row created with correct lot+silo/line; Odoo `move_raw_ids[*].move_line.lot_id` populated from MES `consumed_lots[*]` (NOT FIFO fallback); `qty_producing` advances ONLY on FG-producing step; for multi-step, partial-shipment internal transfer fires in `state=done` on FG step. |
| Failure modes | (1) `consumed_lots[*]` ignored -> Odoo picks FIFO. (2) Master rolls advancing `qty_producing` -> WIP-not-FG rule broken. (3) Settings-cache stale after recent settings POST -> sync uses stale config. (4) Operator-reported qtys lost after `_set_qty_producing` -> need 6a5bf3d-style restore. |

### Phase 5 - Pallet build + reconcile

| | |
|---|---|
| Action | **Manual on cloud test MES**: operator scans pallet at `/kiosk/pallet-scale`, weighs, finalizes. |
| Script | `04_verify_pallets.py` |
| Pass | MES `Pallet` row has `gross_weight_lb`, `is_finalized=true`, `finalized_at`. Odoo `stock.package` exists with matching name (`WH/MO/<n>-PAL-<m>`). Quants in package match MES rolls grouped by FG lot. `msp_mo_ids` and `msp_lot_ids` computes resolve correctly. Reconcile sync queue empty or done. |
| Failure modes | Reconcile sync stuck (`pallet/reconcile`) -> check sync_queue. v19 model rename (`stock.quant.package` -> `stock.package`) -> check addon manifest. `do_unreserve` returns None over XMLRPC -> use `call_void` wrapper. |

### Phase 6 - Pick sheet

| | |
|---|---|
| Action | **Manual on staging Odoo**: open the SO's Delivery picking, Print -> "Warehouse Pick Sheet - MSP". |
| Script | `05_verify_pick_sheet.py` (renders the QWeb HTML + checks contents programmatically) |
| Pass | Every pallet listed; lot visible per row; UoM conversion correct (sales UoM != stock UoM cases use packaging.qty); Order Summary at bottom matches checklist order; Grand Total per-UoM breakdown sums correctly. |
| Failure modes | (1) wkhtmltopdf encoding artifacts (`Â…`/`Ã…`) -> always use plain ASCII (`-`, `|`, `x`) in QWeb. (2) QWeb safe_eval rejecting `next()`/`dict()`/`dict.fromkeys()` -> use `list(set)[0]` and list-of-tuples. (3) Reservation strategy not preferring packaged quants -> Odoo grabs loose WH/Stock instead of packages. (4) Product packaging records missing -> conversion falls through to stock UoM. |

### Phase 7 - Shipping

| | |
|---|---|
| Action | **Manual on staging Odoo**: validate the delivery picking. Optionally split: ship N, backorder rest. |
| Script | `06_verify_shipping.py` |
| Pass | Delivery `state=done`. Every shipped `stock.move.line.lot_id` is the MO-level FG lot (e.g. `MO/01459-001`), NOT a FIFO-picked older lot. Backorder picking auto-reserves with the same MO-level lot pattern. |
| Failure modes | Same as Phase 6 reservation strategy. Customer-paperwork PDFs not visually checked -> data correct but render not validated. |

### Phase 8 - Invoice

| | |
|---|---|
| Action | **Manual on staging Odoo**: from the SO, "Create Invoice" -> regular invoice (delivered qty). |
| Script | `07_verify_invoice.py` (reads draft, asserts shape) |
| Pass | Invoice line product+qty matches delivered qty; UoM matches sales UoM (not stock UoM); price+totals reconcile with SO line. |
| Failure modes | UoM mismatch when sales UoM != stock UoM. Studio computed fields not re-running on draft. |

### Phase 9 - Lot traceability (both directions)

| | |
|---|---|
| Script | `08_trace_lot.py --backward <fg_lot>` and `--forward <raw_lot>` |
| Pass | (Backward) FG lot -> MO -> all raw move_lines with lots -> raw lot id chain. (Forward) Raw lot -> consumption move(s) -> MO -> FG lot -> delivery -> customer. |
| Failure modes | `view_mo_consumption.py` uses pre-v19 fields -> use `description_picking` not `name`. |

### Phase 10 - Final report

Append a pass/fail matrix to `AUDIT_<date>_<product>.md`:
```
| Stage | Pass/Fail | Defect (if any) | Severity |
```

## v19 schema rename cheat sheet (additive - capture as you discover)

| Model | Removed/Renamed | v19 replacement |
|---|---|---|
| `product.product` | `uom_po_id` | gone (use `uom_id` only) |
| `product.product` | `detailed_type` | `type` + `is_storable` |
| `uom.uom` | `category_id` / `uom_type` / `factor_inv` | `relative_uom_id` + `relative_factor` + `parent_path` |
| `stock.quant.package` | model rename | `stock.package` |
| `stock.move.name` | removed | `description_picking` |
| `mrp.production` | `procurement_group_id` | gone (search by `name` -> `origin` instead) |
| `sale.order.line` | `product_uom` | `product_uom_id` |
| `_render_qweb_pdf` | private to RPC | manual print only |

## Common gotchas (running list)

- **Routes [1,6]** = MTO + Manufacture on staging.
- **Cloud test MES API key**: `msplastics-mes-2026-61bf306c6d2e5ede` (set in `/etc/mes.env` on the VM).
- **MES restart after settings change**: `gcloud compute ssh mes-testing --zone=us-central1-a --command="cd /opt/mes && sudo -u anthony git pull && sudo systemctl restart mes"`
- **`call_void` wrapper** required for any XMLRPC call to a method that returns None (e.g. `do_unreserve`, `unlink`).
- **BOMs sometimes have `product_id=False`** when defined at template level; search by `product_tmpl_id.product_variant_ids`.
- **Tracking='none' products still get a forced MO-level lot** via MES; FG move_line carries `MO/<n>-001` even though product is untracked. Lot traceability still works.

## Running the audit

```bash
cd 18to19upgrade

# 0. Probe the candidate product (pick and confirm)
python workflow/audit/probe_product.py 11158
# -> resolves to id 1195

# 1. Lock the baseline
python workflow/audit/00_baseline.py 1195
# -> creates AUDIT_<date>_<product>.md with expected-result block, populates audit_state.json

# 2. Create SO (state-driven)
python workflow/audit/01_create_so.py
# -> writes so_id + mo_ids to state, appends SO block to report

# 3-9. Run each phase script in order (or mid-run after manual operator UI events).
```

When a phase fails: leave the script's PASS/FAIL block in the report, capture the actual vs expected diff, then either fix the underlying defect (separate commit) and re-run, or note as known issue and continue.
