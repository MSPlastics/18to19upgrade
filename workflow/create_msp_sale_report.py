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
        <t t-set="brand_navy" t-value="'#0A182F'"/>
        <t t-set="brand_panel" t-value="'#f1f5f9'"/>
        <t t-set="brand_border" t-value="'#cbd5e1'"/>
        <t t-set="brand_zebra" t-value="'#f8fafc'"/>
        <t t-set="brand_text" t-value="'#0A182F'"/>
        <t t-set="brand_muted" t-value="'#334155'"/>
        <!-- Use the commercial partner's address when the invoice/shipping partner is a child without its own street -->
        <t t-set="bill_addr" t-value="doc.partner_invoice_id if doc.partner_invoice_id.street else doc.partner_invoice_id.commercial_partner_id"/>
        <t t-set="ship_addr" t-value="doc.partner_shipping_id if doc.partner_shipping_id.street else doc.partner_shipping_id.commercial_partner_id"/>
        <t t-set="sold_addr" t-value="doc.partner_id if doc.partner_id.street else doc.partner_id.commercial_partner_id"/>
        <!-- Currency symbol pulled once; we format amounts manually with a regular space to avoid wkhtmltopdf's NBSP-as-Latin1 corruption -->
        <t t-set="cur_sym" t-value="doc.currency_id.symbol"/>
        <div class="page" style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif; color:#111; font-size:9pt;">

            <!-- TOP LAYOUT: left = company + addresses; right = meta panel -->
            <table style="width:100%; border-collapse:collapse; margin-bottom:30px;">
                <tr>
                    <!-- LEFT 68% -->
                    <td style="width:68%; vertical-align:top; padding-right:20px;">

                        <!-- LOGO + COMPANY -->
                        <table style="width:100%; border-collapse:collapse; margin-bottom:25px;">
                            <tr>
                                <td style="width:90px; vertical-align:middle;">
                                    <img t-if="company.logo" t-att-src="image_data_uri(company.logo)" style="max-height:70px; max-width:80px;" alt="Logo"/>
                                </td>
                                <td style="vertical-align:middle; padding-left:14px;">
                                    <div style="font-size:18pt; font-weight:900; color:#0A182F; text-transform:uppercase; letter-spacing:-0.5px;">
                                        <span t-field="company.name"/>
                                    </div>
                                    <div style="font-size:8.5pt; color:#334155; font-weight:500; line-height:1.5;">
                                        <span t-field="company.street"/><t t-if="company.street2"> <span t-field="company.street2"/></t>
                                        <t t-if="company.city or company.state_id or company.zip"> | </t>
                                        <span t-if="company.city" t-field="company.city"/><t t-if="company.state_id">, <span t-field="company.state_id.code"/></t><t t-if="company.zip"> <span t-field="company.zip"/></t>
                                        <br/>
                                        <span t-if="company.phone" t-field="company.phone"/>
                                        <t t-if="company.phone and company.website"> | </t>
                                        <span t-if="company.website" t-field="company.website"/>
                                    </div>
                                </td>
                            </tr>
                        </table>

                        <!-- 3-UP ADDRESSES -->
                        <table style="width:100%; border-collapse:collapse; table-layout:fixed;">
                            <tr>
                                <td style="vertical-align:top; padding-right:15px;">
                                    <div style="font-size:7.5pt; font-weight:bold; text-transform:uppercase; color:white; background-color:#0A182F; padding:4px 8px; margin-bottom:8px; border-radius:2px; display:inline-block;">Bill To / Invoice</div>
                                    <br/>
                                    <strong style="font-size:9.5pt; color:#0A182F;"><t t-out="doc.partner_invoice_id.name"/></strong><br/>
                                    <t t-if="bill_addr.street"><t t-out="bill_addr.street"/><br/></t>
                                    <t t-if="bill_addr.street2"><t t-out="bill_addr.street2"/><br/></t>
                                    <t t-if="bill_addr.city or bill_addr.state_id or bill_addr.zip">
                                        <t t-out="bill_addr.city"/><t t-if="bill_addr.state_id">, <t t-out="bill_addr.state_id.code"/></t><t t-if="bill_addr.zip"> <t t-out="bill_addr.zip"/></t><br/>
                                    </t>
                                    <t t-if="bill_addr.country_id"><t t-out="bill_addr.country_id.name"/><br/></t>
                                    <t t-if="bill_addr.phone"><t t-out="bill_addr.phone"/></t>
                                    <t t-if="doc.partner_invoice_id.vat">
                                        <br/><t t-out="company.account_fiscal_country_id.vat_label or 'Tax ID'"/>: <t t-out="doc.partner_invoice_id.vat"/>
                                    </t>
                                </td>
                                <td style="vertical-align:top; padding-right:15px;">
                                    <div style="font-size:7.5pt; font-weight:bold; text-transform:uppercase; color:white; background-color:#0A182F; padding:4px 8px; margin-bottom:8px; border-radius:2px; display:inline-block;">Sold To / Branch</div>
                                    <br/>
                                    <strong style="font-size:9.5pt; color:#0A182F;"><t t-out="doc.partner_id.name"/></strong><br/>
                                    <t t-if="sold_addr.street"><t t-out="sold_addr.street"/><br/></t>
                                    <t t-if="sold_addr.street2"><t t-out="sold_addr.street2"/><br/></t>
                                    <t t-if="sold_addr.city or sold_addr.state_id or sold_addr.zip">
                                        <t t-out="sold_addr.city"/><t t-if="sold_addr.state_id">, <t t-out="sold_addr.state_id.code"/></t><t t-if="sold_addr.zip"> <t t-out="sold_addr.zip"/></t><br/>
                                    </t>
                                    <t t-if="sold_addr.country_id"><t t-out="sold_addr.country_id.name"/><br/></t>
                                    <t t-if="sold_addr.phone"><t t-out="sold_addr.phone"/></t>
                                </td>
                                <td style="vertical-align:top;">
                                    <div style="font-size:7.5pt; font-weight:bold; text-transform:uppercase; color:white; background-color:#0A182F; padding:4px 8px; margin-bottom:8px; border-radius:2px; display:inline-block;">Ship To</div>
                                    <br/>
                                    <strong style="font-size:9.5pt; color:#0A182F;"><t t-out="doc.partner_shipping_id.name"/></strong><br/>
                                    <t t-if="ship_addr.street"><t t-out="ship_addr.street"/><br/></t>
                                    <t t-if="ship_addr.street2"><t t-out="ship_addr.street2"/><br/></t>
                                    <t t-if="ship_addr.city or ship_addr.state_id or ship_addr.zip">
                                        <t t-out="ship_addr.city"/><t t-if="ship_addr.state_id">, <t t-out="ship_addr.state_id.code"/></t><t t-if="ship_addr.zip"> <t t-out="ship_addr.zip"/></t><br/>
                                    </t>
                                    <t t-if="ship_addr.country_id"><t t-out="ship_addr.country_id.name"/><br/></t>
                                    <t t-if="ship_addr.phone"><t t-out="ship_addr.phone"/></t>
                                </td>
                            </tr>
                        </table>
                    </td>

                    <!-- RIGHT 32%: META PANEL -->
                    <td style="width:32%; background-color:#f1f5f9; vertical-align:top; padding:20px; border-radius:4px; border-top:6px solid #0A182F; box-sizing:border-box;">
                        <div style="font-size:18pt; font-weight:bold; color:#0A182F; text-transform:uppercase; margin-bottom:15px; letter-spacing:1px;">
                            <t t-if="is_proforma">Pro Forma</t>
                            <t t-elif="doc.state in ('draft','sent')">Quotation</t>
                            <t t-else="">Sales Order</t>
                        </div>
                        <table style="width:100%; border-collapse:collapse;">
                            <tr><td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Order No</td><td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:13pt; font-weight:bold; text-align:right; color:#0A182F; font-family:monospace;"><span t-field="doc.name"/></td></tr>
                            <tr><td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Date</td><td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F;"><span t-field="doc.date_order" t-options='{"widget":"date"}'/></td></tr>
                            <tr t-if="doc.validity_date"><td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Expiration</td><td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F;"><span t-field="doc.validity_date" t-options='{"widget":"date"}'/></td></tr>
                            <tr><td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Customer PO</td><td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F; font-family:monospace;"><t t-if="doc.client_order_ref"><span t-field="doc.client_order_ref"/></t></td></tr>
                            <tr><td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Drop PO</td><td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F; font-family:monospace;"><t t-if="doc.msp_drop_po"><span t-field="doc.msp_drop_po"/></t></td></tr>
                            <tr t-if="doc.incoterm"><td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Incoterm</td><td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F;"><span t-field="doc.incoterm.code"/><t t-if="doc.incoterm_location"> <span t-field="doc.incoterm_location"/></t></td></tr>
                            <tr><td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Terms</td><td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F;"><t t-if="doc.payment_term_id"><span t-field="doc.payment_term_id"/></t></td></tr>
                            <tr><td style="padding:6px 0; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Acct Mgr</td><td style="padding:6px 0; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F;"><span t-field="doc.user_id"/></td></tr>
                        </table>
                    </td>
                </tr>
            </table>

            <!-- LINE ITEMS -->
            <table style="width:100%; border-collapse:collapse; margin-bottom:30px;">
                <thead>
                    <tr>
                        <th style="background-color:#0A182F; color:white; text-align:left; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px; width:12%;">MSP PN</th>
                        <th style="background-color:#0A182F; color:white; text-align:left; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px; width:33%;">Description</th>
                        <th style="background-color:#0A182F; color:white; text-align:left; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px; width:18%;">Shipping Info</th>
                        <th style="background-color:#0A182F; color:white; text-align:left; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px; width:13%;">Qty</th>
                        <th style="background-color:#0A182F; color:white; text-align:left; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px; width:10%;">Price</th>
                        <th style="background-color:#0A182F; color:white; text-align:right; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px; width:14%;">Amount</th>
                    </tr>
                </thead>
                <tbody>
                    <t t-set="row_index" t-value="0"/>
                    <t t-foreach="lines_to_report" t-as="line">
                        <!-- SECTION ROW -->
                        <tr t-if="line.display_type == 'line_section'" style="background-color:#e8eef5;">
                            <td colspan="6" style="padding:10px; font-weight:bold; color:#0A182F; text-transform:uppercase; font-size:9pt; letter-spacing:0.5px; border-bottom:1px solid #e2e8f0;">
                                <span t-field="line.name"/>
                            </td>
                        </tr>
                        <!-- NOTE ROW -->
                        <tr t-elif="line.display_type == 'line_note'">
                            <td colspan="6" style="padding:8px 10px; font-style:italic; color:#334155; font-size:8.5pt; border-bottom:1px solid #e2e8f0;">
                                <span t-field="line.name"/>
                            </td>
                        </tr>
                        <!-- PRODUCT ROW -->
                        <tr t-else="" t-att-style="row_index % 2 == 1 and 'background-color:#f8fafc;' or ''">
                            <t t-set="row_index" t-value="row_index + 1"/>
                            <!-- MSP PN: pull from product.product.name (MSP's internal product number, e.g. 10853) -->
                            <td style="padding:14px 10px; vertical-align:top; border-bottom:1px solid #e2e8f0; font-family:monospace; font-size:10pt; font-weight:bold;">
                                <t t-if="line.product_id"><t t-out="line.product_id.name"/></t>
                            </td>
                            <!-- Description: first line of line.name in bold + remaining lines below.
                                 splitlines() avoids the XML attribute normalisation that turns
                                 embedded \n into a single space inside split('\n',...). -->
                            <td style="padding:14px 10px; vertical-align:top; border-bottom:1px solid #e2e8f0;">
                                <t t-set="name_lines" t-value="(line.name or '').splitlines() or ['']"/>
                                <div style="font-weight:bold; font-size:10pt; color:#0A182F;">
                                    <t t-out="name_lines[0]"/>
                                </div>
                                <t t-foreach="name_lines[1:]" t-as="extra_line">
                                    <div style="color:#334155; font-size:8.5pt; margin-top:2px; font-weight:500;">
                                        <t t-out="extra_line"/>
                                    </div>
                                </t>
                                <t t-if="line.product_customer_code">
                                    <div style="margin-top:4px; font-size:8pt; color:#334155;">
                                        Cust Code: <t t-out="line.product_customer_code"/>
                                    </div>
                                </t>
                                <t t-if="line.discount">
                                    <div style="margin-top:4px; font-size:8pt; color:#0A182F; font-style:italic;">
                                        Discount: <t t-out="('%g' % line.discount)"/>%
                                    </div>
                                </t>
                            </td>
                            <td style="padding:14px 10px; vertical-align:top; border-bottom:1px solid #e2e8f0; font-size:8pt; color:#0A182F; font-style:italic;">
                                <t t-if="line.x_studio_freight_terms">
                                    <div><t t-out="line.x_studio_freight_terms"/></div>
                                </t>
                                <t t-if="line.x_studio_item_specific_freight_instructions">
                                    <t t-foreach="line.x_studio_item_specific_freight_instructions.splitlines()" t-as="instr_line">
                                        <div style="margin-top:2px; color:#334155; font-style:normal;">
                                            <t t-out="instr_line"/>
                                        </div>
                                    </t>
                                </t>
                            </td>
                            <td style="padding:14px 10px; vertical-align:top; border-bottom:1px solid #e2e8f0; font-family:monospace; font-size:10pt;">
                                <t t-out="('%g' % line.product_uom_qty)"/>
                                <t t-if="line.product_uom_id"> <t t-out="line.product_uom_id.name"/></t>
                                <t t-if="line.product_packaging_id">
                                    <br/><span style="color:#334155; font-size:8.5pt; font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">(<t t-out="('%g' % line.product_packaging_qty)"/> <t t-out="line.product_packaging_id.name"/>)</span>
                                </t>
                            </td>
                            <td style="padding:14px 10px; vertical-align:top; border-bottom:1px solid #e2e8f0;">
                                <t t-out="cur_sym + ' ' + '{:,.2f}'.format(line.price_unit)"/>
                            </td>
                            <td style="padding:14px 10px; vertical-align:top; border-bottom:1px solid #e2e8f0; text-align:right; font-weight:bold;">
                                <t t-out="cur_sym + ' ' + '{:,.2f}'.format(line.price_subtotal)"/>
                            </td>
                        </tr>
                    </t>
                </tbody>
            </table>

            <!-- TOTALS -->
            <div style="width:100%; text-align:right;">
                <table style="display:inline-table; width:280px; border-collapse:collapse; background-color:#f1f5f9; border-radius:4px;">
                    <tr>
                        <td style="padding:10px 15px; text-align:right; color:#334155; font-size:10pt; font-weight:bold;">Untaxed Amount:</td>
                        <td style="padding:10px 15px; text-align:right; font-weight:bold; font-size:10pt; color:#0A182F;">
                            <t t-out="cur_sym + ' ' + '{:,.2f}'.format(doc.amount_untaxed)"/>
                        </td>
                    </tr>
                    <tr t-if="doc.amount_tax">
                        <td style="padding:10px 15px; text-align:right; color:#334155; font-size:10pt; font-weight:bold;">Tax:</td>
                        <td style="padding:10px 15px; text-align:right; font-weight:bold; font-size:10pt; color:#0A182F;">
                            <t t-out="cur_sym + ' ' + '{:,.2f}'.format(doc.amount_tax)"/>
                        </td>
                    </tr>
                    <tr style="font-size:15pt; font-weight:800; color:white; background-color:#0A182F;">
                        <td style="padding:10px 15px; text-align:right; color:white; border-radius:0 0 0 4px;">TOTAL</td>
                        <td style="padding:10px 15px; text-align:right; color:white; border-radius:0 0 4px 0;">
                            <t t-out="cur_sym + ' ' + '{:,.2f}'.format(doc.amount_total)"/>
                        </td>
                    </tr>
                </table>
            </div>

            <!-- FOOTER NOTES -->
            <div style="margin-top:30px; font-size:8.5pt;">
                <t t-if="doc.payment_term_id and doc.payment_term_id.note">
                    <div style="margin-bottom:12px;">
                        <div style="font-weight:bold; color:#0A182F; text-transform:uppercase; letter-spacing:0.5px; font-size:7.5pt; margin-bottom:4px;">Payment Terms</div>
                        <span t-field="doc.payment_term_id.note"/>
                    </div>
                </t>
                <t t-if="doc.fiscal_position_id and doc.fiscal_position_id.sudo().note">
                    <div style="margin-bottom:12px;">
                        <div style="font-weight:bold; color:#0A182F; text-transform:uppercase; letter-spacing:0.5px; font-size:7.5pt; margin-bottom:4px;">Fiscal Position Remark</div>
                        <span t-field="doc.fiscal_position_id.sudo().note"/>
                    </div>
                </t>
                <t t-if="doc.note">
                    <div style="margin-bottom:12px;">
                        <div style="font-weight:bold; color:#0A182F; text-transform:uppercase; letter-spacing:0.5px; font-size:7.5pt; margin-bottom:4px;">Terms &amp; Conditions</div>
                        <span t-field="doc.note"/>
                    </div>
                </t>
            </div>

            <!-- SIGNATURE -->
            <div t-if="doc.signed_by" style="margin-top:24px; padding-top:12px; border-top:2px solid #0A182F;">
                <div style="font-weight:bold; color:#0A182F; text-transform:uppercase; letter-spacing:0.5px; font-size:7.5pt; margin-bottom:4px;">Signed by</div>
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
