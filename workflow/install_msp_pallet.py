"""
Install (or upgrade) the msp_pallet addon on the v19 staging dev branch.

Polls update_list() until the addon shows up at EXPECTED_VERSION (i.e.
Odoo.sh has finished rebuilding after the push), then triggers
button_immediate_install/upgrade and verifies it's in 'installed' state.

Reads ODOO_STAGING_* from .env. Staging-only by design.
"""
import os
import ssl
import sys
import time
import xmlrpc.client
from pathlib import Path

EXPECTED_VERSION = '19.0.1.0.3'
ADDON = 'msp_pallet'

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
    url = os.environ.get("ODOO_STAGING_URL")
    db = os.environ.get("ODOO_STAGING_DB")
    user = os.environ.get("ODOO_STAGING_USER", "admin@mountainstatesplastics.com")
    key = os.environ.get("ODOO_STAGING_API_KEY")
    if not all([url, db, key]):
        sys.exit("Missing ODOO_STAGING_* env vars in 18to19upgrade/.env")
    ctx = ssl.create_default_context()
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", context=ctx, allow_none=True)
    uid = common.authenticate(db, user, key, {})
    if not uid:
        sys.exit("Authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", context=ctx, allow_none=True)
    return db, key, uid, models


def wait_for_addon(db, key, uid, models):
    print(f"[wait] polling every {POLL_INTERVAL_S}s for {ADDON} {EXPECTED_VERSION}...", flush=True)
    start = time.time()
    while time.time() - start < MAX_WAIT_S:
        elapsed = int(time.time() - start)
        try:
            models.execute_kw(db, uid, key, 'ir.module.module', 'update_list', [])
            recs = models.execute_kw(
                db, uid, key, 'ir.module.module', 'search_read',
                [[('name', '=', ADDON)]],
                {'fields': ['id', 'name', 'state', 'installed_version', 'latest_version']},
            )
            if recs:
                rec = recs[0]
                print(f"  [{elapsed:>4}s] state={rec['state']} installed={rec['installed_version']} latest={rec['latest_version']}", flush=True)
                if rec['latest_version'] == EXPECTED_VERSION or rec['state'] == 'installed':
                    return rec
            else:
                print(f"  [{elapsed:>4}s] addon not yet visible to Odoo", flush=True)
        except Exception as e:
            print(f"  [{elapsed:>4}s] xmlrpc fault (rebuild in progress?): {e}", flush=True)
        time.sleep(POLL_INTERVAL_S)
    sys.exit(f"timed out waiting for {ADDON} {EXPECTED_VERSION}")


def install_or_upgrade(db, key, uid, models, rec):
    state = rec['state']
    addon_id = rec['id']
    if state == 'uninstalled':
        print(f"[install] calling button_immediate_install on {ADDON} (id={addon_id})...", flush=True)
        models.execute_kw(db, uid, key, 'ir.module.module', 'button_immediate_install', [[addon_id]])
    elif state == 'installed' and rec['installed_version'] != EXPECTED_VERSION:
        print(f"[upgrade] calling button_immediate_upgrade on {ADDON} (installed={rec['installed_version']} -> {EXPECTED_VERSION})...", flush=True)
        models.execute_kw(db, uid, key, 'ir.module.module', 'button_immediate_upgrade', [[addon_id]])
    elif state == 'installed':
        print(f"[ok] {ADDON} {EXPECTED_VERSION} already installed", flush=True)
        return
    else:
        sys.exit(f"unexpected addon state: {state}")
    # verify
    rec = models.execute_kw(
        db, uid, key, 'ir.module.module', 'read', [[addon_id]],
        {'fields': ['state', 'installed_version']},
    )[0]
    print(f"[done] state={rec['state']} installed={rec['installed_version']}", flush=True)
    if rec['state'] != 'installed':
        sys.exit(f"install failed: state={rec['state']}")


def main():
    db, key, uid, models = _connect()
    rec = wait_for_addon(db, key, uid, models)
    install_or_upgrade(db, key, uid, models, rec)
    print("[result] PASS")


if __name__ == "__main__":
    main()
