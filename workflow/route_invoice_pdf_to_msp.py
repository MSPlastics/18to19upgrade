"""Route the standard Odoo invoice PDF flow through the MSP invoice view.

Why: the Send Invoice wizard in v17+ generates a cached PDF on
account.move.invoice_pdf_report_id using a HARDCODED report
(account.report_invoice_with_payments). The email template's
report_template_ids gets layered on TOP of that cached PDF — so wiring
just the email template to our MSP report produced two attachments per
send: a standard PDF (from the cache) and an MSP PDF (from the template).

Fix: replace the thin wrapper view `account.report_invoice_with_payments`
(id varies; lookup by key) with a delegate that t-calls our MSP view.
Then the cached PDF IS the MSP report, and we can empty
report_template_ids on the email templates so nothing extra layers on.

The wrapper view we're rewriting is just 191 chars in stock Odoo; nothing
inherits from it (the four inheriting views all hang off the inner
`account.report_invoice_document` view, which we leave untouched). To
revert: re-run with --restore to put the original Odoo arch back.

Idempotent — re-running detects the current state and only writes when
needed.

Usage:
    python route_invoice_pdf_to_msp.py --target staging         # dry-run
    python route_invoice_pdf_to_msp.py --target staging --commit
    python route_invoice_pdf_to_msp.py --target prod --commit
    python route_invoice_pdf_to_msp.py --target staging --restore --commit  # undo
"""
import argparse
import os
import ssl
import sys
import xmlrpc.client
from pathlib import Path

WRAPPER_KEY = "account.report_invoice_with_payments"
MSP_VIEW_KEY = "msp.report_invoice_msp_v1"

# Original stock-Odoo wrapper arch — what we restore on --restore. Match it
# verbatim against ir.ui.view.arch_db when checking idempotency.
ORIGINAL_ARCH = (
    '<t t-name="account.report_invoice_with_payments">\n'
    '            <t t-call="account.report_invoice">\n'
    '                <t t-set="print_with_payments" t-value="True"/>\n'
    '            </t>\n'
    '        </t>'
)

# What we install — a one-line delegate to the MSP view.
DELEGATE_ARCH = (
    f'<t t-name="{WRAPPER_KEY}">\n'
    f'    <t t-call="{MSP_VIEW_KEY}"/>\n'
    f'</t>'
)

# Email templates to clear report_template_ids on. The cached PDF (now MSP)
# is the ONLY attachment we want — extra rendering from the template layer
# is what was producing duplicates.
TEMPLATE_NAMES = ["Invoice: Sending", "Credit Note: Sending"]


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["staging", "prod"], default="staging")
    parser.add_argument("--commit", action="store_true",
                        help="actually write (default: dry-run)")
    parser.add_argument("--restore", action="store_true",
                        help="restore the original Odoo wrapper arch + put report_template_ids back")
    args = parser.parse_args()

    url, call = connect(args.target)
    print(f"Target: {args.target}  ({url})  mode: {'COMMIT' if args.commit else 'dry-run'}"
          f"{'  (RESTORE)' if args.restore else ''}")

    # --- 1. Patch (or restore) the wrapper view -------------------------------

    rows = call("ir.ui.view", "search_read",
                [[("key", "=", WRAPPER_KEY)]],
                {"fields": ["id", "key", "arch_db"]})
    if not rows:
        sys.exit(f"View {WRAPPER_KEY!r} not found — is account module installed?")
    if len(rows) > 1:
        print(f"  WARNING: {len(rows)} views with key={WRAPPER_KEY!r}; using id={rows[0]['id']}")
    v = rows[0]
    target_arch = ORIGINAL_ARCH if args.restore else DELEGATE_ARCH
    current = v["arch_db"] or ""
    if current.strip() == target_arch.strip():
        print(f"  view {v['id']} ({WRAPPER_KEY}): already at target arch, skipping")
    else:
        action = "would write" if not args.commit else "wrote"
        print(f"  view {v['id']} ({WRAPPER_KEY}): {action} new arch")
        print(f"    OLD ({len(current)} chars): {current[:120]!r}{'…' if len(current) > 120 else ''}")
        print(f"    NEW ({len(target_arch)} chars): {target_arch[:120]!r}{'…' if len(target_arch) > 120 else ''}")
        if args.commit:
            call("ir.ui.view", "write", [[v["id"]], {"arch_db": target_arch}])

    # --- 2. Sync report_template_ids on the email templates -------------------

    if args.restore:
        # On restore, put the MSP report back on the templates (we also need
        # the action id, search by report_name).
        rep = call("ir.actions.report", "search",
                   [[("report_name", "=", MSP_VIEW_KEY)]])
        target_ids = rep[:1] if rep else []
    else:
        target_ids = []   # cached MSP PDF is the only attachment we want

    for tname in TEMPLATE_NAMES:
        rows = call("mail.template", "search_read",
                    [[("model", "=", "account.move"), ("name", "=", tname)]],
                    {"fields": ["id", "name", "report_template_ids"]})
        if not rows:
            print(f"  template {tname!r}: NOT FOUND, skipping")
            continue
        t = rows[0]
        current = t.get("report_template_ids") or []
        if list(current) == list(target_ids):
            print(f"  template id={t['id']:>3} {tname!r}: already at target "
                  f"report_template_ids={list(target_ids)}, skipping")
            continue
        action = "would set" if not args.commit else "set"
        print(f"  template id={t['id']:>3} {tname!r}: {action} "
              f"report_template_ids={list(target_ids)}  (was: {list(current)})")
        if args.commit:
            call("mail.template", "write",
                 [[t["id"]], {"report_template_ids": [(6, 0, list(target_ids))]}])

    if args.commit:
        if args.restore:
            print("\nRestored. Standard wrapper arch back; MSP report re-attached on templates.")
        else:
            print("\nDone. Send Invoice now generates a single MSP-styled PDF (the cached")
            print("invoice_pdf_report_id IS rendered via the MSP view; templates no longer")
            print("layer on a second copy).")
    else:
        print("\nDRY-RUN. Re-run with --commit to apply.")


if __name__ == "__main__":
    main()
