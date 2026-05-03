"""Shared connection helpers for diagnostic tools.

Reads credentials from env vars (or a .env file at the repo root if
python-dotenv is available). Falls back to interactive prompt.

Required env vars depend on which target you connect to:
- prod:    ODOO_PROD_URL, ODOO_PROD_DB, ODOO_PROD_USER, ODOO_PROD_API_KEY
- staging: ODOO_STAGING_URL, ODOO_STAGING_DB, ODOO_STAGING_USER, ODOO_STAGING_API_KEY
"""
import os
import ssl
import sys
import xmlrpc.client
from pathlib import Path


def _load_dotenv():
    """Tiny built-in .env loader; no external deps."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


def connect(target: str = "prod"):
    """target: 'prod' or 'staging'. Returns (uid, models, db, api_key)."""
    prefix = f"ODOO_{target.upper()}_"
    url = os.environ.get(prefix + "URL")
    db = os.environ.get(prefix + "DB")
    user = os.environ.get(prefix + "USER")
    api_key = os.environ.get(prefix + "API_KEY")

    missing = [k for k, v in (("URL", url), ("DB", db), ("USER", user), ("API_KEY", api_key)) if not v]
    if missing:
        sys.exit(f"Missing env vars for {target}: {', '.join(prefix + m for m in missing)}\n"
                 f"Copy .env.example to .env and fill in values, or export them in your shell.")

    ctx = ssl.create_default_context()
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", context=ctx, allow_none=True)
    uid = common.authenticate(db, user, api_key, {})
    if not uid:
        sys.exit(f"Authentication failed for {target} ({user} @ {url})")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", context=ctx, allow_none=True)
    return uid, models, db, api_key


def make_caller(uid, models, db, api_key):
    """Returns a 'call(model, method, args, kwargs)' helper."""
    def call(model, method, args, kwargs=None):
        return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})
    return call
