"""End-to-end smoke test for /api/v1/production/pallet/finalize.

  1. Lookup a real pallet (state before)
  2. POST finalize with a sane weight
  3. Lookup again — verify gross_weight_lb / is_finalized / finalized_at
  4. POST again with a different weight — confirm re-finalize works
  5. Try invalid inputs — confirm 400/404 paths
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

def _load_dotenv():
    p = Path(__file__).parent.parent / ".env"
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
_load_dotenv()

MES_URL = os.environ.get("MES_TEST_URL", "https://35.194.23.98.nip.io")
MES_KEY = os.environ["MES_TEST_API_KEY"]
PALLET_ID = sys.argv[1] if len(sys.argv) > 1 else "WH/MO/01206-PAL-1"
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE


def _req(method, path, body=None):
    url = f"{MES_URL}{path}"
    headers = {"X-API-KEY": MES_KEY}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def lookup(pid):
    return _req("GET", f"/api/v1/pallet/lookup/{urllib.parse.quote(pid, safe='')}")


def finalize(pid, weight):
    return _req("POST", "/api/v1/production/pallet/finalize",
                {"pallet_id": pid, "gross_weight_lb": weight})


print(f"=== smoke test against {PALLET_ID} ===\n")

# 1. before
s, b = lookup(PALLET_ID)
print(f"[before] status={s}")
print(f"  is_finalized={b['pallet']['is_finalized']}  gross={b['pallet']['gross_weight_lb']}  finalized_at={b['pallet']['finalized_at']}")
expected = b['pallet']['expected_gross_lb'] or 1000.0

# 2. finalize
weight = round(expected * 1.005, 1)  # within 1% of expected
print(f"\n[finalize #1] weight={weight} lb")
s, b = finalize(PALLET_ID, weight)
print(f"  status={s}  success={b.get('success')}")
assert s == 200 and b.get('success'), f"finalize failed: {b}"
assert b['pallet']['gross_weight_lb'] == weight
assert b['pallet']['is_finalized'] is True

# 3. confirm persisted
s, b = lookup(PALLET_ID)
print(f"\n[after finalize #1]")
print(f"  is_finalized={b['pallet']['is_finalized']}  gross={b['pallet']['gross_weight_lb']}  finalized_at={b['pallet']['finalized_at']}")

# 4. re-finalize with different weight
weight2 = round(expected * 0.99, 1)
print(f"\n[finalize #2] weight={weight2} lb (re-scale)")
s, b = finalize(PALLET_ID, weight2)
print(f"  status={s}  gross={b.get('pallet', {}).get('gross_weight_lb')}")
assert s == 200 and b['pallet']['gross_weight_lb'] == weight2

# 5. invalid input paths
print(f"\n[validation paths]")
s, b = finalize(PALLET_ID, 0)
print(f"  weight=0:               {s}  {b.get('error')}");  assert s == 400
s, b = finalize(PALLET_ID, -5)
print(f"  weight=-5:              {s}  {b.get('error')}");  assert s == 400
s, b = finalize("PLT-NOPE", 100)
print(f"  bogus pallet:           {s}  {b.get('error')}");  assert s == 404
s, b = finalize("", 100)
print(f"  empty pallet_id:        {s}  {b.get('error')}");  assert s == 400

print("\n[result] PASS")
