"""Create the MSP custom invoice report on a target Odoo instance.

A customer-facing invoice PDF bound to account.move, MSP-styled to match
the sale order report (same logo block, same brand palette, same totals
panel) plus a Lot Number column.

Lots: account.move.line.sale_line_ids -> stock.move (sale_line_id) ->
move_line_ids -> lot_id. Combined comma-joined onto ONE invoice-line row
(per accounting's preference — they don't want multiple lines per
invoice line just because picking split across lots).

Idempotent — looks up by view key + report_name, updates if found,
creates if not. Re-run after editing QWEB_ARCH to push design changes.

Coexists with Odoo's standard account.report_invoice / report_invoice_with_payments
(those stay untouched and still appear in the same Print menu).

Usage:
    python create_msp_invoice.py --target staging         # dry-run
    python create_msp_invoice.py --target staging --commit
    python create_msp_invoice.py --target prod --commit   # AFTER staging confirmed
"""
import argparse
import os
import ssl
import sys
import xmlrpc.client
from pathlib import Path

REPORT_VIEW_KEY = "msp.report_invoice_msp_v1"
REPORT_ACTION_NAME = "Invoice — MSP"

# Self-contained QWEB. Same design discipline as create_msp_sale_report.py:
#   - manual amount formatting (no `widget="monetary"`) to dodge wkhtmltopdf
#     NBSP-as-Latin1 corruption
#   - .splitlines() not .split('\n', 1) — XML normalizes embedded newlines
#   - address fallback to commercial_partner_id when contact has no street
QWEB_ARCH = '''<t t-call="web.html_container">
    <t t-foreach="docs" t-as="doc">
        <t t-set="doc" t-value="doc.with_context(lang=doc.partner_id.lang)"/>
        <t t-set="company" t-value="doc.company_id or env.company"/>
        <t t-set="brand_navy" t-value="'#0A182F'"/>
        <t t-set="brand_panel" t-value="'#f1f5f9'"/>
        <t t-set="brand_border" t-value="'#cbd5e1'"/>
        <t t-set="brand_zebra" t-value="'#f8fafc'"/>
        <t t-set="brand_muted" t-value="'#334155'"/>
        <!-- 3-up address mapping for invoices, matching the sale order report:
               Bill To  = doc.partner_id (account.move sets partner_id from sale.order.partner_invoice_id)
               Sold To  = primary_so.partner_id (original customer on the SO); fall back to commercial_partner_id when no SO link
               Ship To  = doc.partner_shipping_id (falls back to partner_id when blank)
             Each address falls back to its commercial_partner_id when the contact carries no street of its own. -->
        <t t-set="primary_so" t-value="doc.invoice_line_ids.sale_line_ids.order_id[:1]"/>
        <t t-set="bill_addr" t-value="doc.partner_id if doc.partner_id.street else doc.partner_id.commercial_partner_id"/>
        <t t-set="sold_partner" t-value="primary_so.partner_id if primary_so else doc.partner_id.commercial_partner_id"/>
        <t t-set="sold_addr" t-value="sold_partner if sold_partner.street else sold_partner.commercial_partner_id"/>
        <t t-set="ship_partner" t-value="doc.partner_shipping_id or doc.partner_id"/>
        <t t-set="ship_addr" t-value="ship_partner if ship_partner.street else ship_partner.commercial_partner_id"/>
        <t t-set="cur_sym" t-value="doc.currency_id.symbol or ''"/>
        <!-- Document type label, state-aware -->
        <t t-set="is_refund" t-value="doc.move_type == 'out_refund'"/>
        <t t-set="is_draft" t-value="doc.state == 'draft'"/>
        <t t-set="report_title">
            <t t-if="is_draft and is_refund">Draft Credit Note</t>
            <t t-elif="is_draft">Draft Invoice</t>
            <t t-elif="is_refund">Credit Note</t>
            <t t-else="">Invoice</t>
        </t>

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
                                        <t t-if="company.phone"><t t-out="company.phone"/></t>
                                        <t t-if="company.phone and company.website"> | </t>
                                        <t t-if="company.website"><t t-out="company.website"/></t>
                                    </div>
                                </td>
                            </tr>
                        </table>

                        <!-- 3-UP ADDRESSES: Bill To / Sold To / Ship To — same layout as sale order report -->
                        <table style="width:100%; border-collapse:collapse; table-layout:fixed;">
                            <tr>
                                <td style="vertical-align:top; padding-right:15px;">
                                    <div style="font-size:7.5pt; font-weight:bold; text-transform:uppercase; color:white; background-color:#0A182F; padding:4px 8px; margin-bottom:8px; border-radius:2px; display:inline-block;">Bill To / Invoice</div>
                                    <br/>
                                    <strong style="font-size:9.5pt; color:#0A182F;"><t t-out="doc.partner_id.name"/></strong><br/>
                                    <t t-if="bill_addr.street"><t t-out="bill_addr.street"/><br/></t>
                                    <t t-if="bill_addr.street2"><t t-out="bill_addr.street2"/><br/></t>
                                    <t t-if="bill_addr.city or bill_addr.state_id or bill_addr.zip">
                                        <t t-out="bill_addr.city"/><t t-if="bill_addr.state_id">, <t t-out="bill_addr.state_id.code"/></t><t t-if="bill_addr.zip"> <t t-out="bill_addr.zip"/></t><br/>
                                    </t>
                                    <t t-if="bill_addr.country_id"><t t-out="bill_addr.country_id.name"/><br/></t>
                                    <t t-if="bill_addr.phone"><t t-out="bill_addr.phone"/></t>
                                    <t t-if="doc.partner_id.vat">
                                        <br/><t t-out="company.account_fiscal_country_id.vat_label or 'Tax ID'"/>: <t t-out="doc.partner_id.vat"/>
                                    </t>
                                </td>
                                <td style="vertical-align:top; padding-right:15px;">
                                    <div style="font-size:7.5pt; font-weight:bold; text-transform:uppercase; color:white; background-color:#0A182F; padding:4px 8px; margin-bottom:8px; border-radius:2px; display:inline-block;">Sold To / Branch</div>
                                    <br/>
                                    <strong style="font-size:9.5pt; color:#0A182F;"><t t-out="sold_partner.name"/></strong><br/>
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
                                    <strong style="font-size:9.5pt; color:#0A182F;"><t t-out="ship_partner.name"/></strong><br/>
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

                    <!-- RIGHT 32% — meta panel -->
                    <td style="width:32%; background-color:#f1f5f9; vertical-align:top; padding:20px; border-radius:4px; border-top:6px solid #0A182F; box-sizing:border-box;">
                        <div style="font-size:18pt; font-weight:bold; color:#0A182F; text-transform:uppercase; margin-bottom:15px; letter-spacing:1px;">
                            <t t-out="report_title"/>
                        </div>
                        <table style="width:100%; border-collapse:collapse;">
                            <tr>
                                <td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Invoice No</td>
                                <td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:13pt; font-weight:bold; text-align:right; color:#0A182F; font-family:monospace;">
                                    <t t-out="doc.name or '/'"/>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Invoice Date</td>
                                <td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F;">
                                    <t t-if="doc.invoice_date" t-out="doc.invoice_date.strftime('%m/%d/%Y')"/>
                                </td>
                            </tr>
                            <tr t-if="doc.invoice_date_due">
                                <td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Due Date</td>
                                <td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F;">
                                    <t t-out="doc.invoice_date_due.strftime('%m/%d/%Y')"/>
                                </td>
                            </tr>
                            <tr t-if="doc.invoice_origin">
                                <td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Source</td>
                                <td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F; font-family:monospace;">
                                    <span t-field="doc.invoice_origin"/>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Customer PO</td>
                                <td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F; font-family:monospace;">
                                    <t t-out="(primary_so.client_order_ref if primary_so else '') or ''"/>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Drop PO</td>
                                <td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F; font-family:monospace;">
                                    <t t-out="(primary_so.msp_drop_po if (primary_so and 'msp_drop_po' in primary_so._fields) else '') or ''"/>
                                </td>
                            </tr>
                            <tr t-if="doc.invoice_payment_term_id">
                                <td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Terms</td>
                                <td style="padding:6px 0; border-bottom:1px solid #cbd5e1; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F;">
                                    <span t-field="doc.invoice_payment_term_id"/>
                                </td>
                            </tr>
                            <tr t-if="doc.invoice_user_id">
                                <td style="padding:6px 0; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Acct Mgr</td>
                                <td style="padding:6px 0; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F;">
                                    <span t-field="doc.invoice_user_id"/>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>

            <!-- LINE ITEMS -->
            <table style="width:100%; border-collapse:collapse; margin-bottom:30px;">
                <thead>
                    <tr>
                        <th style="background-color:#0A182F; color:white; text-align:left;  padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px; width:11%;">MSP PN</th>
                        <th style="background-color:#0A182F; color:white; text-align:left;  padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px; width:33%;">Description</th>
                        <th style="background-color:#0A182F; color:white; text-align:left;  padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px; width:18%;">Lot Number</th>
                        <th style="background-color:#0A182F; color:white; text-align:left;  padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px; width:11%;">Qty</th>
                        <th style="background-color:#0A182F; color:white; text-align:left;  padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px; width:11%;">Price</th>
                        <th style="background-color:#0A182F; color:white; text-align:right; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px; width:16%;">Amount</th>
                    </tr>
                </thead>
                <tbody>
                    <t t-set="row_index" t-value="0"/>
                    <t t-foreach="doc.invoice_line_ids" t-as="line">
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
                            <!-- Lots: walk line.sale_line_ids -> move_ids -> move_line_ids -> lot_id; dedupe + sort + comma-join. ONE row per invoice line per accounting's request. -->
                            <t t-set="lot_names" t-value="sorted({n for n in line.sale_line_ids.move_ids.move_line_ids.mapped('lot_id.name') if n})"/>
                            <td style="padding:14px 10px; vertical-align:top; border-bottom:1px solid #e2e8f0; font-family:monospace; font-size:10pt; font-weight:bold;">
                                <t t-if="line.product_id"><t t-out="line.product_id.name"/></t>
                            </td>
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
                                <t t-if="line.discount">
                                    <div style="margin-top:4px; font-size:8pt; color:#0A182F; font-style:italic;">
                                        Discount: <t t-out="('%g' % line.discount)"/>%
                                    </div>
                                </t>
                            </td>
                            <td style="padding:14px 10px; vertical-align:top; border-bottom:1px solid #e2e8f0; font-family:monospace; font-size:9pt; color:#0A182F; font-weight:bold;">
                                <t t-out="', '.join(lot_names)"/>
                            </td>
                            <td style="padding:14px 10px; vertical-align:top; border-bottom:1px solid #e2e8f0; font-family:monospace; font-size:10pt;">
                                <t t-out="('%g' % line.quantity)"/>
                                <t t-if="line.product_uom_id"> <t t-out="line.product_uom_id.name"/></t>
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
                    <!-- Payments + balance. So a PAID (or partially-paid / reversed) invoice
                         prints each reconciled entry, dated, plus the remaining balance — e.g.
                         "$0.00" once settled — for the accountant. Driven off the SAME data the
                         account form shows: invoice_payments_widget['content'] (Odoo 19; the old
                         account.move._get_reconciled_info_JSON_values() helper was removed in v19).
                         sudo() because the field is gated to the accounting groups, so it must
                         compute regardless of who prints. Labels match the form: refunds /
                         credit-note reversals -> "Reversed on", real payments -> "Paid on".
                         Amounts formatted manually with cur_sym (the widget's
                         amount_company_currency carries an NBSP that wkhtmltopdf corrupts); the
                         'date' is a datetime.date in QWeb -> strftime to MM/DD/YYYY, same as the
                         invoice_date / due fields in the meta panel above. -->
                    <t t-if="doc.state == 'posted'">
                        <t t-set="pay_widget" t-value="doc.sudo().invoice_payments_widget or {}"/>
                        <t t-set="payments_vals" t-value="pay_widget.get('content') or []"/>
                        <t t-foreach="payments_vals" t-as="pv">
                            <t t-set="pdate" t-value="pv.get('date')"/>
                            <t t-set="pdate_fmt" t-value="pdate.strftime('%m/%d/%Y') if pdate else ''"/>
                            <!-- Customer Payment Reference (msp_payment_ref module): pulled from
                                 the account.payment behind this reconciled entry; only real
                                 customer payments carry one (refund/reversal entries don't). -->
                            <t t-set="cust_ref" t-value="doc.env['account.payment'].sudo().browse(pv.get('account_payment_id')).customer_payment_ref if pv.get('account_payment_id') else False"/>
                            <tr>
                                <td style="padding:7px 15px; text-align:right; color:#334155; font-size:9pt; font-style:italic;">
                                    <t t-if="pv.get('is_exchange')">Exchange Difference:</t>
                                    <t t-elif="pv.get('is_refund')">Reversed on <t t-out="pdate_fmt"/>:</t>
                                    <t t-else="">Paid on <t t-out="pdate_fmt"/><t t-if="cust_ref">, ref <t t-out="cust_ref"/></t>:</t>
                                </td>
                                <td style="padding:7px 15px; text-align:right; font-weight:bold; font-size:9pt; color:#0A182F;">
                                    <t t-out="cur_sym + ' ' + '{:,.2f}'.format(pv.get('amount') or 0.0)"/>
                                </td>
                            </tr>
                        </t>
                        <!-- Remaining balance: shown whenever there are payments (so a fully-paid
                             invoice reads $0.00) OR whenever a residual remains on a posted move. -->
                        <tr t-if="payments_vals or (doc.amount_residual and doc.amount_residual != doc.amount_total)" style="background-color:#e8eef5;">
                            <td style="padding:10px 15px; text-align:right; color:#0A182F; font-size:11pt; font-weight:800; border-radius:0 0 0 4px;">Amount Due:</td>
                            <td style="padding:10px 15px; text-align:right; font-weight:800; font-size:11pt; color:#0A182F; border-radius:0 0 4px 0;">
                                <t t-out="cur_sym + ' ' + '{:,.2f}'.format(doc.amount_residual)"/>
                            </td>
                        </tr>
                    </t>
                </table>
            </div>

            <!-- FOOTER NOTES -->
            <div style="margin-top:30px; font-size:8.5pt;">
                <t t-if="doc.invoice_payment_term_id and doc.invoice_payment_term_id.note">
                    <div style="margin-bottom:12px;">
                        <div style="font-weight:bold; color:#0A182F; text-transform:uppercase; letter-spacing:0.5px; font-size:7.5pt; margin-bottom:4px;">Payment Terms</div>
                        <span t-field="doc.invoice_payment_term_id.note"/>
                    </div>
                </t>
                <t t-if="doc.fiscal_position_id and doc.fiscal_position_id.sudo().note">
                    <div style="margin-bottom:12px;">
                        <div style="font-weight:bold; color:#0A182F; text-transform:uppercase; letter-spacing:0.5px; font-size:7.5pt; margin-bottom:4px;">Fiscal Position Remark</div>
                        <span t-field="doc.fiscal_position_id.sudo().note"/>
                    </div>
                </t>
                <t t-if="doc.narration">
                    <div style="margin-bottom:12px;">
                        <div style="font-weight:bold; color:#0A182F; text-transform:uppercase; letter-spacing:0.5px; font-size:7.5pt; margin-bottom:4px;">Terms &amp; Conditions</div>
                        <span t-field="doc.narration"/>
                    </div>
                </t>
                <t t-if="doc.payment_reference">
                    <div style="margin-bottom:12px;">
                        <div style="font-weight:bold; color:#0A182F; text-transform:uppercase; letter-spacing:0.5px; font-size:7.5pt; margin-bottom:4px;">Payment Reference</div>
                        <t t-out="doc.payment_reference"/>
                    </div>
                </t>
            </div>

        </div>
    </t>
</t>
'''


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Upserters
# --------------------------------------------------------------------------

def upsert_view(call, commit):
    existing = call("ir.ui.view", "search_read",
                    [[("key", "=", REPORT_VIEW_KEY)]],
                    {"fields": ["id", "key"]})
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
        "name": "MSP Invoice Report",
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
    am_model_id = call("ir.model", "search", [[("model", "=", "account.move")]])
    if not am_model_id:
        sys.exit("account.move model not found")
    am_model_id = am_model_id[0]
    # Portrait Letter — paperformat the user already tuned L/R margins on.
    pf = call("report.paperformat", "search", [[("name", "=", "US Letter")]])
    pf_id = pf[0] if pf else False
    vals = {
        "name": REPORT_ACTION_NAME,
        "model": "account.move",
        "report_type": "qweb-pdf",
        "report_name": REPORT_VIEW_KEY,
        "report_file": REPORT_VIEW_KEY,
        "binding_model_id": am_model_id,
        "binding_type": "report",
        # Filename: invoice number can have slashes (INV/2026/00578); replace
        # with dashes so the file lands as INV-2026-00578.pdf.
        "print_report_name": "(object.name or 'Invoice').replace('/', '-')",
    }
    if pf_id:
        vals["paperformat_id"] = pf_id
    if existing:
        aid = existing[0]["id"]
        if not commit:
            print(f"  action {aid} ({REPORT_ACTION_NAME}): would UPDATE  (paperformat_id={pf_id or 'unset'})")
            return aid
        call("ir.actions.report", "write", [[aid], vals])
        print(f"  action {aid} ({REPORT_ACTION_NAME}): UPDATED  (paperformat_id={pf_id or 'unset'})")
        return aid
    if not commit:
        print(f"  action ({REPORT_ACTION_NAME}): would CREATE  (paperformat_id={pf_id or 'unset'})")
        return None
    aid = call("ir.actions.report", "create", [vals])
    print(f"  action {aid} ({REPORT_ACTION_NAME}): CREATED  (paperformat_id={pf_id or 'unset'})")
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
        print("\nDone. The report appears under Print -> Invoice — MSP")
        print("on any Customer Invoice. Existing standard invoice reports are untouched.")


if __name__ == "__main__":
    main()
