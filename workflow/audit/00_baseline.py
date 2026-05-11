"""Phase 0 - lock the expected-result baseline.

Probes the chosen product, captures BOM/UoM/packaging/route data, and writes
the per-run audit report's "Expected Results" section.

Usage:
    python workflow/audit/00_baseline.py <product_id_or_name> [--qty 50] [--per-pallet 25] [--partner <id>]

Updates:
    - audit_state.json with target_product_id, target_qty, etc.
    - AUDIT_<YYYY-MM-DD>_<product_token>.md (new file or rewrite header)
"""
from __future__ import annotations
import argparse, datetime as _dt, sys
from pathlib import Path
import _common as C

ap = argparse.ArgumentParser()
ap.add_argument("product", help="product id or name/code/barcode")
ap.add_argument("--qty", type=float, default=None, help="SO qty in stock UoM (default: 50)")
ap.add_argument("--per-pallet", type=int, default=None, help="rolls/cases per pallet (default: 25)")
ap.add_argument("--partner", type=int, default=None, help="customer (res.partner) id")
args = ap.parse_args()

s = C.staging
state = C.state

# resolve product
prod = None
try:
    pid = int(args.product)
    res = s.read_one("product.product", pid, ["id","name","code","barcode","uom_id","tracking","is_storable","sale_ok","route_ids","packaging_ids","categ_id"])
    if res: prod = res
except (ValueError, Exception):
    pass
for fld in ("code","barcode","name"):
    if prod: break
    res = s.search_read("product.product", [(fld, "=", args.product)],
        ["id","name","code","barcode","uom_id","tracking","is_storable","sale_ok","route_ids","packaging_ids","categ_id"])
    if res: prod = res[0]
if not prod:
    sys.exit(f"no product matched {args.product!r}")

PROD_ID = prod["id"]
PROD_NAME = prod["name"]
QTY = args.qty if args.qty is not None else 50.0
PER_PALLET = args.per_pallet if args.per_pallet is not None else 25
# EXPECTED_PALLETS is computed AFTER we know FG_ROLL_COUNT (post-packaging math below).

# routes
routes = s.call("stock.route", "read", [prod["route_ids"]], {"fields":["id","name"]}) if prod["route_ids"] else []

# UoM
uom = s.read_one("uom.uom", prod["uom_id"][0], ["id","name","relative_uom_id","relative_factor","factor"])

# Packaging
packs = s.search_read("product.packaging", [("product_id","=",PROD_ID)],
    ["id","name","qty","product_uom_id","sales","purchase","sequence"], order="sequence ASC")

# BOM
boms = s.search_read("mrp.bom",
    ["|", ("product_id","=",PROD_ID),
     "&", ("product_id","=",False), ("product_tmpl_id.product_variant_ids","in",[PROD_ID])],
    ["id","code","product_qty","product_uom_id","type","consumption","operation_ids","bom_line_ids"],
    order="id ASC")
if not boms:
    sys.exit("no BOM for this product")
bom = boms[0]
ops = s.call("mrp.routing.workcenter","read",[bom["operation_ids"]],
    {"fields":["sequence","name","workcenter_id","time_cycle_manual"]}) if bom["operation_ids"] else []
ops.sort(key=lambda o: o["sequence"])
lines = s.call("mrp.bom.line","read",[bom["bom_line_ids"]],
    {"fields":["product_id","product_qty","product_uom_id","operation_id"]}) if bom["bom_line_ids"] else []

# Partner: argument > prior MO's partner > error
PARTNER_ID = args.partner
if not PARTNER_ID:
    # Look at prior MOs to suggest customer
    prior = s.search_read("mrp.production",
        [("product_id","=",PROD_ID),("state","=","done"),("origin","!=",False)],
        ["id","name","origin"], order="id DESC", limit=1)
    if prior:
        so = s.search_read("sale.order", [("name","=",prior[0]["origin"])],
            ["id","name","partner_id","partner_shipping_id"])
        if so:
            PARTNER_ID = so[0]["partner_id"][0]
            partner_shipping = so[0]["partner_shipping_id"][0]
            C.log(f"  -> using partner from prior {so[0]['name']}: {so[0]['partner_id']}")
if not PARTNER_ID:
    sys.exit("no partner provided and no prior SO to infer from -- use --partner <id>")

partner = s.read_one("res.partner", PARTNER_ID, ["id","name","street","city"])
ship_id = None
prior2 = s.search_read("mrp.production",
    [("product_id","=",PROD_ID),("state","=","done"),("origin","!=",False)],
    ["origin"], order="id DESC", limit=1)
if prior2:
    so2 = s.search_read("sale.order", [("name","=",prior2[0]["origin"])],
        ["partner_shipping_id"])
    if so2 and so2[0]["partner_shipping_id"]:
        ship_id = so2[0]["partner_shipping_id"][0]
        ship = s.read_one("res.partner", ship_id, ["id","name","street","city"])

# Multi-step?
MULTISTEP = len(ops) >= 2
FIRST_STEP = ops[0] if ops else None
LAST_STEP = ops[-1] if ops else None
FIRST_STEP_NAME = FIRST_STEP["name"] if FIRST_STEP else "(none)"
FIRST_STEP_WC = FIRST_STEP["workcenter_id"][1] if FIRST_STEP and FIRST_STEP.get("workcenter_id") else "(none)"
LAST_STEP_NAME = LAST_STEP["name"] if LAST_STEP else "(none)"
LAST_STEP_WC = LAST_STEP["workcenter_id"][1] if LAST_STEP and LAST_STEP.get("workcenter_id") else "(none)"
FG_STEP_NAME = LAST_STEP_NAME if MULTISTEP else FIRST_STEP_NAME

# Compute total resin + FG roll count + per-roll weight from BOM/packaging.
# Works for both Roll-stocked (e.g. 11158, packaging.qty=1) and Lb-stocked
# products (e.g. 10083, packaging.qty=70).
TOTAL_RESIN_LB = 0.0   # sum of resin/blend component demand in lb at order qty
ROLL_PACKAGING = next((p for p in packs if (p.get("name","") or "").lower() == "roll"), None)
if ROLL_PACKAGING and ROLL_PACKAGING.get("qty"):
    FG_PER_ROLL = float(ROLL_PACKAGING["qty"])
    FG_ROLL_COUNT = int(QTY // FG_PER_ROLL)
    FG_PER_ROLL_UOM = ROLL_PACKAGING["product_uom_id"][1] if ROLL_PACKAGING.get("product_uom_id") else prod["uom_id"][1]
else:
    # No Roll packaging => the stock UoM is already Roll
    FG_PER_ROLL = 1.0
    FG_ROLL_COUNT = int(QTY)
    FG_PER_ROLL_UOM = prod["uom_id"][1]

# Pallets pack ROLLS not stock-UoM-units, so compute pallet count from FG_ROLL_COUNT.
EXPECTED_PALLETS = int(-(-FG_ROLL_COUNT // PER_PALLET))   # ceil division

# Resin total: BOM `product_qty` is the FG output per BOM unit, lines have
# `product_qty` per BOM unit. Total resin lb = sum(line.qty WHERE uom=lb) *
# (order_qty / bom_product_qty).
bom_unit_qty = float(bom["product_qty"] or 1.0)
scale = QTY / bom_unit_qty if bom_unit_qty else 1.0
for ln in lines:
    uomn = ln["product_uom_id"][1] if ln["product_uom_id"] else ""
    if uomn.lower() in ("lb", "lbs", "pound", "pounds"):
        TOTAL_RESIN_LB += float(ln["product_qty"] or 0.0) * scale

# --- write state ---
token = (prod["code"] or prod["barcode"] or prod["name"] or str(PROD_ID)).strip().replace("/", "_").replace(" ", "_")
state.update(
    target_product_id=PROD_ID,
    target_product_name=PROD_NAME,
    target_product_token=token,
    target_qty=QTY,
    target_uom_id=prod["uom_id"][0],
    target_uom_name=prod["uom_id"][1],
    target_partner_id=PARTNER_ID,
    target_partner_name=partner["name"],
    target_partner_shipping_id=ship_id,
    target_per_pallet=PER_PALLET,
    target_expected_pallets=EXPECTED_PALLETS,
    bom_id=bom["id"],
    multistep=MULTISTEP,
    first_step_name=FIRST_STEP_NAME,
    first_step_wc=FIRST_STEP_WC,
    last_step_name=LAST_STEP_NAME,
    last_step_wc=LAST_STEP_WC,
    fg_step_name=FG_STEP_NAME,
    fg_per_roll=FG_PER_ROLL,
    fg_per_roll_uom=FG_PER_ROLL_UOM,
    fg_roll_count=FG_ROLL_COUNT,
    total_resin_lb=TOTAL_RESIN_LB,
    audit_started_at=_dt.datetime.now().isoformat(timespec="seconds"),
)

# --- write report header ---
date_token = _dt.date.today().isoformat()
report_path = C.ROOT / f"AUDIT_{date_token}_{token}.md"
state.update(report_path=str(report_path))

def fmt_route(r): return f"{r['name']} ({r['id']})"
def fmt_op(o): return f"  {o['sequence']:>3}. {o['name']:<30} on `{o['workcenter_id'][1] if o['workcenter_id'] else '-'}` (cycle {o['time_cycle_manual']})"
def fmt_pkg(p):
    uomn = p['product_uom_id'][1] if p['product_uom_id'] else '-'
    return f"  - {p['name']}: qty {p['qty']} {uomn}, sales={p['sales']}, purchase={p['purchase']}"

lines_md = []
for ln in lines:
    pname = ln['product_id'][1]
    uomn = ln['product_uom_id'][1] if ln['product_uom_id'] else '-'
    op = ln['operation_id'][1] if ln['operation_id'] else 'unassigned'
    lines_md.append(f"  - {pname}: {ln['product_qty']} {uomn} (op: {op})")

ops_md = "\n".join(fmt_op(o) for o in ops) if ops else "  _(no operations)_"
pkg_md = "\n".join(fmt_pkg(p) for p in packs) if packs else "  _(no product.packaging records)_"
routes_md = ", ".join(fmt_route(r) for r in routes) if routes else "_(none)_"
ship_md = f"`{ship['name']}` (id {ship_id})" if ship_id else "_(none captured - SO will use default)_"

content = f"""# AUDIT {date_token} - product `{PROD_NAME}` (id {PROD_ID})

Generated by `workflow/audit/00_baseline.py`. Do not hand-edit the Expected Results section; rerun the script if the inputs change.

## Run inputs

| Field | Value |
|---|---|
| Product id | {PROD_ID} |
| Product name | `{PROD_NAME}` |
| Stock UoM | `{prod['uom_id'][1]}` (id {prod['uom_id'][0]}) |
| Tracking | `{prod['tracking']}` |
| Is storable | `{prod['is_storable']}` |
| Routes | {routes_md} |
| Category | {prod['categ_id'][1] if prod['categ_id'] else '-'} |
| BOM id | {bom['id']} (type=`{bom['type']}`, consumption=`{bom['consumption']}`) |
| Multi-step? | {MULTISTEP} ({len(ops)} operation(s)) |
| FG-producing step | `{FG_STEP_NAME}` (last step in multi-step OR only step in inline) |
| Customer | `{partner['name']}` (id {PARTNER_ID}) |
| Shipping address | {ship_md} |
| Order qty | **{QTY} {prod['uom_id'][1]}** |
| Per pallet | **{PER_PALLET}** -> expected pallet count: **{EXPECTED_PALLETS}** |

## Operations

{ops_md}

## BOM components ({len(lines)})

{chr(10).join(lines_md) if lines_md else "  _(no components)_"}

## Packaging records ({len(packs)})

{pkg_md}

---

## Expected results per stage

### Phase 1+2 - Sale Order

- One SO confirmed (`state=sale`) for partner {PARTNER_ID} with one line: product {PROD_ID} x {QTY} {prod['uom_id'][1]}.
- Auto-creates **{1 if not MULTISTEP else 1} MO** (Manufacture+MTO route).
- MO product_qty = {QTY}, BOM = {bom['id']}.
- Outbound delivery picking auto-created at `WH/OUT/...`.

### Phase 3 - MO sync to MES

- MES `/api/v1/production/orders` returns the new MO.
- BOM expansion: {len(lines)} component lines, {len(ops)} operation(s).
- `produces_fg` flag: True on `{FG_STEP_NAME}`{', False on all other steps' if MULTISTEP else ' (single-step inline)'}.
- Raw lot prompts visible for each component on the relevant step.

### Phase 4 - Production on operator UI

For multi-step (this product **{'IS' if MULTISTEP else 'is NOT'}** multi-step):

{'- Step 1 (`' + ops[0]['name'] + '` on `' + (ops[0]['workcenter_id'][1] if ops[0]['workcenter_id'] else '-') + '`): produces master roll(s) = WIP. `qty_producing` does NOT advance. Raw materials NOT yet consumed at FG level.' if MULTISTEP else '- Single step (`' + ops[0]['name'] + '`): produces FG immediately.'}
{'- Step 2 (`' + ops[-1]['name'] + '` on `' + (ops[-1]['workcenter_id'][1] if ops[-1]['workcenter_id'] else '-') + '`): produces FG roll/case. `qty_producing` advances by 1 per FG unit. Partial-shipment internal transfer fires in `state=done`.' if MULTISTEP else ''}

After full production:
- MES has {int(QTY)} `Roll`/`Pallet` rows.
- Each MES roll has `consumed_lots[*]` populated with the operator-selected silo+line lots.
- Odoo `move_raw_ids[*].move_line.lot_id` matches MES `consumed_lots[*]` (no FIFO substitution).
- MO `qty_producing == {QTY}`.
- A single MO-level FG lot like `MO/<num>-001` exists, attached to the FG move_line.

### Phase 5 - Pallet build + reconcile

- Operator builds **{EXPECTED_PALLETS} pallet(s)** at `/kiosk/pallet-scale`, {PER_PALLET} per pallet.
- Each MES `Pallet` row: `gross_weight_lb` set, `is_finalized=true`, `finalized_at` set.
- For each pallet, an Odoo `stock.package` exists with name `WH/MO/<num>-PAL-<n>`, `package_type_id` = "MSP Pallet", and quants in WH/Stock matching {PER_PALLET} units of FG lot.
- `msp_mo_ids` and `msp_lot_ids` computed fields populated correctly.

### Phase 6 - Pick sheet

- Print "Warehouse Pick Sheet - MSP" on the SO's outgoing delivery.
- {EXPECTED_PALLETS} rows in the unified Pick Checklist.
- Each row shows: pallet name, dims, weight, contents = `{PROD_NAME} x {PER_PALLET} {prod['uom_id'][1]} | lot MO/<num>-001`.
- Per-pallet Units cell = `{PER_PALLET} {prod['uom_id'][1]}` (no packaging conversion - this product has no packaging records, so display is in stock UoM).
- Order Summary at bottom: 1 row, `{PROD_NAME}`, lot `MO/<num>-001`, total {int(QTY)} {prod['uom_id'][1]}.
- Grand Total: `{int(QTY)} {prod['uom_id'][1]}`.

### Phase 7 - Shipping

- Validate the delivery picking.
- All {int(QTY)} `stock.move.line.lot_id` = the MO-level FG lot `MO/<num>-001` (no FIFO from older lots).
- Optional split: ship 30, backorder 20. Backorder picking auto-reserves remaining 20 with **same** FG lot.

### Phase 8 - Invoice

- "Create Invoice" -> regular (delivered qty).
- 1 invoice line: product {PROD_ID}, qty {int(QTY)}, UoM `{prod['uom_id'][1]}` (matches sales UoM).
- Total = qty x SO line price.

### Phase 9 - Lot traceability

- **Backward** from FG lot `MO/<num>-001`: trace through MO -> 6 raw moves -> 6 raw lots:
  - BLEND - 3001 - CLR Repro 3-Layer ({QTY * 46.28:.2f} lb expected)
  - 3 inch cardboard core ({QTY * 56.25:.0f} in expected)
  - 4x1 label units ({int(QTY)} units expected)
  - Poly Wrap ({int(QTY)} units expected)
  - Core Plugs ({int(QTY * 2)} units expected)
  - 4x6 Label ({int(QTY)} units expected)
- **Forward** from any consumed raw lot: trace consumption move -> MO -> FG lot -> delivery -> partner ({partner['name']}).

---

## Pass/fail matrix (filled in as we run)

| Stage | Pass/Fail | Defect (if any) | Severity |
|---|---|---|---|
| 1 - SO created | _pending_ | | |
| 2 - MO auto-created | _pending_ | | |
| 3 - MES sync | _pending_ | | |
| 4 - Production / consumption / FG lot | _pending_ | | |
| 5 - Pallet build + reconcile | _pending_ | | |
| 6 - Pick sheet | _pending_ | | |
| 7 - Shipping | _pending_ | | |
| 8 - Invoice | _pending_ | | |
| 9 - Lot trace backward | _pending_ | | |
| 9 - Lot trace forward | _pending_ | | |

## Per-stage observations (appended by later scripts)

_Each phase script appends a `### <date> Phase N` block here with the actual values it observed and PASS/FAIL._
"""

report_path.write_text(content, encoding="utf-8")
C.log(f"baseline written: {report_path}")
C.log(f"state captured: {state.all()}")
print(f"\nNext: review {report_path.name}, then run 01_create_so.py to confirm the SO.")
