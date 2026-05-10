"""Idempotently configure MES silos to point at REAL Odoo stock.lot records.

The architectural rule (set 2026-05-09): MES silos must reference Odoo lots
that actually exist with positive inventory, NOT free-text invented lot names.
The MES silo update endpoint currently accepts free text - audit recommendation
is to add an Odoo-lot picker UI in a follow-up.

For each material:
  1. Find a stock.lot on Odoo with positive free qty in WH/Stock (FIFO).
  2. If none exists (Clear Repro on staging), create a real lot AND assign
     existing untracked inventory to it via stock.quant write.
  3. Update the MES silo to reference that exact lot name.
"""
from __future__ import annotations
import _common as C

s = C.staging

# (silo_name, material_name, preferred_lot_or_None)
# preferred_lot is the Odoo lot we want; if None we pick FIFO from positive-free.
DESIRED = [
    ("SILO-BUTENE",    "Butene1-BF",            "5615421-01"),
    ("SILO-CLR-REPRO", "Clear Repro",           None),                                    # see special handling
    ("SILO-FRAC",      "Frac1-A",               "22508010A"),
    ("SILO-EXEED",     "Exeed 1018.RA",         "M26010164A"),
    ("SILO-CONSLIP",   "conSLIP fast",          "TEST-2026-05-09-conSLIP-fast-001"),
    ("SILO-CONANTI",   "conANTIBLOCK clarity",  "TEST-2026-05-09-conANTIBLOCK-clarity-001"),
]


def ensure_clear_repro_lot():
    """Clear Repro has 166k lb in WH/Stock but no stock.lot. Create one + assign quants."""
    prods = s.search_read("product.product", [("name","=","Clear Repro")], ["id","tracking"])
    if not prods:
        raise SystemExit("Clear Repro product not found on staging")
    pid = prods[0]["id"]
    tracking = prods[0]["tracking"]
    print(f"  Clear Repro product id={pid}, tracking={tracking}")

    if tracking == "none":
        # Lot tracking is disabled at the product level - we need to enable it,
        # but flipping product.tracking from 'none' to 'lot' is a heavy schema change.
        # For now, check if there's still an option (Odoo may allow lot on tracked moves).
        print(f"  WARN: Clear Repro tracking='none' - Odoo will not enforce lots even if we provide them.")
        print(f"        Consumption sync will still try to write lot_id, which Odoo accepts but doesn't audit.")

    LOT_NAME = "CLR-REPRO-AUDIT-001"
    existing = s.search_read("stock.lot", [("name","=",LOT_NAME), ("product_id","=",pid)],
                              ["id","name"])
    if existing:
        lot_id = existing[0]["id"]
        print(f"  lot {LOT_NAME} already exists (id {lot_id})")
    else:
        lot_id = s.call("stock.lot", "create", [{"name": LOT_NAME, "product_id": pid, "company_id": 1}])
        print(f"  created stock.lot {LOT_NAME} -> id {lot_id}")

    # Find no-lot quants of Clear Repro in WH/Stock and assign to this lot
    no_lot_quants = s.search_read("stock.quant",
        [("product_id","=",pid), ("location_id.name","=","Stock"),
         ("lot_id","=",False), ("quantity",">",0)],
        ["id","quantity","reserved_quantity","location_id"])
    if no_lot_quants:
        total = sum(q["quantity"] for q in no_lot_quants)
        print(f"  found {len(no_lot_quants)} no-lot quant(s) totaling {total:.2f} lb - assigning to {LOT_NAME}")
        for q in no_lot_quants:
            try:
                s.call("stock.quant", "write", [[q["id"]], {"lot_id": lot_id}])
                print(f"    quant id={q['id']} ({q['quantity']:.2f} lb) -> lot {lot_id}")
            except Exception as e:
                print(f"    quant id={q['id']} write FAILED: {e}")
    else:
        # Maybe it's already assigned. Check.
        with_lot = s.search_read("stock.quant",
            [("product_id","=",pid), ("location_id.name","=","Stock"),
             ("lot_id","=",lot_id)],
            ["quantity","reserved_quantity"])
        total = sum(q["quantity"] for q in with_lot)
        print(f"  no untracked quants found; quants already on lot {LOT_NAME} total {total:.2f} lb")

    return LOT_NAME


def main():
    # Special-case Clear Repro
    clr_lot = ensure_clear_repro_lot()
    DESIRED[1] = ("SILO-CLR-REPRO", "Clear Repro", clr_lot)

    silos = C.mes.get("/api/resin/silos")
    if isinstance(silos, dict) and "_error" in silos:
        raise SystemExit(f"failed to read silos: {silos}")

    by_name = {s2["name"]: s2 for s2 in silos}
    print(f"\nexisting {len(silos)} silos: {sorted(by_name.keys())}")

    for name, material, preferred_lot in DESIRED:
        # Resolve the lot name to use
        lot_name = preferred_lot
        if not lot_name:
            # Fallback: pick FIFO positive-free from Odoo
            prods = s.search_read("product.product", [("name","=",material)], ["id"])
            if not prods:
                print(f"  ! material {material} not found on Odoo, skipping {name}")
                continue
            pid = prods[0]["id"]
            quants = s.search_read("stock.quant",
                [("product_id","=",pid), ("location_id.name","=","Stock"), ("quantity",">",0)],
                ["lot_id","quantity","reserved_quantity"], order="in_date ASC")
            for q in quants:
                if q["lot_id"] and (q["quantity"] - q["reserved_quantity"] > 1):
                    lot_name = q["lot_id"][1]
                    break
            if not lot_name:
                print(f"  ! no positive-free lot found for {material}, skipping {name}")
                continue

        # Verify lot actually exists on Odoo
        prods = s.search_read("product.product", [("name","=",material)], ["id"])
        pid = prods[0]["id"]
        lot_ck = s.search_read("stock.lot", [("name","=",lot_name), ("product_id","=",pid)], ["id","name"])
        if not lot_ck:
            print(f"  ! lot {lot_name!r} not found on Odoo for {material}, skipping silo {name}")
            continue

        # Add silo if missing
        if name not in by_name:
            print(f"  + adding silo {name}")
            r = C.mes.post("/api/resin/silos/add", {"name": name, "capacity": 100000.0, "location": "Outside"})
            if isinstance(r, dict) and "_error" in r:
                print(f"    add FAILED: {r}")
                continue
            silos2 = C.mes.get("/api/resin/silos")
            by_name = {s2["name"]: s2 for s2 in silos2}

        sid = by_name[name]["id"]
        cur_mat = by_name[name].get("material_name")
        cur_lot = by_name[name].get("lot_number")
        if cur_mat == material and cur_lot == lot_name:
            print(f"  = silo {name}: already set to {material!r} | {lot_name!r}")
            continue
        print(f"  ~ updating silo {name}: material={material!r}, lot={lot_name!r}")
        r = C.mes.post("/api/resin/silos/update", {
            "silo_id": sid, "material_name": material, "lot_number": lot_name,
            "quantity": 8000.0,
        })
        if isinstance(r, dict) and "_error" in r:
            print(f"    update FAILED: {r}")
        else:
            print(f"    OK")

    print("\nfinal silo state:")
    silos = C.mes.get("/api/resin/silos")
    for s2 in silos:
        print(f"  {s2['name']:<20}  material={s2['material_name'] or '-':<25}  lot={s2['lot_number'] or '-':<45}  qty={s2['quantity']}")


if __name__ == "__main__":
    main()
