"""Restore dynamic company-logo binding in Studio-customized external layouts.

When users edit an `external_layout_*` template through Odoo Studio's report
editor, Studio sometimes replaces the original dynamic
`<img t-att-src="image_data_uri(company.logo)"/>` with a hardcoded
`<img src="/web/image/{attachment_id}-..." data-attachment-id="..."/>`
pointing at whatever was uploaded at edit time. The result: when the
company logo is updated later, the PDF still shows the old image.

This patcher rewrites such hardcoded `<img>` tags inside web_studio
report-editor diff views (key prefix
`web_studio.report_editor_customization_diff.view._web.external_layout_`)
back to the dynamic v18/v19 pattern. Idempotent — only writes views
where a hardcoded `data-attachment-id` `<img>` is actually present.

Usage:
    python fix_external_layout_logo.py --target staging         # dry-run
    python fix_external_layout_logo.py --target staging --commit
    python fix_external_layout_logo.py --target prod --commit
"""
import argparse
import os
import re
import ssl
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


def connect(target):
    prefix = f"ODOO_{target.upper()}_"
    url = os.environ.get(prefix + "URL")
    db = os.environ.get(prefix + "DB")
    user = os.environ.get(prefix + "USER")
    api_key = os.environ.get(prefix + "API_KEY")
    if not all([url, db, user, api_key]):
        sys.exit(f"Missing {prefix}* env vars")
    ctx = ssl.create_default_context()
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", context=ctx, allow_none=True)
    uid = common.authenticate(db, user, api_key, {})
    if not uid:
        sys.exit(f"auth failed for {target}")
    obj = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", context=ctx, allow_none=True)

    def call(model, method, args, kwargs=None):
        return obj.execute_kw(db, uid, api_key, model, method, args, kwargs or {})
    return url, call


# An <img ...> tag that has src="/web/image/..." AND data-attachment-id="..."
# — the Studio-uploaded hardcoded form. Captures the class attribute so we
# can preserve any extra classes the user (or Studio) added.
_IMG_RE = re.compile(
    r'<img\b[^>]*?\bsrc="/web/image/[^"]*"[^>]*?\bdata-attachment-id="\d+"[^>]*?/?>',
)
_CLASS_RE = re.compile(r'class="([^"]*)"')


def rewrite(match):
    classes = "img img-fluid o_we_custom_image"
    cls = _CLASS_RE.search(match.group(0))
    if cls:
        classes = cls.group(1)
    return (
        f'<img t-if="company.logo" '
        f'class="{classes}" '
        f't-att-src="image_data_uri(company.logo)" '
        f'alt="Logo"/>'
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["staging", "prod"], default="staging")
    parser.add_argument("--commit", action="store_true",
                        help="actually write (default: dry-run)")
    args = parser.parse_args()

    url, call = connect(args.target)
    print(f"Target: {args.target}  ({url})  mode: {'COMMIT' if args.commit else 'dry-run'}")

    candidates = call("ir.ui.view", "search_read",
                      [[("type", "=", "qweb"), ("active", "=", True),
                        ("key", "ilike", "external_layout")]],
                      {"fields": ["id", "key", "arch_db"]})
    print(f"{len(candidates)} active external_layout-related QWeb views")

    patched = skipped = failed = 0
    for v in candidates:
        old = v["arch_db"] or ""
        matches = list(_IMG_RE.finditer(old))
        if not matches:
            skipped += 1
            continue
        new = _IMG_RE.sub(rewrite, old)
        if new == old:
            skipped += 1
            continue
        print(f"\n  id={v['id']} {v['key']!r}: rewriting {len(matches)} hardcoded <img> tag(s)")
        for m in matches:
            print(f"    OLD: {m.group(0)[:160]}{'...' if len(m.group(0))>160 else ''}")
        if not args.commit:
            patched += 1
            continue
        try:
            call("ir.ui.view", "write", [[v["id"]], {"arch_db": new}])
            patched += 1
        except Exception as e:
            print(f"    write FAILED: {str(e)[:200]}")
            failed += 1

    if args.commit:
        print(f"\nDone. Patched {patched}, untouched {skipped}, failed {failed}.")
    else:
        print(f"\nDRY-RUN. Would patch {patched}, untouched {skipped}.")
        print("Re-run with --commit to apply.")


if __name__ == "__main__":
    main()
