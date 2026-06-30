"""Create the MSP BLIND SHIP Delivery Slip report on a target Odoo instance.

A blind/neutral variant of the customer-facing Delivery Slip (create_msp_delivery_slip.py)
for distribution customers who don't want MSP's name on the shipping paperwork.
Identical to the standard MSP delivery slip EXCEPT:
  - NO company letterhead (logo / MSP name / address / phone / website) -- removed
  - NO "Sold To" block -- removed
  - Header shows ONLY the delivery address ("Ship To") -- promoted + enlarged
  - Item column header "MSP PN" -> "MFG PN"
  - Meta panel drops the internal "Sales Order" row (keeps Shipment #, Customer PO,
    Drop PO, Delivery Date)
Everything else (lot collapse, qty math, Total Pallets, POD signature block) is
identical to the standard slip.

Idempotent -- looks up by view key + report_name, updates if found, creates if not.
Coexists with the standard MSP Delivery Slip; this just adds another option in the
Print menu on a transfer ("Blind Ship Delivery Slip -- MSP").

Usage:
    python create_msp_blindship_slip.py --target staging         # dry-run
    python create_msp_blindship_slip.py --target staging --commit
    python create_msp_blindship_slip.py --target prod --commit   # AFTER staging confirmed
"""
import argparse
import os
import ssl
import sys
import xmlrpc.client
from pathlib import Path

REPORT_VIEW_KEY = "msp.report_blindship_slip_v1"
REPORT_ACTION_NAME = "Blind Ship Delivery Slip — MSP"

# Self-contained QWEB. Same field-mapping discipline as create_msp_delivery_slip.py
# (manual qty formatting, .splitlines(), address fallback to commercial_partner_id).
QWEB_ARCH = '''<t t-call="web.html_container">
    <t t-foreach="docs" t-as="doc">
        <t t-set="brand_navy" t-value="'#0A182F'"/>
        <t t-set="brand_panel" t-value="'#f1f5f9'"/>
        <t t-set="brand_border" t-value="'#cbd5e1'"/>
        <t t-set="brand_zebra" t-value="'#f8fafc'"/>
        <t t-set="brand_muted" t-value="'#334155'"/>
        <t t-set="ship_addr" t-value="doc.partner_id if doc.partner_id.street else doc.partner_id.commercial_partner_id"/>
        <t t-set="so" t-value="doc.sale_id"/>
        <t t-set="ship_instr" t-value="doc.partner_id.x_studio_shipping_instructions or doc.partner_id.commercial_partner_id.x_studio_shipping_instructions or ''"/>

        <div class="page" style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif; color:#111; font-size:9pt;">

            <!-- TOP LAYOUT: left = delivery address ONLY (blind: no sender / no MSP identity); right = meta panel -->
            <table style="width:100%; border-collapse:collapse; margin-bottom:25px;">
                <tr>
                    <!-- LEFT 70% -->
                    <td style="width:70%; vertical-align:top; padding-right:20px;">

                        <!-- SHIP TO (the delivery address) -->
                        <div style="border-left:4px solid #0A182F; padding-left:16px;">
                            <div style="font-size:8.5pt; font-weight:bold; text-transform:uppercase; color:white; background-color:#0A182F; padding:5px 10px; margin-bottom:10px; border-radius:2px; display:inline-block; letter-spacing:0.5px;">Ship To</div>
                            <div style="font-size:15pt; color:#0A182F; font-weight:bold; line-height:1.2;"><span t-field="doc.partner_id.name"/></div>
                            <div style="font-size:11pt; color:#334155; line-height:1.55; margin-top:5px;">
                                <t t-if="ship_addr.street"><span t-field="ship_addr.street"/><br/></t>
                                <t t-if="ship_addr.street2"><span t-field="ship_addr.street2"/><br/></t>
                                <t t-if="ship_addr.city"><span t-field="ship_addr.city"/></t><t t-if="ship_addr.state_id">, <span t-field="ship_addr.state_id.code"/></t><t t-if="ship_addr.zip"> <span t-field="ship_addr.zip"/></t>
                                <t t-if="ship_addr.country_id"><br/><span t-field="ship_addr.country_id.name"/></t>
                            </div>
                        </div>

                        <!-- SHIPPING INSTRUCTIONS BAND -->
                        <div t-if="ship_instr" style="margin-top:16px; background-color:#f1f5f9; border-left:4px solid #0A182F; padding:10px 14px; border-radius:2px;">
                            <div style="font-size:7.5pt; font-weight:bold; text-transform:uppercase; color:#0A182F; letter-spacing:0.5px; margin-bottom:5px;">Shipping Instructions</div>
                            <div style="font-size:9pt; color:#334155; line-height:1.4;">
                                <t t-foreach="ship_instr.splitlines()" t-as="instr_line">
                                    <div><t t-out="instr_line"/></div>
                                </t>
                            </div>
                        </div>
                    </td>

                    <!-- RIGHT 30% — meta panel -->
                    <td style="width:30%; background-color:#f1f5f9; vertical-align:top; padding:15px 20px; border-radius:4px; border-top:6px solid #0A182F;">
                        <div style="font-size:16pt; font-weight:bold; color:#0A182F; text-transform:uppercase; margin-bottom:12px; letter-spacing:1px;">Delivery Slip</div>
                        <table style="width:100%; border-collapse:collapse;">
                            <tr>
                                <td style="padding:5px 0; border-bottom:1px solid #cbd5e1; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Shipment #</td>
                                <td style="padding:5px 0; border-bottom:1px solid #cbd5e1; font-size:11pt; font-weight:bold; text-align:right; color:#0A182F; font-family:monospace;"><span t-field="doc.name"/></td>
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
                                <td style="padding:5px 0; font-size:7.5pt; text-transform:uppercase; color:#334155; font-weight:bold;">Delivery Date</td>
                                <td style="padding:5px 0; font-size:10pt; font-weight:bold; text-align:right; color:#0A182F;">
                                    <t t-if="doc.scheduled_date" t-out="doc.scheduled_date.strftime('%m/%d/%Y')"/>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>

            <!-- ITEM TABLE — 6 cols, portrait widths -->
            <table style="width:100%; border-collapse:collapse; margin-bottom:30px;" cellspacing="0">
                <thead>
                    <tr>
                        <th style="width:11%; background-color:#0A182F; color:white; text-align:left;   padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">MFG PN</th>
                        <th style="width:36%; background-color:#0A182F; color:white; text-align:left;   padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Description &amp; Packaging</th>
                        <th style="width:14%; background-color:#0A182F; color:white; text-align:center; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Lot Number</th>
                        <th style="width:13%; background-color:#0A182F; color:white; text-align:center; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Ordered Qty</th>
                        <th style="width:13%; background-color:#0A182F; color:white; text-align:center; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Shipped Qty</th>
                        <th style="width:13%; background-color:#0A182F; color:white; text-align:center; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Total Pallets</th>
                    </tr>
                </thead>
                <tbody>
                    <t t-set="row_idx" t-value="0"/>
                    <t t-foreach="doc.move_ids" t-as="move">
                        <t t-set="desc_src" t-value="(move.sale_line_id.name if move.sale_line_id else False) or move.product_id.display_name or ''"/>
                        <t t-set="desc_lines" t-value="desc_src.splitlines() or ['']"/>
                        <t t-set="order_qty" t-value="move.sale_line_id.product_uom_qty if move.sale_line_id else move.product_uom_qty"/>
                        <t t-set="order_uom_name" t-value="(move.sale_line_id.product_uom_id.name if move.sale_line_id else move.product_uom.name) or ''"/>
                        <t t-set="pkg" t-value="move.product_packaging_id"/>

                        <!-- Collapse per-unit/roll serial lots to their MO-level batch.
                             FG lot names are <MO>-<serial> where <MO> uses '/' not '-'. -->
                        <t t-set="lot_names" t-value="sorted({(n.rsplit('-', 1)[0] if '-' in n else n) for n in move.move_line_ids.mapped('lot_id.name') if n})"/>
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
                                <t t-out="', '.join(lot_names)"/>
                            </td>
                            <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; text-align:center; font-family:monospace; font-size:11pt;">
                                <t t-out="'{:g}'.format(order_qty)"/> <t t-out="order_uom_name"/>
                            </td>
                            <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; text-align:center; font-family:monospace; font-size:11pt; font-weight:bold;">
                                <t t-out="'{:g}'.format(move.quantity)"/> <t t-out="move.product_uom.name or ''"/>
                            </td>
                            <td style="padding:14px 10px; vertical-align:middle; border-bottom:1px solid #e2e8f0; text-align:center; font-family:monospace; font-size:11pt;">
                                <t t-set="dpkgs" t-value="move.move_line_ids.mapped('package_id')"/>
                                <t t-if="dpkgs"><t t-out="str(len(dpkgs))"/> <t t-out="'pallets' if len(dpkgs) != 1 else 'pallet'"/></t>
                                <t t-elif="pkg and pkg.qty"><t t-out="'{:g}'.format(move.quantity / pkg.qty)"/> <t t-out="pkg.name or ''"/></t>
                                <t t-else=""><span style="color:#94a3b8;">&#8212;</span></t>
                            </td>
                        </tr>
                    </t>
                </tbody>
            </table>

            <!-- POD SIGNATURE BLOCK: Shipper (left) + Received By (right) -->
            <table style="width:100%; border-collapse:collapse; margin-top:25px;">
                <tr>
                    <td style="width:50%; vertical-align:top; padding-right:12px;">
                        <div style="background-color:#f1f5f9; border-top:4px solid #0A182F; border-radius:4px; padding:18px;">
                            <div style="font-size:9pt; font-weight:bold; color:#0A182F; text-transform:uppercase; letter-spacing:1px; margin-bottom:14px;">Shipper</div>
                            <table style="width:100%; border-collapse:collapse;">
                                <tr>
                                    <td style="width:90px; padding:8px 0; font-size:9pt; font-weight:bold; color:#334155; text-transform:uppercase;">Signature:</td>
                                    <td style="padding:8px 0; color:#94a3b8; letter-spacing:2px;">_____________________________</td>
                                </tr>
                                <tr>
                                    <td style="padding:8px 0; font-size:9pt; font-weight:bold; color:#334155; text-transform:uppercase;">Date:</td>
                                    <td style="padding:8px 0; color:#94a3b8; letter-spacing:2px;">_____________________________</td>
                                </tr>
                            </table>
                        </div>
                    </td>
                    <td style="width:50%; vertical-align:top; padding-left:12px;">
                        <div style="background-color:#f1f5f9; border-top:4px solid #0A182F; border-radius:4px; padding:18px;">
                            <div style="font-size:9pt; font-weight:bold; color:#0A182F; text-transform:uppercase; letter-spacing:1px; margin-bottom:14px;">Received By</div>
                            <table style="width:100%; border-collapse:collapse;">
                                <tr>
                                    <td style="width:90px; padding:8px 0; font-size:9pt; font-weight:bold; color:#334155; text-transform:uppercase;">Signature:</td>
                                    <td style="padding:8px 0; color:#94a3b8; letter-spacing:2px;">_____________________________</td>
                                </tr>
                                <tr>
                                    <td style="padding:8px 0; font-size:9pt; font-weight:bold; color:#334155; text-transform:uppercase;">Date:</td>
                                    <td style="padding:8px 0; color:#94a3b8; letter-spacing:2px;">_____________________________</td>
                                </tr>
                            </table>
                        </div>
                    </td>
                </tr>
            </table>

        </div>
    </t>
</t>'''


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
        "name": "MSP Blind Ship Delivery Slip",
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
    pf = call("report.paperformat", "search", [[("name", "=", "US Letter")]])
    pf_id = pf[0] if pf else False
    vals = {
        "name": REPORT_ACTION_NAME,
        "model": "stock.picking",
        "report_type": "qweb-pdf",
        "report_name": REPORT_VIEW_KEY,
        "report_file": REPORT_VIEW_KEY,
        "binding_model_id": sp_model_id,
        "binding_type": "report",
        "print_report_name": "(object.name or 'DeliverySlip').replace('/', '-')",
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
    parser.add_argument("--commit", action="store_true", help="actually write (default: dry-run)")
    args = parser.parse_args()
    url, call = connect(args.target)
    print(f"Target: {args.target}  ({url})  mode: {'COMMIT' if args.commit else 'dry-run'}")
    upsert_view(call, args.commit)
    upsert_action(call, args.commit)
    if args.commit:
        print("\nDone. The report appears under Print -> Blind Ship Delivery Slip — MSP")
        print("on any Delivery Order. The standard MSP Delivery Slip is untouched.")


if __name__ == "__main__":
    main()
