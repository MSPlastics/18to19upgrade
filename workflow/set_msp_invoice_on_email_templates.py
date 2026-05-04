"""Set the new MSP invoice report as the attached report on the
standard account.move email templates ("Invoice: Sending" and
"Credit Note: Sending").

Why both: the MSP invoice action is state-aware — its title renders
"Credit Note" / "Draft Credit Note" when move_type=='out_refund' and
"Invoice" / "Draft Invoice" otherwise — so the same action works for
both flows. Wiring only the invoice template would leave credit notes
emailing the legacy standard report.

Skips Self-billing templates (no report attached, not customer-facing)
and Journal Notification (internal).

Idempotent — only writes templates whose attached report differs from
the target. Safe to re-run.

Usage:
    python set_msp_invoice_on_email_templates.py --target staging         # dry-run
    python set_msp_invoice_on_email_templates.py --target staging --commit
    python set_msp_invoice_on_email_templates.py --target prod --commit
"""
import argparse
import os
import ssl
import sys
import xmlrpc.client
from pathlib import Path

NEW_REPORT_KEY = "msp.report_invoice_msp_v1"

# Match by name (stable across builds) rather than id (varies by env).
TEMPLATE_NAMES_TO_UPDATE = [
    "Invoice: Sending",
    "Credit Note: Sending",
]


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
    args = parser.parse_args()

    url, call = connect(args.target)
    print(f"Target: {args.target}  ({url})  mode: {'COMMIT' if args.commit else 'dry-run'}")

    rep = call("ir.actions.report", "search_read",
               [[("report_name", "=", NEW_REPORT_KEY)]],
               {"fields": ["id", "name"]})
    if not rep:
        sys.exit(f"New report {NEW_REPORT_KEY!r} not found on this target — deploy it first.")
    new_report_id = rep[0]["id"]
    print(f"New report: id={new_report_id} name={rep[0]['name']!r}\n")

    updated = skipped = missing = 0
    for tname in TEMPLATE_NAMES_TO_UPDATE:
        rows = call("mail.template", "search_read",
                    [[("model", "=", "account.move"), ("name", "=", tname)]],
                    {"fields": ["id", "name", "report_template_ids"]})
        if not rows:
            print(f"  template {tname!r}: NOT FOUND, skipping")
            missing += 1
            continue
        t = rows[0]
        current = t.get("report_template_ids") or []
        if current == [new_report_id]:
            print(f"  template id={t['id']:>4} {tname!r}: already set, skipping")
            skipped += 1
            continue
        old_names = ""
        if current:
            old = call("ir.actions.report", "read", [current], {"fields": ["name"]})
            old_names = ", ".join(repr(o["name"]) for o in old)
        action = "would set" if not args.commit else "set"
        print(f"  template id={t['id']:>4} {tname!r}: {action} report_template_ids={[new_report_id]}  (was: {old_names or 'none'})")
        if args.commit:
            call("mail.template", "write",
                 [[t["id"]], {"report_template_ids": [(6, 0, [new_report_id])]}])
            updated += 1

    if args.commit:
        print(f"\nDone. Updated {updated}, already-correct {skipped}, missing {missing}.")
    else:
        print(f"\nDRY-RUN. Would update {len(TEMPLATE_NAMES_TO_UPDATE) - skipped - missing} templates.")
        print("Re-run with --commit to apply.")


if __name__ == "__main__":
    main()
