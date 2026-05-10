"""
Backward verification view: given an MO id, print every raw consumption
move_line + the finished-goods state, grouped by product, with the lot
from each move_line shown. This is the "open a work order, see what
raw lot was consumed" check.

Usage:
    python view_mo_consumption.py [MO_ID]
    (defaults to 1583 / WH/MO/01479)

Reads ODOO_STAGING_* from .env.
"""
import os
import sys
import xmlrpc.client
from pathlib import Path


def _load_dotenv():
    p = Path(__file__).parent.parent / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

URL = os.environ["ODOO_STAGING_URL"]
DB = os.environ["ODOO_STAGING_DB"]
USER = os.environ.get("ODOO_STAGING_USER", "admin@mountainstatesplastics.com")
KEY = os.environ["ODOO_STAGING_API_KEY"]

DEFAULT_MO_ID = 1583


def connect():
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, KEY, {})
    return uid, xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)


def call(models, uid, model, method, args, kw=None):
    return models.execute_kw(DB, uid, KEY, model, method, args, kw or {})


def main():
    mo_id = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MO_ID
    uid, models = connect()

    mo = call(models, uid, "mrp.production", "read", [[mo_id]],
              {"fields": ["name", "state", "product_id", "product_qty",
                          "qty_producing", "product_uom_id",
                          "x_studio_total_wgt_mo", "lot_producing_ids",
                          "move_raw_ids", "move_finished_ids",
                          "workorder_ids", "bom_id", "origin"]})[0]

    print(f"\n{'='*75}")
    print(f"  MO {mo['name']} (id={mo['id']}) - {mo['product_id'][1]}")
    print(f"  state={mo['state']}  target={mo['product_qty']:.2f} {mo['product_uom_id'][1]}  "
          f"producing={mo['qty_producing']:.2f}  total_wgt_planned={mo.get('x_studio_total_wgt_mo'):.2f} lb")
    print(f"  bom={mo['bom_id'][1] if mo['bom_id'] else '-'}  origin={mo.get('origin') or '-'}")
    print(f"{'='*75}")

    # Workorders (steps)
    wos = call(models, uid, "mrp.workorder", "read", [mo["workorder_ids"]],
               {"fields": ["name", "state", "workcenter_id", "qty_producing", "qty_production"]})
    print("\n  Workorders (steps):")
    for w in sorted(wos, key=lambda x: x["id"]):
        print(f"    [{w['id']:>4}] {w['name']:<22} wc={w['workcenter_id'][1]:<14} "
              f"state={w['state']:<10} producing={w['qty_producing']:.2f} target={w['qty_production']:.2f}")

    # FG lots on the MO
    if mo["lot_producing_ids"]:
        lots = call(models, uid, "stock.lot", "read", [mo["lot_producing_ids"]],
                    {"fields": ["name", "product_qty"]})
        print("\n  Finished Goods lot(s) on MO:")
        for l in lots:
            print(f"    [{l['id']:>5}] {l['name']:<40} on_hand={l['product_qty']:.2f}")
    else:
        print("\n  Finished Goods lot(s) on MO: NONE")

    # Raw moves with move_lines
    moves = call(models, uid, "stock.move", "read", [mo["move_raw_ids"]],
                 {"fields": ["product_id", "product_uom_qty", "quantity",
                             "state", "picked", "move_line_ids", "product_uom"]})
    print(f"\n  Raw consumption ({len(moves)} components):")
    print(f"    {'Material':<32} | {'demand':>9} | {'total qty':>9} | picked | state    | lines")
    print(f"    {'-'*32}-+-{'-'*9}-+-{'-'*9}-+--------+-----------+------")
    total_consumed_lb = 0.0
    by_lot = {}  # for traceability roll-up
    for mv in sorted(moves, key=lambda x: x["product_id"][1]):
        p = mv["product_id"][1][:30]
        uom = mv["product_uom"][1] if mv["product_uom"] else "?"
        print(f"    {p:<32} | {mv['product_uom_qty']:>9.2f} | {mv['quantity']:>9.4f} | "
              f"{str(mv['picked']):<6} | {mv['state']:<9} | {len(mv['move_line_ids'])}")
        if "lb" in uom:
            total_consumed_lb += mv["quantity"]
        if mv["move_line_ids"]:
            lines = call(models, uid, "stock.move.line", "read", [mv["move_line_ids"]],
                         {"fields": ["product_id", "lot_id", "quantity", "state",
                                     "create_date"]})
            for ln in sorted(lines, key=lambda x: x["create_date"] or ""):
                lot_name = ln["lot_id"][1] if ln["lot_id"] else "(no lot)"
                print(f"        -> {ln.get('create_date', '')[:19]}  qty={ln['quantity']:>8.4f}  lot={lot_name}  state={ln['state']}")
                if ln["lot_id"]:
                    key = (mv["product_id"][1], lot_name)
                    by_lot.setdefault(key, 0.0)
                    by_lot[key] += ln["quantity"]

    print(f"\n  Total raw weight consumed (lb-only fields): {total_consumed_lb:.2f} lb")

    # Finished move(s)
    if mo["move_finished_ids"]:
        fmoves = call(models, uid, "stock.move", "read", [mo["move_finished_ids"]],
                      {"fields": ["product_id", "product_uom_qty", "quantity",
                                  "state", "picked", "move_line_ids", "product_uom"]})
        print(f"\n  Finished moves ({len(fmoves)}):")
        for fm in fmoves:
            uom = fm["product_uom"][1] if fm["product_uom"] else "?"
            print(f"    {fm['product_id'][1][:32]:<32} target={fm['product_uom_qty']:.2f} {uom}  "
                  f"qty_done={fm['quantity']:.2f}  picked={fm['picked']}  state={fm['state']}  lines={len(fm['move_line_ids'])}")
            if fm["move_line_ids"]:
                fl = call(models, uid, "stock.move.line", "read", [fm["move_line_ids"]],
                          {"fields": ["lot_id", "quantity", "state"]})
                for ln in fl:
                    lot_name = ln["lot_id"][1] if ln["lot_id"] else "(no lot)"
                    print(f"        -> qty={ln['quantity']:>8.2f}  lot={lot_name}  state={ln['state']}")

    # Customer-traceability roll-up: for each material, which lots and how much
    print(f"\n  Material -> Lot consumption summary (the 'what raw lot fed this WO' view):")
    print(f"    {'Material':<32} | {'Lot':<42} | {'Qty':>10}")
    print(f"    {'-'*32}-+-{'-'*42}-+-{'-'*10}")
    for (mat, lot), qty in sorted(by_lot.items()):
        print(f"    {mat[:30]:<32} | {lot[:40]:<42} | {qty:>10.4f}")

    print()


if __name__ == "__main__":
    main()
