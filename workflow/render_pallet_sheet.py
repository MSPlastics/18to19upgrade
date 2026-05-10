"""Render the MSP Pallet Sheet PDF for a stock.package and save locally.

Uses the /report/pdf/<report_name>/<ids> HTTP endpoint with session auth
(login form + cookie) since v19 made _render_qweb_pdf private to RPC.
"""
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
import http.cookiejar
from pathlib import Path

def _load_dotenv():
    p = Path(__file__).parent.parent / ".env"
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())
_load_dotenv()

URL = os.environ["ODOO_STAGING_URL"]; DB = os.environ["ODOO_STAGING_DB"]
USER = os.environ.get("ODOO_STAGING_USER", "admin@mountainstatesplastics.com")
KEY = os.environ["ODOO_STAGING_API_KEY"]
PKG_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 3
OUT = sys.argv[2] if len(sys.argv) > 2 else f"pallet_sheet_pkg{PKG_ID}.pdf"
REPORT_NAME = "msp.report_pallet_sheet_v1"

ctx = ssl.create_default_context()
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=ctx),
    urllib.request.HTTPCookieProcessor(cj),
)

# Try HTTP Basic Auth against /report/pdf/... — Odoo accepts API keys
# as the Basic password for HTTP routes.
import base64
auth = base64.b64encode(f"{USER}:{KEY}".encode()).decode()
pdf_url = f"{URL}/report/pdf/{REPORT_NAME}/{PKG_ID}"
print(f"  GET {pdf_url}")
req = urllib.request.Request(pdf_url, headers={"Authorization": f"Basic {auth}"})
try:
    with opener.open(req, timeout=120) as r:
        data = r.read()
        ct = r.headers.get("Content-Type", "")
        if "application/pdf" not in ct:
            Path(OUT + ".html").write_bytes(data)
            sys.exit(f"  expected PDF, got {ct}; saved response to {OUT}.html")
        Path(OUT).write_bytes(data)
        print(f"saved {OUT} ({len(data)} bytes)")
except urllib.error.HTTPError as e:
    body = e.read().decode(errors='replace')
    print(f"  HTTP {e.code}: {body[:1500]}")
    sys.exit(1)
