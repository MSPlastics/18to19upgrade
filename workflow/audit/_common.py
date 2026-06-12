"""Shared helpers for `workflow/audit/*` scripts.

Every audit script imports from here so credentials, XMLRPC plumbing, and
the audit-state file are centralized. Read-only by default — mutating helpers
log what they did to AUDIT_STATE.

Usage:
    from _common import staging, mes, state, log
    prod = staging.read_one("product.product", 1195, ["name","uom_id"])
"""
from __future__ import annotations
import json, os, ssl, sys, time, datetime as _dt, urllib.request, urllib.error
import xmlrpc.client
from pathlib import Path

# --- repo layout ---
ROOT = Path(__file__).resolve().parent.parent.parent       # 18to19upgrade/
WORKFLOW = ROOT / "workflow"
AUDIT_DIR = WORKFLOW / "audit"
STATE_PATH = AUDIT_DIR / "audit_state.json"
LOG_PATH = AUDIT_DIR / "audit_run.log"

# --- env ---
def _load_dotenv():
    p = ROOT / ".env"
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
_load_dotenv()


def log(msg: str, *, also_print: bool = True):
    """Append a timestamped line to audit_run.log and optionally to stdout."""
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    if also_print:
        print(line)


class _Staging:
    """Thin XMLRPC client for staging Odoo with v19-friendly defaults."""
    def __init__(self):
        self.url = os.environ["ODOO_STAGING_URL"]
        self.db = os.environ["ODOO_STAGING_DB"]
        self.user = os.environ.get("ODOO_STAGING_USER", "admin@mountainstatesplastics.com")
        self.key = os.environ["ODOO_STAGING_API_KEY"]
        ctx = ssl.create_default_context()
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", context=ctx, allow_none=True)
        self.uid = common.authenticate(self.db, self.user, self.key, {})
        if not self.uid:
            sys.exit("staging auth failed")
        self._m = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", context=ctx, allow_none=True)

    def call(self, model: str, method: str, args, kw=None):
        return self._m.execute_kw(self.db, self.uid, self.key, model, method, args, kw or {})

    def call_void(self, model: str, method: str, args, kw=None):
        """Swallow Odoo's 'cannot marshal None' fault for methods that return void."""
        try:
            return self.call(model, method, args, kw)
        except xmlrpc.client.Fault as e:
            if "cannot marshal None" in str(e):
                return None
            raise

    def read_one(self, model: str, rec_id: int, fields: list[str]) -> dict | None:
        res = self.call(model, "read", [[rec_id]], {"fields": fields})
        return res[0] if res else None

    def search_read(self, model: str, domain, fields, **kw):
        return self.call(model, "search_read", [domain], {"fields": fields, **kw})

    def search(self, model: str, domain, **kw):
        return self.call(model, "search", [domain], kw)


class _MES:
    """Thin HTTP client for the cloud test MES."""
    def __init__(self):
        self.url = os.environ.get("MES_TEST_URL", "https://34.57.35.195.nip.io").rstrip("/")
        self.key = os.environ.get("MES_TEST_API_KEY", "msplastics-mes-2026-61bf306c6d2e5ede")
        # Allow self-signed
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    def get(self, path: str, *, timeout: int = 30):
        url = f"{self.url}{path}"
        req = urllib.request.Request(url, headers={"X-API-Key": self.key, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body.strip() else None
        except urllib.error.HTTPError as e:
            return {"_error": e.code, "_body": e.read().decode("utf-8", errors="replace")}

    def post(self, path: str, payload: dict, *, timeout: int = 30):
        url = f"{self.url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={
            "X-API-Key": self.key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body.strip() else None
        except urllib.error.HTTPError as e:
            return {"_error": e.code, "_body": e.read().decode("utf-8", errors="replace")}


class _State:
    """JSON-backed dict for cross-script state (SO id, MO id, lot id, pallet ids, ...).

    Each script reads what it needs and writes what it discovered. The file
    lives at workflow/audit/audit_state.json so we can inspect mid-run.
    """
    def __init__(self, path=STATE_PATH):
        self.path = path
        self._data = {}
        if path.exists():
            try: self._data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError: self._data = {}

    def __getitem__(self, k): return self._data[k]
    def __setitem__(self, k, v):
        self._data[k] = v
        self._save()
    def __contains__(self, k): return k in self._data
    def get(self, k, default=None): return self._data.get(k, default)
    def update(self, **kv):
        self._data.update(kv); self._save()
    def all(self): return dict(self._data)
    def reset(self): self._data = {}; self._save()
    def _save(self):
        self.path.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")


# Lazy singletons — instantiate on first access so import is fast.
_staging_instance = None
_mes_instance = None
_state_instance = None

def __getattr__(name):
    global _staging_instance, _mes_instance, _state_instance
    if name == "staging":
        if _staging_instance is None: _staging_instance = _Staging()
        return _staging_instance
    if name == "mes":
        if _mes_instance is None: _mes_instance = _MES()
        return _mes_instance
    if name == "state":
        if _state_instance is None: _state_instance = _State()
        return _state_instance
    raise AttributeError(name)
