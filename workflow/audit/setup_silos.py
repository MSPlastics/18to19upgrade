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
    ("SILO-BUTENE",      "Butene1-BF",            "5615421-01"),
    ("SILO-CLR-REPRO",   "Clear Repro",           None),    # bootstrap: see ensure_inventory_lot
    ("SILO-FRAC",        "Frac1-A",               "12508040A"),
    ("SILO-EXEED",       "Exeed 1018.RA",         "M26010164A"),
    ("SILO-CONSLIP",     "conSLIP fast",          "TEST-2026-05-09-conSLIP-fast-001"),
    ("SILO-CONANTI",     "conANTIBLOCK clarity",  "TEST-2026-05-09-conANTIBLOCK-clarity-001"),
    ("SILO-ENABLE",      "Enable 4002",           "M25120010A"),
    ("SILO-CONANTISTAT", "conANTISTAT-1",         None),    # bootstrap: see ensure_inventory_lot
]

# Materials that lack a real lot+inventory in WH/Stock on staging — the
# audit needs to bootstrap them via inventory adjustment. Each tuple is
# (product_name, lot_name_to_create, qty_to_seed_lb).
NEED_BOOTSTRAP = [
    ("Clear Repro",    "CLR-REPRO-AUDIT-001",   None),    # already has 166k existing untracked qty to reassign
    ("conANTISTAT-1",  "CONANTISTAT-AUDIT-001", 2000.0),  # no qty exists; seed 2000 lb
]


def ensure_inventory_lot(material, lot_name, seed_qty=None):
    """Make sure `material` has a stock.lot named `lot_name` with positive
    inventory in WH/Stock. If the product has untracked positive quants
    in WH/Stock, reassign them to the new lot. If still no positive qty
    and seed_qty is provided, do an inventory adjustment to add it.

    Returns the lot name (== input lot_name) on success.
    """
    prods = s.search_read("product.product", [("name","=",material)], ["id","tracking"])
    if not prods:
        raise SystemExit(f"{material!r} product not found on staging")
    pid = prods[0]["id"]
    tracking = prods[0]["tracking"]
    print(f"  {material} (id {pid}, tracking={tracking}) -> ensure lot {lot_name}")

    # Create lot if missing
    existing = s.search_read("stock.lot", [("name","=",lot_name), ("product_id","=",pid)], ["id"])
    if existing:
        lot_id = existing[0]["id"]
        print(f"    lot exists (id {lot_id})")
    else:
        lot_id = s.call("stock.lot", "create", [{"name": lot_name, "product_id": pid, "company_id": 1}])
        print(f"    created stock.lot -> id {lot_id}")

    # Reassign any no-lot positive quants in WH/Stock to this lot
    no_lot_quants = s.search_read("stock.quant",
        [("product_id","=",pid), ("location_id.name","=","Stock"),
         ("lot_id","=",False), ("quantity",">",0)],
        ["id","quantity"])
    if no_lot_quants:
        total = sum(q["quantity"] for q in no_lot_quants)
        print(f"    reassigning {len(no_lot_quants)} no-lot quant(s) totaling {total:.2f} -> {lot_name}")
        for q in no_lot_quants:
            try:
                s.call("stock.quant", "write", [[q["id"]], {"lot_id": lot_id}])
            except Exception as e:
                print(f"      quant id={q['id']} write FAILED: {e}")

    # Confirm we have positive WH/Stock qty on the lot
    with_lot = s.search_read("stock.quant",
        [("product_id","=",pid), ("location_id.name","=","Stock"), ("lot_id","=",lot_id)],
        ["id","quantity"])
    have_qty = sum(q["quantity"] for q in with_lot)
    print(f"    WH/Stock qty on lot: {have_qty:.2f}")

    if have_qty <= 0 and seed_qty:
        # Seed inventory: find or create WH/Stock quant, set quantity via
        # inventory adjustment workflow.
        wh_stock = s.search("stock.location", [("name","=","Stock"),("usage","=","internal")], limit=1)
        if not wh_stock:
            raise SystemExit("Cannot find WH/Stock location")
        loc_id = wh_stock[0]
        # Find existing quant in WH/Stock with this lot, or create
        existing_q = s.search("stock.quant",
            [("product_id","=",pid), ("location_id","=",loc_id), ("lot_id","=",lot_id)],
            limit=1)
        if existing_q:
            qid = existing_q[0]
        else:
            qid = s.call("stock.quant", "create", [{
                "product_id": pid, "location_id": loc_id, "lot_id": lot_id,
                "quantity": 0.0,
            }])
            print(f"    created stock.quant id={qid}")
        try:
            s.call("stock.quant", "write", [[qid], {"inventory_quantity": float(seed_qty)}])
            s.call_void("stock.quant", "action_apply_inventory", [[qid]])
            print(f"    seeded {seed_qty} via inventory adjustment")
        except Exception as e:
            # Fallback: direct write
            s.call("stock.quant", "write", [[qid], {"quantity": float(seed_qty)}])
            print(f"    seeded {seed_qty} via direct write (adjustment fallback: {e})")
    return lot_name


def main():
    # Bootstrap any materials that need a real lot + WH/Stock inventory first.
    bootstrap_lots = {}
    for material, lot_name, seed_qty in NEED_BOOTSTRAP:
        bootstrap_lots[material] = ensure_inventory_lot(material, lot_name, seed_qty)

    # Patch DESIRED entries with None preferred_lot to use the bootstrapped lot
    for i, (silo_name, material, preferred_lot) in enumerate(DESIRED):
        if preferred_lot is None and material in bootstrap_lots:
            DESIRED[i] = (silo_name, material, bootstrap_lots[material])

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
