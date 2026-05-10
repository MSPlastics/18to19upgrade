"""Phase 4 driver: simulate operator UI actions via the MES production API.

Subcommands (run in order):
    extrude       create N master rolls on the extrusion step
    advance       mark MR workorder done so Conversion becomes ready
    convert       create the finished rolls referencing master rolls as source
    finalize-mo   mark Conversion done + close MO

Each command is idempotent in spirit -- skips if state already advanced. Reads
all targets from audit_state.json. Logs every API call to audit_run.log.
"""
from __future__ import annotations
import argparse, time
import _common as C

s = C.staging
state = C.state

MO_NAME = state["mo_names"][0]
MO_ID = state["mo_ids"][0]
PROD_NAME = state["target_product_name"]
QTY = int(state["target_qty"])
PER_PALLET = state["target_per_pallet"]


def _wos():
    return s.call("mrp.workorder","search_read",[[("production_id","=",MO_ID)]],
        {"fields":["id","name","state","sequence","operation_id","workcenter_id","duration_expected"], "order":"sequence ASC"})


def _wait_until(label, condition_fn, max_seconds=120, interval=3):
    """Poll Odoo until condition_fn() returns truthy or timeout. condition_fn returns
    a (ok, info_str) tuple. Used to wait for sync engine to land changes from MES."""
    for i in range(0, max_seconds, interval):
        ok, info = condition_fn()
        if ok:
            C.log(f"  CONDITION MET ({label}) after {i}s -- {info}")
            return True
        if i % 9 == 0:
            C.log(f"  waiting for {label} ({i}s): {info}")
        time.sleep(interval)
    C.log(f"  WARN: condition '{label}' not met after {max_seconds}s")
    return False


def cmd_extrude(args):
    """Create N master rolls of total ~target weight."""
    wos = _wos()
    mr_wo = wos[0]
    if mr_wo["operation_id"] and "MR" not in mr_wo["operation_id"][1]:
        raise SystemExit(f"first WO is {mr_wo['operation_id']}, expected MR")
    C.log(f"=== EXTRUDE: {args.count} master roll(s) on {mr_wo['name']} (WO id {mr_wo['id']}) ===")

    # Total resin = ~46.28 lb/roll * 50 rolls = 2,314 lb
    total_resin = 46.28 * QTY
    per_mr = total_resin / args.count
    # Approximate length: target_feet for whole MO is ~9270 ft. Per MR = 9270 / count.
    total_feet = 9270.83
    per_ft = total_feet / args.count

    if state.get("mr_roll_ids") and not args.force:
        C.log(f"  state already has MR rolls: {state['mr_roll_ids']}. Re-run with --force to recreate.")
        return

    created = []
    for i in range(1, args.count + 1):
        roll_id = f"AUDIT-{MO_NAME.split('/')[-1]}-MR-{i:02d}"
        weight = round(per_mr, 2)
        length = round(per_ft, 2)
        payload = {
            "wo_number": MO_NAME,
            "work_order_id": str(mr_wo["id"]),
            "current_step_seq": 1,
            "roll_id": roll_id,
            "weight_lbs": weight,
            "length_ft": length,
            "width": "110",
            "mil": "1.5",
        }
        C.log(f"  POST /api/v1/production/roll: roll_id={roll_id} weight={weight} length={length}")
        r = C.mes.post("/api/v1/production/roll", payload)
        C.log(f"    -> {r}")
        if isinstance(r, dict) and r.get("success"):
            created.append(roll_id)
        else:
            raise SystemExit(f"roll create failed: {r}")
        time.sleep(0.5)
    state["mr_roll_ids"] = created
    C.log(f"  created MR rolls: {created}")
    # Wait for sync engine to push raw consumption to Odoo: a few of the
    # blend-resin raw moves (Butene, Clear Repro, etc.) should grow move_lines.
    def _check():
        mo = s.read_one("mrp.production", MO_ID, ["move_raw_ids"])
        moves = s.call("stock.move","read",[mo["move_raw_ids"]],
            {"fields":["product_id","move_line_ids"]})
        with_lines = [m for m in moves if m["product_id"][1] in
                      ("Butene1-BF","Clear Repro","Frac1-A","Exeed 1018.RA","conSLIP fast","conANTIBLOCK clarity")
                      and m["move_line_ids"]]
        return (len(with_lines) >= 4, f"{len(with_lines)}/6 resin moves have move_lines")
    _wait_until("MR consumption sync to Odoo", _check, max_seconds=180, interval=5)


def cmd_advance(args):
    """Mark the MR workorder done so the Conversion workorder becomes ready."""
    wos = _wos()
    mr, conv = wos[0], wos[-1]
    C.log(f"=== ADVANCE: MR ({mr['state']}) -> done; Conversion ({conv['state']}) -> ready ===")
    if mr["state"] == "done":
        C.log("  MR already done")
    else:
        # Try button_finish (raw mrp.workorder method)
        for method in ["button_finish", "do_finish"]:
            try:
                C.log(f"  trying mrp.workorder.{method}([{mr['id']}])")
                s.call_void("mrp.workorder", method, [[mr["id"]]])
                C.log(f"    OK")
                break
            except Exception as e:
                C.log(f"    {method} failed: {e}")
        else:
            raise SystemExit("could not finish MR workorder")
    # Re-read
    wos = _wos()
    for w in wos:
        C.log(f"  WO seq{w['sequence']} {w['operation_id']}: state={w['state']}")


def cmd_convert(args):
    """Create the finished rolls (one per FG unit), referencing MR rolls as source."""
    wos = _wos()
    conv_wo = wos[-1]
    if conv_wo["operation_id"] and "Conversion" not in conv_wo["operation_id"][1]:
        raise SystemExit(f"last WO is {conv_wo['operation_id']}, expected Conversion")
    if conv_wo["state"] not in ("ready", "progress"):
        raise SystemExit(f"Conversion WO is {conv_wo['state']}; need to advance MR first")

    mr_rolls = state.get("mr_roll_ids") or []
    if not mr_rolls:
        raise SystemExit("no master rolls in state - run extrude first")

    if state.get("fg_roll_ids") and not args.force:
        C.log(f"  state already has FG rolls (count {len(state['fg_roll_ids'])}). Re-run with --force.")
        return

    C.log(f"=== CONVERT: {QTY} finished rolls on {conv_wo['name']} (WO id {conv_wo['id']}) ===")
    # Distribute FG rolls across MR rolls round-robin
    fg_per_mr = QTY // len(mr_rolls)
    extras = QTY - fg_per_mr * len(mr_rolls)

    created = []
    unit_no = 1
    for mr_idx, mr_roll_id in enumerate(mr_rolls):
        n_for_this_mr = fg_per_mr + (1 if mr_idx < extras else 0)
        for j in range(n_for_this_mr):
            fg_roll_id = f"AUDIT-{MO_NAME.split('/')[-1]}-FG-{unit_no:03d}"
            weight = 46.28
            payload = {
                "wo_number": MO_NAME,
                "work_order_id": str(conv_wo["id"]),
                "current_step_seq": 2,
                "roll_id": fg_roll_id,
                "weight_lbs": weight,
                "length_ft": 185.4,
                "source_roll_id": mr_roll_id,
                "unit_number": unit_no,
            }
            C.log(f"  POST /api/v1/production/roll: FG #{unit_no} from {mr_roll_id}")
            r = C.mes.post("/api/v1/production/roll", payload)
            if isinstance(r, dict) and r.get("success"):
                created.append(fg_roll_id)
            else:
                C.log(f"    FAILED: {r}")
                raise SystemExit(f"FG roll create failed at unit {unit_no}: {r}")
            unit_no += 1
            time.sleep(0.2)
    state["fg_roll_ids"] = created
    C.log(f"  created {len(created)} FG rolls")
    # Wait until MO qty_producing == QTY (each FG roll bumps it by 1 via
    # action_increment_qty_producing in the Odoo sync handler).
    def _check():
        mo = s.read_one("mrp.production", MO_ID, ["qty_producing","state"])
        return (abs((mo["qty_producing"] or 0) - QTY) < 0.001,
                f"qty_producing={mo['qty_producing']}/{QTY} state={mo['state']}")
    _wait_until("MO qty_producing reaches target", _check, max_seconds=900, interval=8)


def cmd_finalize_mo(args):
    """Finish the Conversion WO and try to close the MO."""
    wos = _wos()
    conv = wos[-1]
    C.log(f"=== FINALIZE-MO: Conversion state={conv['state']} ===")
    if conv["state"] not in ("done",):
        for method in ["button_finish", "do_finish"]:
            try:
                C.log(f"  trying mrp.workorder.{method}([{conv['id']}])")
                s.call_void("mrp.workorder", method, [[conv["id"]]])
                C.log(f"    OK")
                break
            except Exception as e:
                C.log(f"    {method} failed: {e}")
    mo = s.read_one("mrp.production", MO_ID, ["state","qty_producing","qty_produced","lot_producing_ids"])
    C.log(f"  MO state={mo['state']} qty_producing={mo['qty_producing']} qty_produced={mo['qty_produced']} lots={mo['lot_producing_ids']}")
    # Try mark done
    if mo["state"] in ("progress", "to_close"):
        try:
            C.log(f"  trying mrp.production.button_mark_done([{MO_ID}])")
            s.call_void("mrp.production", "button_mark_done", [[MO_ID]])
        except Exception as e:
            C.log(f"    button_mark_done failed: {e}")
    mo = s.read_one("mrp.production", MO_ID, ["state","qty_producing","qty_produced"])
    C.log(f"  final MO state={mo['state']}")


def cmd_build_pallets(args):
    """Build pallets via /api/v1/production/pallet then finalize via /pallet/finalize.

    Uses the converter step's WO and the FG roll IDs from state.
    """
    fg = state.get("fg_roll_ids") or []
    if len(fg) != QTY:
        raise SystemExit(f"expected {QTY} FG rolls in state, found {len(fg)}")
    wos = _wos()
    conv_wo = wos[-1]

    # Distribute rolls across pallets, PER_PALLET per pallet
    pallets = []
    for i in range(0, len(fg), PER_PALLET):
        pal_n = i // PER_PALLET + 1
        chunk = fg[i:i + PER_PALLET]
        pallets.append((pal_n, chunk))

    if state.get("pallet_ids") and not args.force:
        C.log(f"  state already has pallets {state['pallet_ids']}; re-run with --force")
        return

    created = []
    for pal_n, roll_ids in pallets:
        pallet_id = f"{MO_NAME}-PAL-{pal_n}"
        payload = {
            "wo_number": MO_NAME,
            "work_order_id": str(conv_wo["id"]),
            "pallet_id": pallet_id,
            "roll_ids": roll_ids,
        }
        C.log(f"  POST /api/v1/production/pallet: {pallet_id} with {len(roll_ids)} rolls")
        r = C.mes.post("/api/v1/production/pallet", payload)
        C.log(f"    -> {r}")
        if not (isinstance(r, dict) and r.get("success")):
            raise SystemExit(f"pallet create failed: {r}")
        created.append(pallet_id)
        time.sleep(0.5)
    state["pallet_ids"] = created

    # Now finalize each (set gross weight)
    # 25 rolls × 46.28 lb = 1157 lb + 50 lb tare = 1207 lb gross
    gross = round(PER_PALLET * 46.28 + 50.0, 1)
    for pallet_id in created:
        C.log(f"  POST /api/v1/production/pallet/finalize: {pallet_id} gross={gross} lb")
        r = C.mes.post("/api/v1/production/pallet/finalize",
                       {"pallet_id": pallet_id, "gross_weight_lb": gross})
        C.log(f"    -> {r if not isinstance(r, dict) else 'success' if r.get('success') else r}")
        if not (isinstance(r, dict) and r.get("success")):
            raise SystemExit(f"pallet finalize failed: {r}")
        time.sleep(0.3)

    # Wait for reconcile sync to land Odoo stock.package records
    def _check():
        pkgs = s.search_read("stock.package", [("name","in",created)], ["id","name"])
        return (len(pkgs) == len(created), f"{len(pkgs)}/{len(created)} packages on Odoo")
    _wait_until("pallet reconcile sync", _check, max_seconds=300, interval=8)


# main
ap = argparse.ArgumentParser()
sub = ap.add_subparsers(dest="cmd", required=True)
ap_e = sub.add_parser("extrude"); ap_e.add_argument("--count", type=int, default=3); ap_e.add_argument("--force", action="store_true")
ap_a = sub.add_parser("advance")
ap_c = sub.add_parser("convert"); ap_c.add_argument("--force", action="store_true")
ap_f = sub.add_parser("finalize-mo")
ap_p = sub.add_parser("build-pallets"); ap_p.add_argument("--force", action="store_true")
args = ap.parse_args()
{
    "extrude": cmd_extrude,
    "advance": cmd_advance,
    "convert": cmd_convert,
    "finalize-mo": cmd_finalize_mo,
    "build-pallets": cmd_build_pallets,
}[args.cmd](args)
