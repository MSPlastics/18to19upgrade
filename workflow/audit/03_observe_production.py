"""Phase 4 - observe live production state during operator UI runs.

Run this after each operator action (record roll, finish step, etc.) to
snapshot MES + Odoo state and surface deltas.

Modes:
    python workflow/audit/03_observe_production.py            # one-shot snapshot
    python workflow/audit/03_observe_production.py --watch    # poll every 8s, print deltas
    python workflow/audit/03_observe_production.py --finalize # write Phase 4 PASS/FAIL block (run when production done)

Snapshot file: workflow/audit/audit_state_phase4.json (overwritten each run except --finalize)
"""
from __future__ import annotations
import argparse, datetime as _dt, json, time
from pathlib import Path
import _common as C

ap = argparse.ArgumentParser()
ap.add_argument("--watch", action="store_true")
ap.add_argument("--finalize", action="store_true")
ap.add_argument("--interval", type=int, default=8)
args = ap.parse_args()

s = C.staging
state = C.state
SNAP_PATH = C.AUDIT_DIR / "audit_state_phase4.json"

mo_id = state["mo_ids"][0]
mo_name = state["mo_names"][0]
PROD_NAME = state["target_product_name"]
QTY = state["target_qty"]
PER_PALLET = state["target_per_pallet"]
EXPECTED_PALLETS = state["target_expected_pallets"]
REPORT = Path(state["report_path"])


def snapshot():
    snap = {"ts": _dt.datetime.now().isoformat(timespec="seconds")}
    # Odoo MO state
    mo = s.read_one("mrp.production", mo_id, [
        "name","state","qty_producing","qty_produced","date_start","date_finished",
        "lot_producing_ids","workorder_ids","move_raw_ids","move_finished_ids",
        "reservation_state"])
    snap["odoo_mo"] = {
        "name": mo["name"], "state": mo["state"], "reservation": mo["reservation_state"],
        "qty_producing": mo["qty_producing"], "qty_produced": mo["qty_produced"],
        "lot_producing_ids": mo["lot_producing_ids"],
        "date_start": mo["date_start"], "date_finished": mo["date_finished"],
    }
    # Workorders
    wos = s.call("mrp.workorder","read",[mo["workorder_ids"]],
        {"fields":["id","name","state","sequence","qty_producing","qty_produced","date_start","date_finished","operation_id","workcenter_id"]}) if mo["workorder_ids"] else []
    snap["workorders"] = [{
        "seq": w["sequence"], "op": w["operation_id"][1] if w["operation_id"] else "-",
        "wc": w["workcenter_id"][1] if w["workcenter_id"] else "-",
        "state": w["state"], "qty_producing": w["qty_producing"], "qty_produced": w["qty_produced"],
        "date_start": w["date_start"], "date_finished": w["date_finished"],
    } for w in sorted(wos, key=lambda x: x["sequence"])]
    # Raw move_lines (lots consumed so far)
    raw_moves = s.call("stock.move","read",[mo["move_raw_ids"]],
        {"fields":["id","product_id","product_uom_qty","quantity","state","move_line_ids"]}) if mo["move_raw_ids"] else []
    raw_summary = []
    for rm in raw_moves:
        mls = []
        if rm["move_line_ids"]:
            ml_data = s.call("stock.move.line","read",[rm["move_line_ids"]],
                {"fields":["quantity","lot_id","location_id"]})
            for ml in ml_data:
                mls.append({
                    "qty": ml["quantity"],
                    "lot": ml["lot_id"][1] if ml["lot_id"] else None,
                    "location": ml["location_id"][1] if ml["location_id"] else None,
                })
        raw_summary.append({
            "product": rm["product_id"][1],
            "demand": rm["product_uom_qty"], "consumed": rm["quantity"],
            "state": rm["state"],
            "lines": mls,
        })
    snap["raw_moves"] = raw_summary
    # FG move_lines
    fg_moves = s.call("stock.move","read",[mo["move_finished_ids"]],
        {"fields":["id","product_id","product_uom_qty","quantity","state","move_line_ids"]}) if mo["move_finished_ids"] else []
    fg_summary = []
    for fm in fg_moves:
        mls = []
        if fm["move_line_ids"]:
            ml_data = s.call("stock.move.line","read",[fm["move_line_ids"]],
                {"fields":["quantity","lot_id","package_id","location_dest_id"]})
            for ml in ml_data:
                mls.append({
                    "qty": ml["quantity"],
                    "lot": ml["lot_id"][1] if ml["lot_id"] else None,
                    "package": ml["package_id"][1] if ml["package_id"] else None,
                    "dest": ml["location_dest_id"][1] if ml["location_dest_id"] else None,
                })
        fg_summary.append({
            "product": fm["product_id"][1],
            "demand": fm["product_uom_qty"], "produced": fm["quantity"],
            "state": fm["state"],
            "lines": mls,
        })
    snap["fg_moves"] = fg_summary
    # MES side: rolls + pallets for this MO
    # Use direct lookup since /api/work-orders only shows current step
    # First find the MES API for production rolls
    rolls_resp = C.mes.get(f"/api/work-orders/{mo_name}/master-rolls")
    if isinstance(rolls_resp, dict) and "_error" in rolls_resp:
        snap["mes_rolls"] = {"error": rolls_resp["_error"]}
    else:
        snap["mes_rolls"] = rolls_resp or []
    return snap


def fmt_snap(snap):
    out = []
    out.append(f"=== snapshot {snap['ts']} ===")
    mo = snap["odoo_mo"]
    out.append(f"  MO {mo['name']}: state={mo['state']}, reservation={mo['reservation']}, qty_producing={mo['qty_producing']}/{int(QTY)}, qty_produced={mo['qty_produced']}, lots={mo['lot_producing_ids']}")
    for w in snap["workorders"]:
        out.append(f"    WO seq{w['seq']} {w['op']:<12} on {w['wc']:<15} state={w['state']:<10} q_ing={w['qty_producing']} q_ed={w['qty_produced']} start={w['date_start']} end={w['date_finished']}")
    out.append(f"  Raw moves consumption progress:")
    for rm in snap["raw_moves"]:
        lots = sum(1 for ln in rm["lines"] if ln["lot"])
        out.append(f"    {rm['product'][:40]:<40}  demand={rm['demand']:<8.2f}  consumed={rm['consumed']:<8.2f}  state={rm['state']:<10}  lines={len(rm['lines'])} ({lots} with lot)")
    out.append(f"  FG moves:")
    for fm in snap["fg_moves"]:
        out.append(f"    {fm['product']:<30}  demand={fm['demand']:<6}  produced={fm['produced']:<6}  state={fm['state']:<10}  lines={len(fm['lines'])}")
        for ml in fm["lines"]:
            out.append(f"      qty={ml['qty']} lot={ml['lot']} pkg={ml['package']} dest={ml['dest']}")
    mr = snap["mes_rolls"]
    if isinstance(mr, list):
        out.append(f"  MES rolls reported: {len(mr)}")
        for r in mr[:3]:
            out.append(f"    roll_id={r.get('roll_id')} weight={r.get('weight_lbs')} pos={r.get('position')}")
        if len(mr) > 3: out.append(f"    ... and {len(mr)-3} more")
    else:
        out.append(f"  MES rolls: {mr}")
    return "\n".join(out)


def diff(prev, curr):
    if not prev: return ["initial snapshot"]
    deltas = []
    if prev["odoo_mo"]["state"] != curr["odoo_mo"]["state"]:
        deltas.append(f"MO state {prev['odoo_mo']['state']} -> {curr['odoo_mo']['state']}")
    if prev["odoo_mo"]["qty_producing"] != curr["odoo_mo"]["qty_producing"]:
        deltas.append(f"qty_producing {prev['odoo_mo']['qty_producing']} -> {curr['odoo_mo']['qty_producing']}")
    if prev["odoo_mo"]["qty_produced"] != curr["odoo_mo"]["qty_produced"]:
        deltas.append(f"qty_produced {prev['odoo_mo']['qty_produced']} -> {curr['odoo_mo']['qty_produced']}")
    if prev["odoo_mo"]["lot_producing_ids"] != curr["odoo_mo"]["lot_producing_ids"]:
        deltas.append(f"lot_producing_ids {prev['odoo_mo']['lot_producing_ids']} -> {curr['odoo_mo']['lot_producing_ids']}")
    for pw, cw in zip(prev["workorders"], curr["workorders"]):
        if pw["state"] != cw["state"]:
            deltas.append(f"WO seq{cw['seq']} ({cw['op']}) state {pw['state']} -> {cw['state']}")
        if pw["qty_produced"] != cw["qty_produced"]:
            deltas.append(f"WO seq{cw['seq']} qty_produced {pw['qty_produced']} -> {cw['qty_produced']}")
    pmr = prev.get("mes_rolls"); cmr = curr.get("mes_rolls")
    if isinstance(pmr, list) and isinstance(cmr, list) and len(pmr) != len(cmr):
        deltas.append(f"MES rolls count {len(pmr)} -> {len(cmr)}")
    return deltas or ["(no changes)"]


def finalize():
    """Write Phase 4 PASS/FAIL block to the audit report based on current state."""
    snap = snapshot()
    SNAP_PATH.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
    mo = snap["odoo_mo"]
    wos = snap["workorders"]
    raw = snap["raw_moves"]
    fg = snap["fg_moves"]
    checks = []
    checks.append(("MO state == done", mo["state"] == "done", f"actual {mo['state']}"))
    checks.append((f"qty_producing == {QTY}", abs(mo["qty_producing"] - QTY) < 0.001 if mo['qty_producing'] is not None else False, f"actual {mo['qty_producing']}"))
    checks.append(("All workorders state==done", all(w["state"] == "done" for w in wos), f"states: {[w['state'] for w in wos]}"))
    # Raw consumption: every raw move should have move_lines with lots (for tracked products) and consumed qty
    raw_with_no_lot = [r for r in raw if any(ln["lot"] is None and r["product"] not in {"Core Plugs","Poly Wrap","4x1 label units","4x6 Label","3 inch cardboard core"} for ln in r["lines"])]
    checks.append(("Every resin/blend raw move_line has a lot", len(raw_with_no_lot) == 0, f"missing-lot: {[r['product'] for r in raw_with_no_lot]}"))
    # FG: the per-roll partial-shipment internal transfers move 50 units of the
    # MO-level FG lot to WH/Stock. The legacy move_finished_ids[0] gets its demand
    # decremented to 0 by each partial-ship (curr_target -= fg_qty), so it's NOT
    # the right place to look. Instead read the MO's lot_producing_ids and verify
    # that lot has 50 units in WH/Stock.
    mo_full = s.read_one("mrp.production", mo_id, ["lot_producing_ids","product_id"])
    if mo_full["lot_producing_ids"]:
        lot_id = mo_full["lot_producing_ids"][0]
        lot_rec = s.read_one("stock.lot", lot_id, ["id","name"])
        state["fg_lot_id"] = lot_id
        state["fg_lot_name"] = lot_rec["name"]
        # Quants in WH/Stock for this lot
        wh_quants = s.search_read("stock.quant",
            [("product_id","=",mo_full["product_id"][0]), ("lot_id","=",lot_id), ("location_id.name","=","Stock")],
            ["quantity","reserved_quantity"])
        wh_qty = sum(q["quantity"] for q in wh_quants)
        checks.append(("FG lot exists at MO level", True, f"lot={lot_rec['name']} (id {lot_id})"))
        checks.append((f"FG lot has {QTY} units in WH/Stock", abs(wh_qty - QTY) < 0.001, f"actual {wh_qty} units"))
        checks.append((f"FG lot follows MO-level pattern (MO/...)", lot_rec["name"].startswith("MO/"), f"actual {lot_rec['name']}"))
    else:
        checks.append(("FG lot exists at MO level", False, "lot_producing_ids empty"))

    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    all_pass = all(ok for _, ok, _ in checks)
    status = "PASS" if all_pass else "FAIL"

    lines = [
        f"\n### {ts} - Phase 4: Production execution - **{status}**",
        "",
        f"- MO {mo['name']}: state=`{mo['state']}`, qty_producing={mo['qty_producing']}, qty_produced={mo['qty_produced']}",
        f"- FG lot: `{state.get('fg_lot_name', '(none)')}` (id {state.get('fg_lot_id', '-')})",
        f"- Workorder progression:",
    ]
    for w in wos:
        lines.append(f"  - seq {w['seq']} `{w['op']}` on `{w['wc']}`: state=`{w['state']}`, qty_produced={w['qty_produced']}, start={w['date_start']}, end={w['date_finished']}")
    lines.append(f"- Raw consumption summary:")
    for rm in raw:
        lots_str = ", ".join(set(ln["lot"] or "(none)" for ln in rm["lines"]))
        lines.append(f"  - `{rm['product']}`: demand {rm['demand']}, consumed {rm['consumed']}, state=`{rm['state']}`, lots: {lots_str or '(no move_lines)'}")
    lines.append(f"- FG move:")
    for fm in fg:
        lines.append(f"  - `{fm['product']}`: demand {fm['demand']}, produced {fm['produced']}, state=`{fm['state']}`")
        for ml in fm["lines"]:
            lines.append(f"    - qty={ml['qty']}, lot=`{ml['lot']}`, package=`{ml['package']}`, dest=`{ml['dest']}`")
    lines.append("")
    lines.append("Checks:")
    for label, ok, detail in checks:
        mark = "OK" if ok else "FAIL"
        lines.append(f"- [{mark}] {label} - {detail}")

    text = REPORT.read_text(encoding="utf-8")
    text = text.replace("| 4 - Production / consumption / FG lot | _pending_ | | |",
                        f"| 4 - Production / consumption / FG lot | {'PASS' if all_pass else 'FAIL'} | {'' if all_pass else 'see Phase 4 block'} | {'' if all_pass else 'blocker'} |")
    text += "\n".join(lines) + "\n"
    REPORT.write_text(text, encoding="utf-8")
    C.log(f"appended Phase 4 block to {REPORT.name} ({status})")
    return all_pass


# main
if args.finalize:
    ok = finalize()
    print(f"\n  {'PASS' if ok else 'FAIL'}.  Next: pallet build, then run 04_verify_pallets.py")
elif args.watch:
    prev = None
    try:
        while True:
            snap = snapshot()
            SNAP_PATH.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
            print(fmt_snap(snap))
            if prev:
                deltas = diff(prev, snap)
                print(f"  DELTAS: " + " | ".join(deltas))
            prev = snap
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped.")
else:
    snap = snapshot()
    SNAP_PATH.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
    print(fmt_snap(snap))
    print(f"\n  Snapshot saved to {SNAP_PATH.name}")
    print(f"  Run with --watch to poll, or --finalize when production is complete to write Phase 4 PASS/FAIL block.")
