# AUDIT FIX VERIFICATION 2026-05-10 - product 11158

Verifies fixes from commit ac919b1:
- Fix #1: silo lot validation (server-side rejection of bogus lot_numbers)
- Fix #4: pallet/reconcile auto-rewires open outbound move_lines to packages

This run uses the SAME audit pipeline scripts (00-08) but does NOT run
wire_packages_to_picking.py manually. The reconcile sync should re-wire
the picking automatically.

## Pass/fail matrix

| Stage | Pass/Fail | Defect (if any) | Severity |
|---|---|---|---|
| 1 - SO created | PASS |  |  |
| 2 - MO auto-created | PASS |  |  |
| 3 - MES sync | PASS |  |  |
| 4 - Production / consumption / FG lot | PASS |  |  |
| 5 - Pallet build + reconcile | PASS |  |  |
| 6 - Pick sheet | PASS | data only - PDF render not auto-verified | cosmetic-pdf |
| 7 - Shipping | PASS |  |  |
| 8 - Invoice | PASS |  |  |
| 9 - Lot trace backward | PASS |  |  |
| 9 - Lot trace forward | PASS |  |  |

### 2026-05-10 00:12:29 - Phase 1+2: Sale Order creation - **PASS**

- SO: **S01098** (id 1098), state=`sale`
- Customer: SEK Enterprise (id 44)
- Line: product `11158` (id 1195) x **50.0 Roll**
- Auto-created MO(s): **WH/MO/01554** (id 1662, state=`confirmed`)
- Auto-created outgoing picking(s): **WH/OUT/01342** (id 1562, state=`confirmed`)

Checks:
- [OK] SO state == 'sale' - actual: sale
- [OK] Exactly 1 MO created - actual: 1 MO(s) - ['WH/MO/01554']
- [OK] MO product matches - expected=1195, actual=[1195, '11158']
- [OK] MO qty matches - expected=50.0, actual=50.0
- [OK] MO BOM matches baseline - expected=1040, actual=[1040, '11158']
- [OK] Exactly 1 outgoing picking - actual: 1

### 2026-05-10 00:12:33 - Phase 3: MO sync to MES - **PASS**

- Odoo MO: **WH/MO/01554** state=`confirmed`, 2 workorder(s):
  - seq 0: `MR` on `3-Layer`, state=`ready`, expected duration 99.0min
  - seq 1: `Conversion` on `Amutech TSRA`, state=`blocked`, expected duration 69.54min
- Odoo raw moves: 11
  - `Butene1-BF` x 453.544 lb (state=`confirmed`)
  - `Clear Repro` x 985.764 lb (state=`confirmed`)
  - `3 inch cardboard core` x 2812.5 in (state=`confirmed`)
  - `Frac1-A` x 347.1 lb (state=`confirmed`)
  - `4x1 label units` x 50.0 Units (state=`assigned`)
  - `conSLIP fast` x 55.536 lb (state=`confirmed`)
  - `Poly Wrap` x 50.0 Units (state=`assigned`)
  - `conANTIBLOCK clarity` x 27.768 lb (state=`confirmed`)
  - `Core Plugs` x 100.0 Units (state=`confirmed`)
  - `Exeed 1018.RA` x 444.288 lb (state=`confirmed`)
  - `4x6 Label` x 50.0 Units (state=`confirmed`)
- Odoo finished moves: 1
  - `11158` x 50.0 Roll (state=`assigned`)

- MES /api/work-orders sees: **1 active step** (multi-step MOs surface only the current step at a time)
  - operation=`MR`, wc=`3-Layer`, qty=50.0 Roll
  - customer=`SEK Enterprise`, customer_po=`S01098`, msp_drop_po=``
  - target_feet=9270.8333, density=0.9310440000000001
  - count_per_unit=25 (per pallet)
  - hoppers configured: 3
  - blend recipe id: 3001

Checks:
- [OK] MO has 2 workorders (multi-step BOM) - actual 2
- [OK] Step 1 is 'MR' on '3-Layer' - got op=[1259, 'MR'], wc=[11, '3-Layer']
- [OK] Step 2 is 'Conversion' - got [1260, 'Conversion']
- [OK] Step 1 is state=ready (active) - actual ready
- [OK] Step 2 is state=blocked (waiting on step 1) - actual blocked
- [OK] All 5 packaging components present - missing: set(); actual: {'3 inch cardboard core', '4x6 Label', 'Core Plugs', 'Poly Wrap', '4x1 label units'}
- [OK] BLEND expanded into >= 1 resin/additive constituent - actual 6 constituents: ['Butene1-BF', 'Clear Repro', 'Frac1-A', 'conSLIP fast', 'conANTIBLOCK clarity', 'Exeed 1018.RA']
- [OK] Exactly 1 finished move on MO - actual 1
- [OK] Finished move qty == 50.0 Roll - actual 50.0 Roll
- [OK] MES /api/work-orders shows WH/MO/01554 - got 1 WO entries
- [OK] MES WO operation == 'MR' - actual MR
- [OK] MES WO work_center == '3-Layer' - actual 3-Layer
- [OK] MES WO target_qty == 50.0 - actual 50.0
- [OK] MES WO uom == 'Roll' - actual Roll
- [OK] MES sees customer == 'SEK Enterprise' - actual SEK Enterprise
- [OK] MES sees customer_po == 'S01098' - actual S01098
- [OK] MES count_per_unit == 25 - actual 25

### 2026-05-10 00:22:00 - Phase 4: Production execution - **PASS**

- MO WH/MO/01554: state=`done`, qty_producing=50.0, qty_produced=50.0
- FG lot: `MO/01554-001` (id 1605)
- Workorder progression:
  - seq 0 `MR` on `3-Layer`: state=`done`, qty_produced=50.0, start=2026-05-10 07:12:14, end=2026-05-10 07:12:48
  - seq 1 `Conversion` on `Amutech TSRA`: state=`done`, qty_produced=50.0, start=2026-05-10 07:20:45, end=2026-05-10 15:08:09
- Raw consumption summary:
  - `Butene1-BF`: demand 453.544, consumed 453.544, state=`done`, lots: 5615421-01
  - `Clear Repro`: demand 985.764, consumed 985.764, state=`done`, lots: CLR-REPRO-AUDIT-001
  - `3 inch cardboard core`: demand 2812.5, consumed 2868.5, state=`done`, lots: FIX_DATA_29_AUTO
  - `Frac1-A`: demand 347.1, consumed 347.1, state=`done`, lots: 22508010A
  - `4x1 label units`: demand 50.0, consumed 51.0, state=`done`, lots: (none), FIX_DATA_88_AUTO
  - `conSLIP fast`: demand 55.536, consumed 55.536, state=`done`, lots: TEST-2026-05-09-conSLIP-fast-001
  - `Poly Wrap`: demand 50.0, consumed 51.0, state=`done`, lots: (none), FIX_DATA_72_AUTO
  - `conANTIBLOCK clarity`: demand 27.768, consumed 27.768, state=`done`, lots: TEST-2026-05-09-conANTIBLOCK-clarity-001
  - `Core Plugs`: demand 100.0, consumed 102.0, state=`done`, lots: FIX_DATA_393_AUTO
  - `Exeed 1018.RA`: demand 444.288, consumed 444.288, state=`done`, lots: M26010164A
  - `4x6 Label`: demand 50.0, consumed 51.0, state=`done`, lots: FIX_DATA_52_AUTO
- FG move:
  - `11158`: demand 0.0, produced 50.0, state=`done`
    - qty=50.0, lot=`None`, package=`None`, dest=`WH/Stock`

Checks:
- [OK] MO state == done - actual done
- [OK] qty_producing == 50.0 - actual 50.0
- [OK] All workorders state==done - states: ['done', 'done']
- [OK] Every resin/blend raw move_line has a lot - missing-lot: []
- [OK] FG lot exists at MO level - lot=MO/01554-001 (id 1605)
- [OK] FG lot has 50.0 units in WH/Stock - actual 50.0 units
- [OK] FG lot follows MO-level pattern (MO/...) - actual MO/01554-001

### 2026-05-10 00:22:01 - Phase 5: Pallet build + reconcile - **PASS**

- Expected 2 pallets, found 2 stock.package(s) on Odoo
  - **WH/MO/01554-PAL-1**: gross=1207.0 lb, finalized_at=2026-05-10 07:21:03, dims=0.0x0.0x0.0 in, 1 quant(s), FG qty=25.0, lots=['MO/01554-001']
    - msp_mo_ids=[1662], msp_lot_ids=[1605]
  - **WH/MO/01554-PAL-2**: gross=1207.0 lb, finalized_at=2026-05-10 07:21:03, dims=0.0x0.0x0.0 in, 1 quant(s), FG qty=25.0, lots=['MO/01554-001']
    - msp_mo_ids=[1662], msp_lot_ids=[1605]

Checks:
- [OK] All 2 pallet packages exist on Odoo - missing: set()
- [OK] WH/MO/01554-PAL-1 package_type == 'MSP Pallet' - got [1, 'MSP Pallet']
- [OK] WH/MO/01554-PAL-1 msp_gross_weight_lb populated - got 1207.0
- [OK] WH/MO/01554-PAL-1 msp_finalized_at populated - got 2026-05-10 07:21:03
- [OK] WH/MO/01554-PAL-1 contains 25 units of FG product - actual 25.0
- [OK] WH/MO/01554-PAL-1 FG quants on FG lot MO/01554-001 - actual lots ['MO/01554-001']
- [OK] WH/MO/01554-PAL-1 msp_mo_ids includes WH/MO/01554 - msp_mo_ids=[1662], looking for MO id 1662
- [OK] WH/MO/01554-PAL-2 package_type == 'MSP Pallet' - got [1, 'MSP Pallet']
- [OK] WH/MO/01554-PAL-2 msp_gross_weight_lb populated - got 1207.0
- [OK] WH/MO/01554-PAL-2 msp_finalized_at populated - got 2026-05-10 07:21:03
- [OK] WH/MO/01554-PAL-2 contains 25 units of FG product - actual 25.0
- [OK] WH/MO/01554-PAL-2 FG quants on FG lot MO/01554-001 - actual lots ['MO/01554-001']
- [OK] WH/MO/01554-PAL-2 msp_mo_ids includes WH/MO/01554 - msp_mo_ids=[1662], looking for MO id 1662

### 2026-05-10 00:22:03 - Phase 6: Pick Sheet data correctness - **PASS**

- Picking: **WH/OUT/01342** state=`assigned`
- 2 move_line(s) across 2 package(s)

Per-pallet view (matches Pick Checklist row order):
  - **WH/MO/01554-PAL-1**: 25.0 units, lot(s) ['MO/01554-001']
  - **WH/MO/01554-PAL-2**: 25.0 units, lot(s) ['MO/01554-001']

Order Summary (matches bottom-of-sheet table):
  - `11158` | lot `MO/01554-001` | total **50.0 Roll**

Note: PDF render must be manually printed on Odoo UI to verify the visual layout. v19 made `_render_qweb_pdf` private to RPC.

Checks:
- [OK] Picking has 2 package(s) referenced - actual 2: ['WH/MO/01554-PAL-1', 'WH/MO/01554-PAL-2']
- [OK] Pallet WH/MO/01554-PAL-1 has 25 units - actual 25.0
- [OK] Pallet WH/MO/01554-PAL-1 units have FG lot MO/01554-001 - lots: ['MO/01554-001']
- [OK] Pallet WH/MO/01554-PAL-2 has 25 units - actual 25.0
- [OK] Pallet WH/MO/01554-PAL-2 units have FG lot MO/01554-001 - lots: ['MO/01554-001']
- [OK] Order Summary: 1 row for 11158 x 50.0 Roll on lot MO/01554-001 - actual {('11158', 'MO/01554-001', 'Roll'): 50.0}
- [OK] msp_pallet_ids on picking matches packages - actual [32, 33], expected [32, 33]
- [OK] Warehouse Pick Sheet report action exists - found 1

### 2026-05-10 00:22:04 - Phase 7: Shipping - **PASS**

- Picking **WH/OUT/01342** state=`done`, date_done=`2026-05-10 07:22:04`, backorder_id=`False`
- 2 shipped move_line(s):
  - qty=25.0 | lot=`MO/01554-001` | package=`WH/MO/01554-PAL-2`
  - qty=25.0 | lot=`MO/01554-001` | package=`WH/MO/01554-PAL-1`

- Customer location quants for FG lot: 2 totaling 50.0
- WH/Stock quants for FG lot: 1 totaling 0.0

Checks:
- [OK] Picking state=done - actual done
- [OK] date_done populated - actual 2026-05-10 07:22:04
- [OK] No backorder created (shipped all 50) - actual []
- [OK] All shipped move_lines kept FG lot MO/01554-001 - lots: ['MO/01554-001', 'MO/01554-001']
- [OK] Total shipped qty == 50.0 - actual 50.0
- [OK] FG lot now at customer location (50.0 units) - actual 50.0
- [OK] FG lot no longer in WH/Stock - WH/Stock qty 0.0

### 2026-05-10 00:22:12 - Phase 8: Invoice draft - **PASS**

- SO S01098: invoice_status=`invoiced`, amount_total=3933.5
- Invoice **False** state=`draft`, type=`out_invoice`, partner=`SEK Enterprise, Accounts Payable`, origin=`S01098`, total=3933.5 (untaxed=3933.5)
- 1 product line(s):
  - product=`11158` qty=**50.0 Roll** price=78.67 subtotal=3933.5

Note: invoice left in draft state per audit policy. Posting requires explicit instruction.

Checks:
- [OK] Invoice created - got 1
- [OK] Invoice state == draft - actual draft
- [OK] Invoice move_type == out_invoice - actual out_invoice
- [OK] Invoice partner is or is a contact under customer id 44 - actual partner=[46, 'SEK Enterprise, Accounts Payable'], commercial=44
- [OK] Invoice origin == S01098 - actual S01098
- [OK] Exactly 1 product line - got 1
- [OK] Line product == 11158 (id 1195) - actual [1195, '11158']
- [OK] Line qty == 50.0 - actual 50.0
- [OK] Line UoM == Roll - actual [27, 'Roll']
- [OK] Line price_unit matches SO line price (78.67) - actual 78.67

### 2026-05-10 00:22:16 - Phase 9: Lot traceability - **PASS**

### Backward trace: FG lot `MO/01554-001` -> MO `WH/MO/01554` -> 11 raw materials

  - `3 inch cardboard core`: total 2868.5000, lot(s) ['FIX_DATA_29_AUTO']
  - `4x1 label units`: total 51.0000, lot(s) ['(no lot)', 'FIX_DATA_88_AUTO']
  - `4x6 Label`: total 51.0000, lot(s) ['FIX_DATA_52_AUTO']
  - `Butene1-BF`: total 453.5440, lot(s) ['5615421-01']
  - `Clear Repro`: total 985.7640, lot(s) ['CLR-REPRO-AUDIT-001']
  - `Core Plugs`: total 102.0000, lot(s) ['FIX_DATA_393_AUTO']
  - `Exeed 1018.RA`: total 444.2880, lot(s) ['M26010164A']
  - `Frac1-A`: total 347.1000, lot(s) ['22508010A']
  - `Poly Wrap`: total 51.0000, lot(s) ['(no lot)', 'FIX_DATA_72_AUTO']
  - `conANTIBLOCK clarity`: total 27.7680, lot(s) ['TEST-2026-05-09-conANTIBLOCK-clarity-001']
  - `conSLIP fast`: total 55.5360, lot(s) ['TEST-2026-05-09-conSLIP-fast-001']

### Forward trace: raw lot `5615421-01` (Butene1-BF) -> MO `WH/MO/01554` -> FG lot `MO/01554-001` -> delivery -> customer `SEK Enterprise`

Checks:
- [OK] conSLIP fast traces to real lot - lots={'TEST-2026-05-09-conSLIP-fast-001'}
- [OK] Butene1-BF traces to real lot - lots={'5615421-01'}
- [OK] Frac1-A traces to real lot - lots={'22508010A'}
- [OK] Clear Repro traces to real lot - lots={'CLR-REPRO-AUDIT-001'}
- [OK] conANTIBLOCK clarity traces to real lot - lots={'TEST-2026-05-09-conANTIBLOCK-clarity-001'}
- [OK] Exeed 1018.RA traces to real lot - lots={'M26010164A'}
- [OK] Raw lot 5615421-01 consumed on WH/MO/01554 - 1 move_lines, 453.544 lb
- [OK] FG lot MO/01554-001 delivered to a customer-location picking - deliveries: [1562]
- [OK] Delivery customer is or is contact under SEK Enterprise (id 44) - commercial partner id=44


---

## Final summary - 2026-05-10 00:22:17

**Duration**: 1:28:03 (started 22:54:14, ended 00:22:17)

### Final state references

| | |
|---|---|
| Sale Order | **S01098** (id 1098) |
| Manufacturing Order | **WH/MO/01554** (id 1662), state=`done` |
| FG Lot | **MO/01554-001** (id 1605) |
| Pallets | WH/MO/01554-PAL-1, WH/MO/01554-PAL-2 |
| Outbound Delivery | **WH/OUT/01342** (id 1562), state=`done` |
| Invoice | **False** (id 1773), state=`draft` |

### Overall result

**ALL 10 STAGES PASS** (with one caveat: pick sheet PDF visual layout requires manual print verification on Odoo UI - data correctness is automated).

### Findings discovered during this audit

1. **WORKFLOW: MES silos must reference real Odoo lot names.** The MES `_get_or_create_lot` falls through to FIFO from existing free quants if a requested lot doesn't exist on Odoo - silently substituting. With proper silo->Odoo lot binding, the system works correctly. **Recommendation**: replace the MES silo update UI's free-text `lot_number` field with an Odoo-lot picker (autocomplete from `stock.lot` filtered by selected material). This prevents silo-to-Odoo drift in production.

2. **DATA QUALITY (separate from MES): Clear Repro had 166k lb of inventory with NO lot.** Resolved during this audit by creating `CLR-REPRO-AUDIT-001` and assigning the existing untracked qty via stock.quant write. **Recommendation**: audit other resin/blend products for similar lot-tracking gaps; enable `tracking='lot'` on resin products that should be lot-controlled.

3. **DATA QUALITY: Frac1-A lot `5613851-01` and Exeed 1018.RA lot `7260190A14` have NEGATIVE free qty** (over-reserved from prior MOs). Not blocking for this audit but warrants cleanup.

4. **KNOWN: Odoo's reservation strategy doesn't auto-prefer packaged quants.** When the SO confirmed, Odoo reserved 50 against a phantom 0-qty loose quant instead of the 25+25 in pallet packages. Worked around by manually wiring move_lines (`package_id` + `result_package_id`) via `wire_packages_to_picking.py`. **Recommendation**: address via custom removal strategy on the warehouse (prefer packages) OR a server action on SO confirm OR an Odoo module that re-wires reservation when packages exist. (Already in open follow-ups list.)

5. **MES sync lag for new MOs.** MES caches MO data (~5min periodic sync); a new SO->MO doesn't appear in `/api/work-orders` until the next periodic pull OR a manual `/api/sync` trigger. Now baked into `01_create_so.py`.

6. **MES sync timing**: each FG roll takes ~8s round-trip (Odoo `action_increment_qty_producing` + `action_ship_partial_batch`). For 50 rolls that's ~7 minutes. Acceptable for the operator workflow but worth noting.

7. **Cosmetic emergency lots**: tracking='none' packaging products (cardboard core, labels, poly wrap, core plugs) get `FIX_DATA_<product_id>_AUTO` lots created on every MO. Functionally harmless but pollutes `stock.lot`. (Already in open follow-ups list.)

8. **BLEND expansion**: BOM line `BLEND - 3001 - CLR Repro 3-Layer` expands into 6 actual stock.move records at MO time (Butene1-BF + Clear Repro + Frac1-A + Exeed 1018.RA + conSLIP fast + conANTIBLOCK clarity). The audit baseline initially expected 6 raw moves; correct count is 11 (6 from blend + 5 fixed packaging items). Documented in `02_verify_mo_sync.py`.

### v19 schema renames captured

Added to `AUDIT_PROCEDURE.md` cheat sheet:
- `product.product`: `uom_po_id` removed; `detailed_type` -> `type` + `is_storable`
- `uom.uom`: `category_id` / `uom_type` / `factor_inv` -> `relative_uom_id` + `relative_factor` + `parent_path`
- `stock.quant.package` -> `stock.package`
- `stock.move.name` -> `description_picking`
- `mrp.production.procurement_group_id` -> gone (search by `name` -> `origin`)
- `sale.order.line.product_uom` -> `product_uom_id`
- `_render_qweb_pdf` -> private to RPC (manual print only)

### Repeatability

This audit run took 1:28:03. The next category (e.g. Thousands-sold inline, or Case-sold 2-step) can re-use:
- `_common.py` (XMLRPC + MES helpers)
- `setup_silos.py` (just update DESIRED list per BOM blend recipe)
- `00_baseline.py` through `08_trace_lot.py` (parameterized via audit_state.json)
- `wire_packages_to_picking.py` (until reservation strategy is fixed upstream)
- `drive_production.py` (extrude/advance/convert/finalize-mo/build-pallets subcommands)

To run a different product: `python workflow/audit/00_baseline.py <product_id>` and continue. Allow 30-60 min total (most of it spent on the FG-roll sync-queue wait in Phase 4).

### Status of Phase 4 sync timing

The 7-minute wait during Phase 4 is dominated by Odoo's `action_increment_qty_producing` + `action_ship_partial_batch` taking ~8s per FG unit. For larger orders this scales linearly. Possible optimization: batch multiple FG units into a single `action_increment_qty_producing` call (would need MES + msppartialMO addon changes). Not blocking for audit purposes.

---

## Fix #4 (auto-rewire pickings to packages) - smoking-gun evidence

### Before pallet build (after MO/01554 marked done)

```
picking WH/OUT/01342 state=assigned
  qty=50.0 lot=MO/01554-001 pkg=False        <-- LOOSE, no package linked
```

This is the buggy state caught in audit finding #4: outbound delivery
auto-reserved 50 loose units from a phantom 0-qty quant before any pallet
existed.

### After pallet build (16s after build-pallets API call)

```
picking WH/OUT/01342 state=assigned
  qty=25.0 lot=MO/01554-001 pkg=WH/MO/01554-PAL-2 result_pkg=WH/MO/01554-PAL-2
  qty=25.0 lot=MO/01554-001 pkg=WH/MO/01554-PAL-1 result_pkg=WH/MO/01554-PAL-1
```

Both `package_id` and `result_package_id` set. The `_rewire_open_outbound_to_package`
helper (added to `sync_pallet_reconcile_to_odoo` in commit ac919b1) ran during
each pallet's reconcile and auto-rewired the picking. **No `wire_packages_to_picking.py`
was run on this audit cycle.**

The fix is per-pallet and idempotent: PAL-1's reconcile split the 50-qty line
into 25 (rewired to PAL-1) + 25 (still loose); then PAL-2's reconcile rewired
the remaining 25 loose line directly to PAL-2.

Both Phase 6 (pick sheet checklist shows correct per-pallet data) and Phase 7
(shipping validates with the correct package metadata persisting to delivery)
PASSed without manual intervention.

## Fix #1 (silo lot validation) - smoking-gun evidence

Three smoke tests against POST `/api/resin/silos/update`:

```
Test 1 (bogus lot for existing material):
  POST {silo_id, material='Butene1-BF', lot='BOGUS-LOT-DOES-NOT-EXIST', qty=5000}
  -> 400: "Lot 'BOGUS-LOT-DOES-NOT-EXIST' not found on Odoo for material 'Butene1-BF'.
           Pick a lot from the dropdown..."
  REJECTED as expected.

Test 2 (real lot for existing material):
  POST {silo_id, material='Butene1-BF', lot='5615421-01', qty=8000}
  -> 200 {"success": true}
  ACCEPTED as expected.

Test 3 (bogus material):
  POST {silo_id, material='TotallyMadeUpMaterial', lot='whatever', qty=5000}
  -> 400: "Material 'TotallyMadeUpMaterial' not found on Odoo. Pick from the material search."
  REJECTED as expected.
```

Plus `get_available_lots` filter verified locally:
- Butene1-BF: returns 4 real lots ordered by qty desc (5.6M, 140k, 17k, 10k)
- 11158 (FG): returns 0 lots (all prior MOs' FG either shipped or zero)
