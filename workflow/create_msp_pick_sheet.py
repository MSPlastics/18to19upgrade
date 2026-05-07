"""Create the MSP Warehouse Pick Sheet report on a target Odoo instance.

A custom landscape pick sheet bound to stock.picking (delivery orders).
One table row per stock.move.line so workers see exactly which lot to pick
and how much from each. Pallets + Weight are blank write-in columns the
floor team fills in by hand.

Idempotent — looks up by view key + report_name, updates if found,
creates if not. Re-run after editing QWEB_ARCH to push design changes.

Coexists with Odoo's standard delivery slip + picking operations report;
this just adds another option in the Print menu on a transfer.

Usage:
    python create_msp_pick_sheet.py --target staging         # dry-run
    python create_msp_pick_sheet.py --target staging --commit
    python create_msp_pick_sheet.py --target prod --commit   # AFTER staging confirmed
"""
import argparse
import os
import ssl
import sys
import xmlrpc.client
from pathlib import Path

REPORT_VIEW_KEY = "msp.report_pick_sheet_v1"
REPORT_ACTION_NAME = "Warehouse Pick Sheet — MSP"

# Self-contained QWEB. Does NOT call web.external_layout — full control of
# header/footer for the modern landscape design. Mirrors the field-mapping
# discipline from create_msp_sale_report.py:
#   - manual amount/qty formatting (no `widget="monetary"`) to dodge wkhtmltopdf
#     NBSP-as-Latin1 corruption
#   - .splitlines() instead of .split('\n', 1) so the XML parser doesn't
#     normalize embedded newlines into spaces before evaluation
#   - address fallback to commercial_partner_id when contact has no street
QWEB_ARCH = '''<t t-call="web.html_container">
    <t t-foreach="docs" t-as="doc">
        <t t-set="company" t-value="doc.company_id or env.company"/>
        <t t-set="brand_navy" t-value="'#0A182F'"/>
        <t t-set="brand_panel" t-value="'#f1f5f9'"/>
        <t t-set="brand_border" t-value="'#cbd5e1'"/>
        <t t-set="brand_zebra" t-value="'#f8fafc'"/>
        <t t-set="brand_muted" t-value="'#334155'"/>
        <!-- Address fallback: invoice/ship contacts on child partners often carry no street -->
        <t t-set="ship_addr" t-value="doc.partner_id if doc.partner_id.street else doc.partner_id.commercial_partner_id"/>
        <t t-set="so" t-value="doc.sale_id"/>
        <!-- Sold-to: customer on the SO; fall back to delivery partner_id when no SO is linked (manual transfers, etc.) -->
        <t t-set="sold_partner" t-value="(so.partner_id if so else doc.partner_id) or doc.partner_id"/>
        <t t-set="sold_addr" t-value="sold_partner if sold_partner.street else sold_partner.commercial_partner_id"/>
        <t t-set="ship_instr" t-value="doc.partner_id.x_studio_shipping_instructions or doc.partner_id.commercial_partner_id.x_studio_shipping_instructions or ''"/>

        <div class="page" style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif; color:#111; font-size:9pt;">

            <!-- TOP LAYOUT: left = company + ship-to; right = meta panel -->
            <table style="width:100%; border-collapse:collapse; margin-bottom:25px;">
                <tr>
                    <!-- LEFT 70% -->
                    <td style="width:70%; vertical-align:top; padding-right:20px;">

                        <!-- LOGO + COMPANY -->
                        <table style="width:100%; border-collapse:collapse; margin-bottom:20px;">
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

                        <!-- SOLD TO + SHIP TO side-by-side -->
                        <table style="width:100%; border-collapse:collapse;">
                            <tr>
                                <td style="width:50%; vertical-align:top; padding-right:12px;">
                                    <div style="border-left:4px solid #0A182F; padding-left:15px;">
                                        <div style="font-size:7.5pt; font-weight:bold; text-transform:uppercase; color:white; background-color:#0A182F; padding:4px 8px; margin-bottom:8px; border-radius:2px; display:inline-block;">Sold To</div>
                                        <div style="font-size:11pt; color:#0A182F; font-weight:bold;"><span t-field="sold_partner.name"/></div>
                                        <div style="font-size:10pt; color:#334155;">
                                            <t t-if="sold_addr.street"><span t-field="sold_addr.street"/><br/></t>
                                            <t t-if="sold_addr.street2"><span t-field="sold_addr.street2"/><br/></t>
                                            <t t-if="sold_addr.city"><span t-field="sold_addr.city"/></t><t t-if="sold_addr.state_id">, <span t-field="sold_addr.state_id.code"/></t><t t-if="sold_addr.zip"> <span t-field="sold_addr.zip"/></t>
                                            <t t-if="sold_addr.country_id"><br/><span t-field="sold_addr.country_id.name"/></t>
                                        </div>
                                    </div>
                                </td>
                                <td style="width:50%; vertical-align:top; padding-left:12px;">
                                    <div style="border-left:4px solid #0A182F; padding-left:15px;">
                                        <div style="font-size:7.5pt; font-weight:bold; text-transform:uppercase; color:white; background-color:#0A182F; padding:4px 8px; margin-bottom:8px; border-radius:2px; display:inline-block;">Ship To</div>
                                        <div style="font-size:11pt; color:#0A182F; font-weight:bold;"><span t-field="doc.partner_id.name"/></div>
                                        <div style="font-size:10pt; color:#334155;">
                                            <t t-if="ship_addr.street"><span t-field="ship_addr.street"/><br/></t>
                                            <t t-if="ship_addr.street2"><span t-field="ship_addr.street2"/><br/></t>
                                            <t t-if="ship_addr.city"><span t-field="ship_addr.city"/></t><t t-if="ship_addr.state_id">, <span t-field="ship_addr.state_id.code"/></t><t t-if="ship_addr.zip"> <span t-field="ship_addr.zip"/></t>
                                            <t t-if="ship_addr.country_id"><br/><span t-field="ship_addr.country_id.name"/></t>
                                        </div>
                                    </div>
                                </td>
                            </tr>
                        </table>

                        <!-- SHIPPING INSTRUCTIONS BAND (spans full width of LEFT cell) -->
                        <div t-if="ship_instr" style="margin-top:14px; background-color:#f1f5f9; border-left:4px solid #0A182F; padding:10px 14px; border-radius:2px;">
                            <div style="font-size:7.5pt; font-weight:bold; text-transform:uppercase; color:#0A182F; letter-spacing:0.5px; margin-bottom:5px;">Shipping Instructions</div>
                            <div style="font-size:9.5pt; color:#334155; line-height:1.4;">
                                <t t-foreach="ship_instr.splitlines()" t-as="instr_line">
                                    <div><t t-out="instr_line"/></div>
                                </t>
                            </div>
                        </div>
                    </td>

                    <!-- RIGHT 30% — meta panel -->
                    <td style="width:30%; background-color:#f1f5f9; vertical-align:top; padding:15px 20px; border-radius:4px; border-top:6px solid #0A182F;">
                        <div style="font-size:16pt; font-weight:bold; color:#0A182F; text-transform:uppercase; margin-bottom:12px; letter-spacing:1px;">Warehouse Pick Sheet</div>
                        <table style="width:100%; border-collapse:collapse;">
                            <tr>
                                <td style="padding:5px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Transfer</td>
                                <td style="padding:5px 0; border-bottom:1px solid #cbd5e1; font-size:11pt; font-weight:bold; text-align:right; color:#0A182F; font-family:monospace;"><span t-field="doc.name"/></td>
                            </tr>
                            <tr>
                                <td style="padding:5px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Sales Order</td>
                                <td style="padding:5px 0; border-bottom:1px solid #cbd5e1; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F; font-family:monospace;">
                                    <t t-out="(so.name if so else (doc.origin or '')) or ''"/>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding:5px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Customer PO</td>
                                <td style="padding:5px 0; border-bottom:1px solid #cbd5e1; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F; font-family:monospace;">
                                    <t t-out="(so.client_order_ref if so else '') or ''"/>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding:5px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Drop PO</td>
                                <td style="padding:5px 0; border-bottom:1px solid #cbd5e1; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F; font-family:monospace;">
                                    <t t-out="(so.msp_drop_po if (so and 'msp_drop_po' in so._fields) else '') or ''"/>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding:5px 0; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Scheduled Date</td>
                                <td style="padding:5px 0; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F;">
                                    <t t-if="doc.scheduled_date" t-out="doc.scheduled_date.strftime('%m/%d/%Y')"/>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>

            <!-- ITEM TABLE -->
            <table style="width:100%; border-collapse:collapse; margin-bottom:30px;" cellspacing="0">
                <thead>
                    <tr>
                        <th style="width:7%;  background-color:#0A182F; color:white; text-align:left;   padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">MSP PN</th>
                        <th style="width:26%; background-color:#0A182F; color:white; text-align:left;   padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Description &amp; Packaging</th>
                        <th style="width:12%; background-color:#0A182F; color:white; text-align:center; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Lot Number</th>
                        <th style="width:10%; background-color:#0A182F; color:white; text-align:center; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Order Qty</th>
                        <th style="width:11%; background-color:#0A182F; color:white; text-align:center; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Pick Qty</th>
                        <th style="width:12%; background-color:#0A182F; color:white; text-align:center; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Pack Qty</th>
                        <th style="width:11%; background-color:#0A182F; color:white; text-align:center; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Total Pallets</th>
                        <th style="width:11%; background-color:#0A182F; color:white; text-align:center; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Total Weight</th>
                    </tr>
                </thead>
                <tbody>
                    <t t-set="row_idx" t-value="0"/>
                    <t t-foreach="doc.move_ids" t-as="move">
                        <!-- description: SO line name carries the rich SKU+pack copy; fall back to product display name -->
                        <t t-set="desc_src" t-value="(move.sale_line_id.name if move.sale_line_id else False) or move.product_id.display_name or ''"/>
                        <t t-set="desc_lines" t-value="desc_src.splitlines() or ['']"/>
                        <!-- order qty = customer's SO line qty so the picker sees full picture; fall back to move qty -->
                        <t t-set="order_qty" t-value="move.sale_line_id.product_uom_qty if move.sale_line_id else move.product_uom_qty"/>
                        <t t-set="order_uom_name" t-value="(move.sale_line_id.product_uom_id.name if move.sale_line_id else move.product_uom.name) or ''"/>
                        <!-- packaging shared across all rows of this move -->
                        <t t-set="pkg" t-value="move.product_packaging_id"/>

                        <t t-if="move.move_line_ids">
                            <!-- One row per move_line so multi-lot moves split out per lot -->
                            <t t-foreach="move.move_line_ids" t-as="ml">
                                <t t-set="row_idx" t-value="row_idx + 1"/>
                                <t t-set="bg" t-value="brand_zebra if (row_idx % 2 == 0) else '#ffffff'"/>
                                <tr t-att-style="'background-color:' + bg + ';'">
                                    <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; font-family:monospace; font-size:11pt; font-weight:bold;">
                                        <t t-out="move.product_id.name or ''"/>
                                    </td>
                                    <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0;">
                                        <div style="font-weight:bold; font-size:10.5pt; color:#0A182F;"><t t-out="desc_lines[0]"/></div>
                                        <t t-if="len(desc_lines) > 1">
                                            <div style="color:#334155; font-size:8.5pt; margin-top:4px; font-weight:500; line-height:1.3;">
                                                <t t-foreach="desc_lines[1:]" t-as="ln"><t t-out="ln"/><br/></t>
                                            </div>
                                        </t>
                                    </td>
                                    <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; text-align:center; font-family:monospace; font-size:10pt; font-weight:bold; color:#0A182F;">
                                        <t t-out="(ml.lot_id.name if ml.lot_id else '') or ''"/>
                                    </td>
                                    <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; text-align:center; font-family:monospace; font-size:11pt;">
                                        <t t-out="'{:g}'.format(order_qty)"/> <t t-out="order_uom_name"/>
                                    </td>
                                    <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; text-align:center; font-family:monospace; font-size:11pt; font-weight:bold;">
                                        <t t-out="'{:g}'.format(ml.quantity)"/> <t t-out="ml.product_uom_id.name or ''"/>
                                    </td>
                                    <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; text-align:center; font-family:monospace; font-size:11pt;">
                                        <t t-if="pkg and pkg.qty">
                                            <t t-out="'{:g}'.format(ml.quantity / pkg.qty)"/> <t t-out="pkg.name or ''"/>
                                        </t>
                                    </td>
                                    <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; text-align:center; color:#94a3b8; letter-spacing:2px;">_______________</td>
                                    <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; text-align:center; color:#94a3b8; letter-spacing:2px;">_______________</td>
                                </tr>
                            </t>
                        </t>
                        <t t-else="">
                            <!-- No reservations yet (state != assigned, or untracked auto-pick): one row per move, blank lot -->
                            <t t-set="row_idx" t-value="row_idx + 1"/>
                            <t t-set="bg" t-value="brand_zebra if (row_idx % 2 == 0) else '#ffffff'"/>
                            <tr t-att-style="'background-color:' + bg + ';'">
                                <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; font-family:monospace; font-size:11pt; font-weight:bold;">
                                    <t t-out="move.product_id.name or ''"/>
                                </td>
                                <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0;">
                                    <div style="font-weight:bold; font-size:10.5pt; color:#0A182F;"><t t-out="desc_lines[0]"/></div>
                                    <t t-if="len(desc_lines) > 1">
                                        <div style="color:#334155; font-size:8.5pt; margin-top:4px; font-weight:500; line-height:1.3;">
                                            <t t-foreach="desc_lines[1:]" t-as="ln"><t t-out="ln"/><br/></t>
                                        </div>
                                    </t>
                                </td>
                                <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; text-align:center;"></td>
                                <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; text-align:center; font-family:monospace; font-size:11pt;">
                                    <t t-out="'{:g}'.format(order_qty)"/> <t t-out="order_uom_name"/>
                                </td>
                                <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; text-align:center; font-family:monospace; font-size:11pt; font-weight:bold;">
                                    <!-- Pick Qty = move.quantity (matches Odoo's Operations UI). Demand stays in Order Qty column. -->
                                    <t t-out="'{:g}'.format(move.quantity)"/> <t t-out="move.product_uom.name or ''"/>
                                </td>
                                <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; text-align:center; font-family:monospace; font-size:11pt;">
                                    <t t-if="pkg and pkg.qty">
                                        <t t-out="'{:g}'.format(move.quantity / pkg.qty)"/> <t t-out="pkg.name or ''"/>
                                    </t>
                                </td>
                                <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; text-align:center; color:#94a3b8; letter-spacing:2px;">_______________</td>
                                <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; text-align:center; color:#94a3b8; letter-spacing:2px;">_______________</td>
                            </tr>
                        </t>
                    </t>
                </tbody>
            </table>

            <!-- SIGN-OFF (right-aligned, mirrors the design) -->
            <div style="width:100%; margin-top:20px; clear:both;">
                <table style="float:right; width:400px; border-collapse:collapse; background-color:#f1f5f9; border-top:4px solid #0A182F; border-radius:4px;" cellspacing="0">
                    <tr>
                        <td style="padding:15px; font-size:9pt; font-weight:bold; color:#334155; text-transform:uppercase;">Staged By:</td>
                        <td style="padding:15px; color:#94a3b8; letter-spacing:2px;">_____________________________</td>
                    </tr>
                    <tr>
                        <td style="padding:15px; font-size:9pt; font-weight:bold; color:#334155; text-transform:uppercase;">Loaded By:</td>
                        <td style="padding:15px; color:#94a3b8; letter-spacing:2px;">_____________________________</td>
                    </tr>
                </table>
            </div>

        </div>
    </t>
</t>'''


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
        "name": "MSP Warehouse Pick Sheet",
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
    sp_model_id = call("ir.model", "search", [[("model", "=", "stock.picking")]])
    if not sp_model_id:
        sys.exit("stock.picking model not found")
    sp_model_id = sp_model_id[0]
    # Letter Landscape paperformat (id 10 on this DB). If margins need adjusting
    # the user tunes them in Settings -> Paper Format (we're not provisioning a
    # dedicated paperformat for this report).
    pf_landscape = call("report.paperformat", "search",
                        [[("name", "=", "US Letter LANDSCAPE")]])
    pf_id = pf_landscape[0] if pf_landscape else False
    vals = {
        "name": REPORT_ACTION_NAME,
        "model": "stock.picking",
        "report_type": "qweb-pdf",
        "report_name": REPORT_VIEW_KEY,
        "report_file": REPORT_VIEW_KEY,
        "binding_model_id": sp_model_id,
        "binding_type": "report",
        # Filename: picking name has slashes (WH/OUT/00390) which break some
        # browsers' download UI; replace them so the file lands as
        # WH-OUT-00390.pdf. Evaluated against `object` at render time.
        "print_report_name": "(object.name or 'PickSheet').replace('/', '-')",
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
        print("\nDone. The report appears under Print -> Warehouse Pick Sheet — MSP")
        print("on any Delivery Order. Existing standard reports (Delivery Slip, Picking")
        print("Operations) are untouched and still available in the same Print menu.")


if __name__ == "__main__":
    main()
