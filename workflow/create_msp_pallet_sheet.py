"""Create the MSP Pallet Sheet report on a target Odoo instance.

A custom QWeb report bound to stock.package. Operators / shipping
print this from the package form (Print -> Pallet Sheet — MSP) to get
a one-page summary of what's on a pallet — pallet ID + QR, MO + product,
dimensions, gross weight, contents (lot / qty per move_line), and
finalize timestamp.

Idempotent — looks up by view key + report_name, updates if found,
creates if not. Re-run after editing QWEB_ARCH to push design changes.

Mirrors the design language of operatorUI/templates/pallet_report_pdf.html
but adapted to QWeb + stock.package data model. Phase 6 of
PALLET_SHIPPING_PLAN.md.

Usage:
    python create_msp_pallet_sheet.py --target staging
    python create_msp_pallet_sheet.py --target staging --commit
    python create_msp_pallet_sheet.py --target prod --commit  # AFTER staging
"""
import argparse
import os
import ssl
import sys
import xmlrpc.client
from pathlib import Path

REPORT_VIEW_KEY = "msp.report_pallet_sheet_v1"
REPORT_ACTION_NAME = "Pallet Sheet — MSP"

# Self-contained QWeb (no web.external_layout). Mirrors the pick-sheet pattern:
#   - manual qty formatting via {:g} so wkhtmltopdf doesn't NBSP-corrupt
#   - QR via the report/barcode controller, sized 110x110px
#   - msp_mo_ids is computed in the addon from lot.lot_producing_ids, so
#     packages built from delivery move_lines still get the originating MO
QWEB_ARCH = '''<t t-call="web.html_container">
    <t t-foreach="docs" t-as="doc">
        <t t-set="company" t-value="doc.company_id or env.company"/>
        <t t-set="brand_navy" t-value="'#0A182F'"/>
        <t t-set="brand_panel" t-value="'#f1f5f9'"/>
        <t t-set="brand_border" t-value="'#cbd5e1'"/>
        <t t-set="brand_zebra" t-value="'#f8fafc'"/>
        <t t-set="origin_mo" t-value="doc.msp_mo_ids[:1]"/>
        <t t-set="origin_product" t-value="origin_mo.product_id if origin_mo else False"/>
        <t t-set="origin_customer" t-value="(origin_mo.x_studio_customer if (origin_mo and 'x_studio_customer' in origin_mo._fields) else False)"/>
        <t t-set="case_count" t-value="len(doc.move_line_ids) or len(doc.quant_ids)"/>

        <div class="page" style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif; color:#111; font-size:9pt;">

            <!-- HEADER: company / title / QR -->
            <table style="width:100%; border-collapse:collapse; margin-bottom:18px;">
                <tr>
                    <td style="width:55%; vertical-align:top;">
                        <table style="border-collapse:collapse;">
                            <tr>
                                <td style="width:80px; vertical-align:middle;">
                                    <img t-if="company.logo" t-att-src="image_data_uri(company.logo)" style="max-height:60px; max-width:70px;" alt="Logo"/>
                                </td>
                                <td style="vertical-align:middle; padding-left:14px;">
                                    <div style="font-size:16pt; font-weight:900; color:#0A182F; text-transform:uppercase; letter-spacing:-0.5px;">
                                        <span t-field="company.name"/>
                                    </div>
                                    <div style="font-size:8pt; color:#334155; font-weight:500;">
                                        <span t-if="company.city" t-field="company.city"/><t t-if="company.state_id">, <span t-field="company.state_id.code"/></t>
                                    </div>
                                </td>
                            </tr>
                        </table>
                        <div style="font-size:20pt; font-weight:bold; color:#0A182F; text-transform:uppercase; letter-spacing:1px; margin-top:14px;">Pallet Sheet</div>
                    </td>
                    <td style="width:45%; vertical-align:top; text-align:right;">
                        <img t-att-src="'/report/barcode/?type=QR&amp;value=%s&amp;width=220&amp;height=220' % doc.name" style="width:110px; height:110px; border:1px solid #cbd5e1; padding:4px;" alt="Pallet QR"/>
                        <div style="font-family:monospace; font-size:11pt; color:#0A182F; font-weight:bold; margin-top:6px;">
                            <t t-out="doc.name"/>
                        </div>
                    </td>
                </tr>
            </table>

            <!-- META PANEL: Pallet info grid -->
            <table style="width:100%; border-collapse:collapse; background-color:#f1f5f9; border-top:6px solid #0A182F; border-radius:4px; margin-bottom:18px;" cellspacing="0">
                <tr>
                    <td style="padding:12px 16px; vertical-align:top; border-right:1px solid #cbd5e1; width:33%;">
                        <div style="font-size:7.5pt; font-weight:bold; text-transform:uppercase; color:#334155; letter-spacing:0.5px;">Work Order</div>
                        <div style="font-family:monospace; font-size:13pt; font-weight:bold; color:#0A182F; margin-top:3px;">
                            <t t-out="(origin_mo.name if origin_mo else '—')"/>
                        </div>
                        <div style="font-size:7.5pt; font-weight:bold; text-transform:uppercase; color:#334155; letter-spacing:0.5px; margin-top:10px;">Customer</div>
                        <div style="font-size:10pt; color:#0A182F; margin-top:3px;">
                            <t t-out="(origin_customer or '—')"/>
                        </div>
                    </td>
                    <td style="padding:12px 16px; vertical-align:top; border-right:1px solid #cbd5e1; width:34%;">
                        <div style="font-size:7.5pt; font-weight:bold; text-transform:uppercase; color:#334155; letter-spacing:0.5px;">Product</div>
                        <div style="font-size:11pt; font-weight:bold; color:#0A182F; margin-top:3px;">
                            <t t-out="(origin_product.display_name if origin_product else '—')"/>
                        </div>
                        <div style="font-size:7.5pt; font-weight:bold; text-transform:uppercase; color:#334155; letter-spacing:0.5px; margin-top:10px;">Lots</div>
                        <div style="font-family:monospace; font-size:10pt; color:#0A182F; margin-top:3px;">
                            <t t-foreach="doc.msp_lot_ids" t-as="lot"><t t-out="lot.name"/><t t-if="not lot_last">, </t></t>
                            <t t-if="not doc.msp_lot_ids">—</t>
                        </div>
                    </td>
                    <td style="padding:12px 16px; vertical-align:top; width:33%;">
                        <div style="font-size:7.5pt; font-weight:bold; text-transform:uppercase; color:#334155; letter-spacing:0.5px;">Dimensions (L x W x H)</div>
                        <div style="font-family:monospace; font-size:13pt; font-weight:bold; color:#0A182F; margin-top:3px;">
                            <t t-out="doc.msp_dimensions_display or '—'"/>
                            <t t-if="doc.msp_length_in or doc.msp_width_in or doc.msp_height_in"> in</t>
                        </div>
                        <div style="font-size:7.5pt; font-weight:bold; text-transform:uppercase; color:#334155; letter-spacing:0.5px; margin-top:10px;">Finalized</div>
                        <div style="font-size:10pt; color:#0A182F; margin-top:3px;">
                            <t t-if="doc.msp_finalized_at" t-out="doc.msp_finalized_at.strftime('%m/%d/%Y %H:%M')"/>
                            <t t-if="not doc.msp_finalized_at">—</t>
                        </div>
                    </td>
                </tr>
            </table>

            <!-- UNIT-RANGE BAND -->
            <div t-if="doc.msp_unit_numbers_summary" style="background-color:#0A182F; color:white; padding:10px 16px; margin-bottom:14px; border-radius:3px;">
                <span style="font-size:8pt; text-transform:uppercase; letter-spacing:1px; font-weight:bold;">Units on this pallet:</span>
                <span style="font-family:monospace; font-size:13pt; font-weight:bold; margin-left:10px;"><t t-out="doc.msp_unit_numbers_summary"/></span>
            </div>

            <!-- CONTENTS TABLE — one row per move_line -->
            <table style="width:100%; border-collapse:collapse; margin-bottom:18px;" cellspacing="0">
                <thead>
                    <tr>
                        <th style="width:8%;  background-color:#0A182F; color:white; text-align:center; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">#</th>
                        <th style="width:30%; background-color:#0A182F; color:white; text-align:left;   padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Lot</th>
                        <th style="width:32%; background-color:#0A182F; color:white; text-align:left;   padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Product</th>
                        <th style="width:15%; background-color:#0A182F; color:white; text-align:center; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Quantity</th>
                        <th style="width:15%; background-color:#0A182F; color:white; text-align:center; padding:10px; font-size:8pt; text-transform:uppercase; letter-spacing:0.5px;">Picking</th>
                    </tr>
                </thead>
                <tbody>
                    <t t-set="row_idx" t-value="0"/>
                    <t t-foreach="doc.move_line_ids" t-as="ml">
                        <t t-set="row_idx" t-value="row_idx + 1"/>
                        <t t-set="bg" t-value="brand_zebra if (row_idx % 2 == 0) else '#ffffff'"/>
                        <tr t-att-style="'background-color:' + bg + ';'">
                            <td style="padding:8px 10px; border-bottom:1px solid #e2e8f0; text-align:center; font-family:monospace; font-weight:bold;"><t t-out="row_idx"/></td>
                            <td style="padding:8px 10px; border-bottom:1px solid #e2e8f0; font-family:monospace; font-size:10pt; font-weight:bold; color:#0A182F;">
                                <t t-out="(ml.lot_id.name if ml.lot_id else '') or '—'"/>
                            </td>
                            <td style="padding:8px 10px; border-bottom:1px solid #e2e8f0; font-size:9pt;">
                                <t t-out="ml.product_id.display_name or ''"/>
                            </td>
                            <td style="padding:8px 10px; border-bottom:1px solid #e2e8f0; text-align:center; font-family:monospace; font-size:10pt;">
                                <t t-out="'{:g}'.format(ml.quantity)"/> <t t-out="ml.product_uom_id.name or ''"/>
                            </td>
                            <td style="padding:8px 10px; border-bottom:1px solid #e2e8f0; text-align:center; font-family:monospace; font-size:9pt;">
                                <t t-out="(ml.picking_id.name if ml.picking_id else '') or '—'"/>
                            </td>
                        </tr>
                    </t>
                    <t t-if="not doc.move_line_ids">
                        <tr>
                            <td colspan="5" style="padding:14px; text-align:center; color:#94a3b8; font-style:italic;">
                                No move_lines yet. (Pallet may be finalized before partial-ship sync; the kiosk will retry the sync.)
                            </td>
                        </tr>
                    </t>
                </tbody>
            </table>

            <!-- TOTALS -->
            <table style="width:100%; border-collapse:collapse; background-color:#f1f5f9; border-top:4px solid #0A182F; border-radius:4px;" cellspacing="0">
                <tr>
                    <td style="width:50%; padding:14px 18px; vertical-align:middle;">
                        <span style="font-size:8pt; font-weight:bold; text-transform:uppercase; color:#334155; letter-spacing:0.5px;">Total Units</span>
                        <span style="font-size:18pt; font-weight:bold; color:#0A182F; margin-left:14px;"><t t-out="case_count"/></span>
                    </td>
                    <td style="width:50%; padding:14px 18px; vertical-align:middle; text-align:right;">
                        <span style="font-size:8pt; font-weight:bold; text-transform:uppercase; color:#334155; letter-spacing:0.5px;">Gross Weight</span>
                        <span style="font-size:18pt; font-weight:bold; color:#0A182F; margin-left:14px;">
                            <t t-out="'{:.1f}'.format(doc.msp_gross_weight_lb or 0)"/> lb
                        </span>
                    </td>
                </tr>
            </table>

            <div style="margin-top:18px; font-size:8pt; color:#94a3b8; font-style:italic; text-align:center;">
                Generated from MSP MES. Pallet ID encoded in QR (above).
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
    user = os.environ.get(prefix + "USER", "admin@mountainstatesplastics.com")
    api_key = os.environ.get(prefix + "API_KEY")
    if not all([url, db, api_key]):
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
    if existing:
        vid = existing[0]["id"]
        if not commit:
            print(f"  view {vid} ({REPORT_VIEW_KEY}): would UPDATE")
            return vid
        call("ir.ui.view", "write", [[vid], {"arch_db": QWEB_ARCH, "active": True}])
        print(f"  view {vid} ({REPORT_VIEW_KEY}): UPDATED")
        return vid
    if not commit:
        print(f"  view ({REPORT_VIEW_KEY}): would CREATE")
        return None
    vid = call("ir.ui.view", "create", [{
        "name": "MSP Pallet Sheet",
        "type": "qweb",
        "key": REPORT_VIEW_KEY,
        "arch_db": QWEB_ARCH,
        "active": True,
    }])
    print(f"  view {vid} ({REPORT_VIEW_KEY}): CREATED")
    return vid


def upsert_action(call, commit):
    existing = call("ir.actions.report", "search_read",
                    [[("report_name", "=", REPORT_VIEW_KEY)]],
                    {"fields": ["id", "name"]})
    sp_model_id = call("ir.model", "search", [[("model", "=", "stock.package")]])
    if not sp_model_id:
        sys.exit("stock.package model not found (install msp_pallet first)")
    sp_model_id = sp_model_id[0]
    pf = call("report.paperformat", "search",
              [[("name", "=", "US Letter")]])
    pf_id = pf[0] if pf else False
    vals = {
        "name": REPORT_ACTION_NAME,
        "model": "stock.package",
        "report_type": "qweb-pdf",
        "report_name": REPORT_VIEW_KEY,
        "report_file": REPORT_VIEW_KEY,
        "binding_model_id": sp_model_id,
        "binding_type": "report",
        # Filename: package name (e.g. PLT-00081) is filesystem-safe enough
        # but our test pallets contain slashes, so normalize the same way the
        # pick sheet does.
        "print_report_name": "(object.name or 'PalletSheet').replace('/', '-')",
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
        print("\nDone. The report appears under Print -> Pallet Sheet — MSP")
        print("on any stock.package form.")


if __name__ == "__main__":
    main()
