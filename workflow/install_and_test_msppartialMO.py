"""
Install + smoke-test msppartialMO on the v19 staging dev branch.

Reads ODOO_STAGING_URL / DB / USER / API_KEY from .env (matches the env
pattern used by fix_qweb_v18_residue.py and the other workflow scripts).
Staging-only by design — installing msppartialMO on prod is a separate
deliberate step controlled by the user.

Flow:
  1. Polls update_list() until msppartialMO appears at EXPECTED_VERSION
     (set this each time the addon's manifest version changes)
  2. Calls button_immediate_install (or button_immediate_upgrade) on it
  3. Smoke-tests action_increment_qty_producing — exercises the
     lot_producing_id → lot_producing_ids[:1] v19 fix
  4. Smoke-tests action_ship_partial_batch — exercises the
     stock.move.name → description_picking v19 fix
  5. Resets qty_producing back to the pre-test value

Note: action_ship_partial_batch creates a real stock.picking on staging
and validates it. Staging is meant to accumulate this kind of test data;
we do NOT attempt to roll it back. The reset only restores qty_producing.
"""
import os
import ssl
import sys
import time
import xmlrpc.client
from pathlib import Path

# Smoke-test parameters — adjust per addon iteration
EXPECTED_VERSION = '19.0.1.1.0'
TEST_MO_ID = 95          # WH/MO/00096 — confirmed state, qty 17.5, 1 lot
INC_QTY = 5.0            # bump qty_producing by this much
SHIP_QTY = 2.0           # then ship this much (must be <= INC_QTY)

POLL_INTERVAL_S = 30
MAX_WAIT_S = 25 * 60


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


def _connect():
    prefix = "ODOO_STAGING_"
    url = os.environ.get(prefix + "URL")
    db = os.environ.get(prefix + "DB")
    user = os.environ.get(prefix + "USER")
    key = os.environ.get(prefix + "API_KEY")
    if not all([url, db, user, key]):
        sys.exit(f"Missing {prefix}* env vars — set them in 18to19upgrade/.env")
    ctx = ssl.create_default_context()
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", context=ctx, allow_none=True)
    uid = common.authenticate(db, user, key, {})
    if not uid:
        sys.exit("Authentication failed — check ODOO_STAGING_API_KEY")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", context=ctx, allow_none=True)
    return url, db, user, key, uid, models


def wait_for_addon(db, key, uid, models):
    print(f'[wait] polling every {POLL_INTERVAL_S}s for msppartialMO {EXPECTED_VERSION}...', flush=True)
    start = time.time()
    while time.time() - start < MAX_WAIT_S:
        elapsed = int(time.time() - start)
        try:
            models.execute_kw(db, uid, key, 'ir.module.module', 'update_list', [])
            recs = models.execute_kw(
                db, uid, key, 'ir.module.module', 'search_read',
                [[['name', '=', 'msppartialMO']]],
                {'fields': ['id', 'name', 'state', 'installed_version', 'latest_version']}
            )
            if recs:
                latest = recs[0].get('latest_version')
                if latest and latest != EXPECTED_VERSION:
                    print(f'[wait] +{elapsed}s found {latest} (waiting for {EXPECTED_VERSION} rebuild)...', flush=True)
                else:
                    print(f'[wait] +{elapsed}s found: {recs[0]}', flush=True)
                    return recs[0]
            else:
                print(f'[wait] +{elapsed}s update_list ran but addon not yet present (still building)...', flush=True)
        except xmlrpc.client.Fault as e:
            print(f'[wait] +{elapsed}s xmlrpc fault: {str(e.faultString)[:120]}', flush=True)
        except Exception as e:
            print(f'[wait] +{elapsed}s connection error: {type(e).__name__}: {str(e)[:120]}', flush=True)
        time.sleep(POLL_INTERVAL_S)
    print(f'[wait] TIMEOUT after {MAX_WAIT_S}s', flush=True)
    return None


def install_or_upgrade(db, key, uid, models, mod):
    if mod['state'] == 'installed' and mod.get('installed_version') == EXPECTED_VERSION:
        print(f'[install] already at {EXPECTED_VERSION}', flush=True)
        return True
    if mod['state'] == 'installed':
        print(f'[install] upgrading {mod.get("installed_version")} -> {EXPECTED_VERSION}...', flush=True)
        try:
            models.execute_kw(db, uid, key, 'ir.module.module', 'button_immediate_upgrade', [[mod['id']]])
        except xmlrpc.client.Fault as e:
            print(f'[install] upgrade FAULT: {str(e.faultString)[:600]}', flush=True)
            return False
    elif mod['state'] == 'uninstalled':
        print(f'[install] calling button_immediate_install on id={mod["id"]}...', flush=True)
        try:
            models.execute_kw(db, uid, key, 'ir.module.module', 'button_immediate_install', [[mod['id']]])
        except xmlrpc.client.Fault as e:
            print(f'[install] install FAULT: {str(e.faultString)[:600]}', flush=True)
            return False
    else:
        print(f'[install] unexpected state {mod["state"]} - aborting', flush=True)
        return False
    after = models.execute_kw(
        db, uid, key, 'ir.module.module', 'search_read',
        [[['name', '=', 'msppartialMO']]],
        {'fields': ['name', 'state', 'installed_version']}
    )[0]
    print(f'[install] post: {after}', flush=True)
    return after['state'] == 'installed' and after.get('installed_version') == EXPECTED_VERSION


def read_mo(db, key, uid, models, mo_id):
    return models.execute_kw(
        db, uid, key, 'mrp.production', 'read', [[mo_id]],
        {'fields': ['name', 'state', 'product_qty', 'qty_producing', 'lot_producing_ids']}
    )[0]


def smoke_test_increment(db, key, uid, models):
    print(f'\n[smoke-1] action_increment_qty_producing path (lot_producing_ids fix)', flush=True)
    before = read_mo(db, key, uid, models, TEST_MO_ID)
    print(f'  BEFORE: {before}', flush=True)
    pre_qty = before['qty_producing']
    try:
        result = models.execute_kw(
            db, uid, key, 'mrp.production', 'action_increment_qty_producing',
            [[TEST_MO_ID], INC_QTY]
        )
        print(f'  return value: {result!r}', flush=True)
    except xmlrpc.client.Fault as e:
        print(f'  FAULT: {str(e.faultString)[:600]}', flush=True)
        return False, pre_qty
    after = read_mo(db, key, uid, models, TEST_MO_ID)
    print(f'  AFTER:  {after}', flush=True)
    delta = after['qty_producing'] - pre_qty
    ok = abs(delta - INC_QTY) < 0.001
    print(f'  delta: {delta:+.4f} expected {INC_QTY:+.4f} -> {"PASS" if ok else "FAIL"}', flush=True)
    return ok, pre_qty


def smoke_test_ship_partial(db, key, uid, models):
    print(f'\n[smoke-2] action_ship_partial_batch path (description_picking fix)', flush=True)
    before_pickings = models.execute_kw(
        db, uid, key, 'stock.picking', 'search_count',
        [[['origin', 'like', 'Partial Shipment']]]
    )
    print(f'  pre-existing partial-shipment pickings: {before_pickings}', flush=True)
    try:
        result = models.execute_kw(
            db, uid, key, 'mrp.production', 'action_ship_partial_batch',
            [[TEST_MO_ID], SHIP_QTY]
        )
        print(f'  return value: {result!r}', flush=True)
    except xmlrpc.client.Fault as e:
        print(f'  FAULT: {str(e.faultString)[:1000]}', flush=True)
        return False
    after_pickings = models.execute_kw(
        db, uid, key, 'stock.picking', 'search_count',
        [[['origin', 'like', 'Partial Shipment']]]
    )
    print(f'  post partial-shipment pickings: {after_pickings} (delta: {after_pickings - before_pickings})', flush=True)

    new_pickings = models.execute_kw(
        db, uid, key, 'stock.picking', 'search_read',
        [[['origin', 'like', 'Partial Shipment']]],
        {'fields': ['id', 'name', 'origin', 'state', 'move_ids'], 'order': 'id desc', 'limit': 1}
    )
    if new_pickings:
        pick = new_pickings[0]
        print(f'  most-recent picking: {pick}', flush=True)
        if pick['move_ids']:
            mvs = models.execute_kw(
                db, uid, key, 'stock.move', 'read', [pick['move_ids']],
                {'fields': ['description_picking', 'product_uom_qty', 'quantity', 'state', 'picked']}
            )
            for mv in mvs:
                print(f'  move: {mv}', flush=True)
    return after_pickings == before_pickings + 1


def reset_qty(db, key, uid, models, pre_qty):
    print(f'\n[reset] writing qty_producing back to {pre_qty}', flush=True)
    try:
        models.execute_kw(db, uid, key, 'mrp.production', 'write', [[TEST_MO_ID], {'qty_producing': pre_qty}])
        after = read_mo(db, key, uid, models, TEST_MO_ID)
        print(f'  RESET: {after}', flush=True)
    except xmlrpc.client.Fault as e:
        print(f'  reset FAULT: {str(e.faultString)[:300]}', flush=True)


def main():
    url, db, user, key, uid, models = _connect()
    print(f'[start] target={url}', flush=True)
    print(f'[start] expecting msppartialMO {EXPECTED_VERSION}', flush=True)
    print(f'[start] authed uid={uid}', flush=True)

    mod = wait_for_addon(db, key, uid, models)
    if not mod:
        print('[result] FAILED: addon never appeared', flush=True); sys.exit(2)

    if not install_or_upgrade(db, key, uid, models, mod):
        print('[result] FAILED: install/upgrade error', flush=True); sys.exit(3)

    inc_ok, pre_qty = smoke_test_increment(db, key, uid, models)
    ship_ok = smoke_test_ship_partial(db, key, uid, models) if inc_ok else False
    reset_qty(db, key, uid, models, pre_qty)

    print(f'\n[result] increment: {"PASS" if inc_ok else "FAIL"}, ship_partial: {"PASS" if ship_ok else "FAIL"}', flush=True)
    sys.exit(0 if (inc_ok and ship_ok) else 4)


if __name__ == '__main__':
    main()
