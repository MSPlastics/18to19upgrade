"""Create the new modern MSP sale order report on a target Odoo instance.

Idempotent — if the view/action already exists by key/report_name, the
script updates them in place instead of creating duplicates. Lets you
iterate freely on the design and re-run.

Brand palette (sampled from MSP logo):
  primary navy   #0F2347
  secondary navy #1E3A6B
  light panel    #F4F6F9
  border         #E1E5EC
  text           #2C3E50
  muted          #7B8794

Usage:
    python create_msp_sale_report.py --target staging         # dry-run
    python create_msp_sale_report.py --target staging --commit
    python create_msp_sale_report.py --target prod --commit
"""
import argparse
import os
import ssl
import sys
import xmlrpc.client
from pathlib import Path

REPORT_VIEW_KEY = "msp.report_saleorder_msp_v1"
REPORT_ACTION_NAME = "Quotation / Order — MSP"

# The QWeb template. Self-contained — does NOT call web.external_layout
# (we want full control of header/footer for the modern design).
QWEB_ARCH = '''<t t-call="web.html_container">
    <t t-foreach="docs" t-as="doc">
        <t t-set="doc" t-value="doc.with_context(lang=doc.partner_id.lang)"/>
        <t t-set="company" t-value="doc.company_id or env.company"/>
        <t t-set="forced_vat" t-value="doc.fiscal_position_id.foreign_vat"/>
        <t t-set="is_proforma" t-value="env.context.get('proforma', False)"/>
        <t t-set="lines_to_report" t-value="doc._get_order_lines_to_report()"/>
        <t t-set="display_discount" t-value="any(l.discount for l in lines_to_report)"/>
        <div class="page" style="font-family: 'Lato','Helvetica Neue',Arial,sans-serif; color:#2C3E50; font-size:11px;">

            <!-- HEADER BAND -->
            <table style="width:100%; background-color:#0F2347; color:#FFFFFF; border-collapse:collapse; margin:0 0 24px 0;">
                <tr>
                    <td style="padding:22px 30px; vertical-align:middle; width:50%;">
                        <img t-if="company.logo" t-att-src="image_data_uri(company.logo)" style="max-height:56px; max-width:100%;" alt="Logo"/>
                    </td>
                    <td style="padding:22px 30px; vertical-align:middle; text-align:right; width:50%; line-height:1.5;">
                        <div style="font-size:14px; font-weight:700; letter-spacing:0.5px; margin-bottom:4px;">
                            <span t-field="company.name"/>
                        </div>
                        <div style="font-size:10px; color:#C8D2E0;">
                            <span t-field="company.partner_id" t-options='{"widget":"contact","fields":["address","phone","email"],"no_marker":true}'/>
                        </div>
                    </td>
                </tr>
            </table>

            <!-- TITLE -->
            <div style="padding:0 30px; margin-bottom:18px;">
                <h1 style="color:#0F2347; font-size:26px; font-weight:700; margin:0; letter-spacing:0.5px;">
                    <t t-if="is_proforma">PRO FORMA INVOICE</t>
                    <t t-elif="doc.state in ('draft','sent')">QUOTATION</t>
                    <t t-else="">SALES ORDER</t>
                </h1>
                <div style="color:#7B8794; font-size:13px; margin-top:4px;">
                    <span t-field="doc.name"/>
                </div>
            </div>

            <!-- BILL TO / SHIP TO -->
            <table style="width:100%; border-collapse:collapse; margin:0 0 24px 0;">
                <tr>
                    <td style="padding:0 30px; vertical-align:top; width:50%;">
                        <div style="background-color:#F4F6F9; border-left:3px solid #0F2347; padding:12px 16px;">
                            <div style="font-size:9px; font-weight:700; color:#0F2347; letter-spacing:1.2px; text-transform:uppercase; margin-bottom:6px;">Bill To</div>
                            <div style="line-height:1.5;">
                                <strong><span t-field="doc.partner_invoice_id" t-options='{"widget":"contact","fields":["name"],"no_marker":true}'/></strong><br/>
                                <span t-field="doc.partner_invoice_id" t-options='{"widget":"contact","fields":["address","phone"],"no_marker":true}'/>
                                <t t-if="doc.partner_invoice_id.vat">
                                    <br/><t t-out="company.account_fiscal_country_id.vat_label or 'Tax ID'"/>: <span t-field="doc.partner_invoice_id.vat"/>
                                </t>
                            </div>
                        </div>
                    </td>
                    <td style="padding:0 30px; vertical-align:top; width:50%;">
                        <div style="background-color:#F4F6F9; border-left:3px solid #0F2347; padding:12px 16px;">
                            <div style="font-size:9px; font-weight:700; color:#0F2347; letter-spacing:1.2px; text-transform:uppercase; margin-bottom:6px;">Ship To</div>
                            <div style="line-height:1.5;">
                                <strong><span t-field="doc.partner_shipping_id" t-options='{"widget":"contact","fields":["name"],"no_marker":true}'/></strong><br/>
                                <span t-field="doc.partner_shipping_id" t-options='{"widget":"contact","fields":["address","phone"],"no_marker":true}'/>
                            </div>
                        </div>
                    </td>
                </tr>
            </table>

            <!-- META STRIP -->
            <table style="width:auto; margin:0 30px 24px 30px; border-collapse:collapse;">
                <tr style="background-color:#0F2347; color:#FFFFFF;">
                    <th style="padding:8px 14px; font-size:9px; font-weight:700; letter-spacing:1px; text-transform:uppercase; text-align:left;">Order #</th>
                    <th style="padding:8px 14px; font-size:9px; font-weight:700; letter-spacing:1px; text-transform:uppercase; text-align:left;">
                        <t t-if="doc.state in ('draft','sent')">Quotation Date</t>
                        <t t-else="">Order Date</t>
                    </th>
                    <th style="padding:8px 14px; font-size:9px; font-weight:700; letter-spacing:1px; text-transform:uppercase; text-align:left;">Expiration</th>
                    <th style="padding:8px 14px; font-size:9px; font-weight:700; letter-spacing:1px; text-transform:uppercase; text-align:left;">Customer PO</th>
                    <th style="padding:8px 14px; font-size:9px; font-weight:700; letter-spacing:1px; text-transform:uppercase; text-align:left;">Salesperson</th>
                    <th t-if="doc.incoterm" style="padding:8px 14px; font-size:9px; font-weight:700; letter-spacing:1px; text-transform:uppercase; text-align:left;">Incoterm</th>
                </tr>
                <tr>
                    <td style="padding:9px 14px; border-bottom:1px solid #E1E5EC;"><span t-field="doc.name"/></td>
                    <td style="padding:9px 14px; border-bottom:1px solid #E1E5EC;"><span t-field="doc.date_order" t-options='{"widget":"date"}'/></td>
                    <td style="padding:9px 14px; border-bottom:1px solid #E1E5EC;">
                        <t t-if="doc.validity_date"><span t-field="doc.validity_date" t-options='{"widget":"date"}'/></t>
                        <t t-else=""><span style="color:#C8D2E0;">—</span></t>
                    </td>
                    <td style="padding:9px 14px; border-bottom:1px solid #E1E5EC;">
                        <t t-if="doc.client_order_ref"><span t-field="doc.client_order_ref"/></t>
                        <t t-else=""><span style="color:#C8D2E0;">—</span></t>
                    </td>
                    <td style="padding:9px 14px; border-bottom:1px solid #E1E5EC;"><span t-field="doc.user_id"/></td>
                    <td t-if="doc.incoterm" style="padding:9px 14px; border-bottom:1px solid #E1E5EC;">
                        <span t-field="doc.incoterm.code"/><t t-if="doc.incoterm_location"> – <span t-field="doc.incoterm_location"/></t>
                    </td>
                </tr>
            </table>

            <!-- LINE ITEMS -->
            <table style="width:auto; margin:0 30px; border-collapse:collapse;">
                <thead>
                    <tr style="background-color:#0F2347; color:#FFFFFF;">
                        <th style="padding:10px 12px; font-size:10px; font-weight:700; letter-spacing:0.5px; text-transform:uppercase; text-align:left;">Description</th>
                        <th style="padding:10px 12px; font-size:10px; font-weight:700; letter-spacing:0.5px; text-transform:uppercase; text-align:left;">MSP Part #</th>
                        <th style="padding:10px 12px; font-size:10px; font-weight:700; letter-spacing:0.5px; text-transform:uppercase; text-align:right;">Quantity</th>
                        <th style="padding:10px 12px; font-size:10px; font-weight:700; letter-spacing:0.5px; text-transform:uppercase; text-align:right;">Unit Price</th>
                        <th t-if="display_discount" style="padding:10px 12px; font-size:10px; font-weight:700; letter-spacing:0.5px; text-transform:uppercase; text-align:right;">Disc.</th>
                        <th style="padding:10px 12px; font-size:10px; font-weight:700; letter-spacing:0.5px; text-transform:uppercase; text-align:left;">Tax</th>
                        <th style="padding:10px 12px; font-size:10px; font-weight:700; letter-spacing:0.5px; text-transform:uppercase; text-align:right;">Amount</th>
                    </tr>
                </thead>
                <tbody>
                    <t t-foreach="lines_to_report" t-as="line">
                        <tr t-if="line.display_type == 'line_section'" style="background-color:#E8EEF5;">
                            <td t-att-colspan="display_discount and 7 or 6" style="padding:8px 12px; font-weight:700; color:#0F2347; text-transform:uppercase; font-size:10px; letter-spacing:0.5px;">
                                <span t-field="line.name"/>
                            </td>
                        </tr>
                        <tr t-elif="line.display_type == 'line_note'" style="background-color:#F8F9FB;">
                            <td t-att-colspan="display_discount and 7 or 6" style="padding:6px 12px; font-style:italic; color:#7B8794;">
                                <span t-field="line.name"/>
                            </td>
                        </tr>
                        <tr t-else="" style="border-bottom:1px solid #E1E5EC;">
                            <td style="padding:9px 12px; vertical-align:top;"><span t-field="line.name"/></td>
                            <td style="padding:9px 12px; vertical-align:top;"><span t-field="line.product_customer_code"/></td>
                            <td style="padding:9px 12px; vertical-align:top; text-align:right;">
                                <span t-field="line.product_uom_qty"/>
                                <span t-field="line.product_uom_id" groups="uom.group_uom"/>
                                <t t-if="line.product_packaging_id">
                                    <br/><span style="color:#7B8794; font-size:10px;">(<span t-field="line.product_packaging_qty" t-options='{"widget":"integer"}'/> <span t-field="line.product_packaging_id"/>)</span>
                                </t>
                            </td>
                            <td style="padding:9px 12px; vertical-align:top; text-align:right;">
                                <span t-field="line.price_unit" t-options='{"widget":"monetary","display_currency":doc.currency_id}'/>
                            </td>
                            <td t-if="display_discount" style="padding:9px 12px; vertical-align:top; text-align:right;">
                                <t t-if="line.discount"><span t-field="line.discount"/>%</t>
                            </td>
                            <td style="padding:9px 12px; vertical-align:top; font-size:10px;">
                                <t t-set="taxes" t-value="', '.join([(t.invoice_label or t.name) for t in line.tax_ids])"/>
                                <span t-out="taxes"/>
                            </td>
                            <td style="padding:9px 12px; vertical-align:top; text-align:right; font-weight:600;">
                                <span t-field="line.price_subtotal" t-options='{"widget":"monetary","display_currency":doc.currency_id}'/>
                            </td>
                        </tr>
                    </t>
                </tbody>
            </table>

            <!-- TOTALS -->
            <table style="width:auto; margin:24px 30px 0 30px; border-collapse:collapse;">
                <tr>
                    <td style="width:60%; padding:0;"></td>
                    <td style="width:40%; padding:0; vertical-align:top;">
                        <table style="width:100%; border-collapse:collapse;">
                            <tr style="border-bottom:1px solid #E1E5EC;">
                                <td style="padding:8px 14px; text-align:right; color:#7B8794; font-weight:600; font-size:10px; text-transform:uppercase; letter-spacing:0.5px;">Subtotal</td>
                                <td style="padding:8px 14px; text-align:right; width:130px;">
                                    <span t-field="doc.amount_untaxed" t-options='{"widget":"monetary","display_currency":doc.currency_id}'/>
                                </td>
                            </tr>
                            <tr t-if="doc.amount_tax" style="border-bottom:1px solid #E1E5EC;">
                                <td style="padding:8px 14px; text-align:right; color:#7B8794; font-weight:600; font-size:10px; text-transform:uppercase; letter-spacing:0.5px;">Tax</td>
                                <td style="padding:8px 14px; text-align:right;">
                                    <span t-field="doc.amount_tax" t-options='{"widget":"monetary","display_currency":doc.currency_id}'/>
                                </td>
                            </tr>
                            <tr style="background-color:#0F2347; color:#FFFFFF;">
                                <td style="padding:12px 14px; text-align:right; font-weight:700; text-transform:uppercase; letter-spacing:1.2px; font-size:11px;">Total</td>
                                <td style="padding:12px 14px; text-align:right; font-weight:700; font-size:14px;">
                                    <span t-field="doc.amount_total" t-options='{"widget":"monetary","display_currency":doc.currency_id}'/>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>

            <!-- NOTES -->
            <div style="padding:0 30px; margin-top:30px;">
                <t t-if="doc.payment_term_id and doc.payment_term_id.note">
                    <div style="margin-bottom:12px;">
                        <div style="font-weight:700; color:#0F2347; text-transform:uppercase; letter-spacing:1px; font-size:9px; margin-bottom:4px;">Payment Terms</div>
                        <span t-field="doc.payment_term_id.note"/>
                    </div>
                </t>
                <t t-if="doc.fiscal_position_id and doc.fiscal_position_id.sudo().note">
                    <div style="margin-bottom:12px;">
                        <div style="font-weight:700; color:#0F2347; text-transform:uppercase; letter-spacing:1px; font-size:9px; margin-bottom:4px;">Fiscal Position Remark</div>
                        <span t-field="doc.fiscal_position_id.sudo().note"/>
                    </div>
                </t>
                <t t-if="doc.note">
                    <div style="margin-bottom:12px;">
                        <div style="font-weight:700; color:#0F2347; text-transform:uppercase; letter-spacing:1px; font-size:9px; margin-bottom:4px;">Notes</div>
                        <span t-field="doc.note"/>
                    </div>
                </t>
            </div>

            <!-- SIGNATURE -->
            <div t-if="doc.signed_by" style="padding:12px 30px 0 30px; margin-top:24px; border-top:2px solid #0F2347;">
                <div style="font-weight:700; color:#0F2347; text-transform:uppercase; letter-spacing:1px; font-size:9px; margin-bottom:4px;">Signed by</div>
                <span t-field="doc.signed_by"/>
            </div>

        </div>
    </t>
</t>
'''


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


def upsert_view(call, commit):
    existing = call("ir.ui.view", "search_read",
                    [[("key", "=", REPORT_VIEW_KEY)]],
                    {"fields": ["id", "key", "write_date"]})
    arch = QWEB_ARCH
    if existing:
        vid = existing[0]["id"]
        if not commit:
            print(f"  view {vid} ({REPORT_VIEW_KEY}): would UPDATE")
            return vid
        call("ir.ui.view", "write", [[vid], {"arch_db": arch, "active": True}])
        print(f"  view {vid} ({REPORT_VIEW_KEY}): UPDATED")
        return vid
    if not commit:
        print(f"  view ({REPORT_VIEW_KEY}): would CREATE")
        return None
    vid = call("ir.ui.view", "create", [{
        "name": "MSP Quotation/Order Report",
        "type": "qweb",
        "key": REPORT_VIEW_KEY,
        "arch_db": arch,
        "active": True,
    }])
    print(f"  view {vid} ({REPORT_VIEW_KEY}): CREATED")
    return vid


def upsert_action(call, commit):
    existing = call("ir.actions.report", "search_read",
                    [[("report_name", "=", REPORT_VIEW_KEY)]],
                    {"fields": ["id", "name"]})
    so_model_id = call("ir.model", "search", [[("model", "=", "sale.order")]])
    if not so_model_id:
        sys.exit("sale.order model not found")
    so_model_id = so_model_id[0]
    vals = {
        "name": REPORT_ACTION_NAME,
        "model": "sale.order",
        "report_type": "qweb-pdf",
        "report_name": REPORT_VIEW_KEY,
        "report_file": REPORT_VIEW_KEY,
        "binding_model_id": so_model_id,
        "binding_type": "report",
    }
    if existing:
        aid = existing[0]["id"]
        if not commit:
            print(f"  action {aid} ({REPORT_ACTION_NAME}): would UPDATE")
            return aid
        call("ir.actions.report", "write", [[aid], vals])
        print(f"  action {aid} ({REPORT_ACTION_NAME}): UPDATED")
        return aid
    if not commit:
        print(f"  action ({REPORT_ACTION_NAME}): would CREATE")
        return None
    aid = call("ir.actions.report", "create", [vals])
    print(f"  action {aid} ({REPORT_ACTION_NAME}): CREATED")
    return aid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["staging", "prod"], default="staging")
    parser.add_argument("--commit", action="store_true",
                        help="actually write (default: dry-run)")
    args = parser.parse_args()

    url, call = connect(args.target)
    print(f"Target: {args.target}  ({url})  mode: {'COMMIT' if args.commit else 'dry-run'}")

    upsert_view(call, args.commit)
    upsert_action(call, args.commit)

    if args.commit:
        print("\nDone. The report will appear under Print -> Quotation / Order — MSP")
        print("on any sale order. Reload the page if it doesn't show up immediately.")


if __name__ == "__main__":
    main()
