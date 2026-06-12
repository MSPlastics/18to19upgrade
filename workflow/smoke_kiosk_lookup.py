"""Smoke-test the cloud test MES /api/v1/pallet/lookup endpoint."""
import json
import os
import ssl
import sys
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

MES_URL = os.environ.get("MES_TEST_URL", "https://34.57.35.195.nip.io")
MES_KEY = os.environ["MES_TEST_API_KEY"]
PALLET_ID = sys.argv[1] if len(sys.argv) > 1 else "WH/MO/01206-PAL-9"

ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE

# 1. lookup an existing pallet
url = f"{MES_URL}/api/v1/pallet/lookup/{urllib.parse.quote(PALLET_ID, safe='')}"
print(f"GET {url}")
req = urllib.request.Request(url, headers={"X-API-KEY": MES_KEY})
with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
    body = json.loads(r.read().decode())
    print(json.dumps(body, indent=2))
    assert r.status == 200 and body.get("success"), "lookup failed"

# 2. lookup a bogus pallet — expect 404
url = f"{MES_URL}/api/v1/pallet/lookup/PLT-DOES-NOT-EXIST"
print(f"\nGET {url}")
req = urllib.request.Request(url, headers={"X-API-KEY": MES_KEY})
try:
    with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
        print(f"  unexpected status {r.status}: {r.read().decode()}")
        sys.exit(1)
except urllib.error.HTTPError as e:
    body = json.loads(e.read().decode())
    print(f"  status={e.code}  body={body}")
    assert e.code == 404 and not body.get("success"), "should 404 for missing pallet"

print("\n[result] PASS")
