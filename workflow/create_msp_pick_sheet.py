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

            <!-- UNIFIED PICK CHECKLIST — every pallet rendered with the
                 contents-breakdown style (product x cases | lot per move_line,
                 stacked when there are multiple). Pure pallets show one line
                 in the contents cell, mixed pallets show several. Sorted by
                 trailing pallet number ASC so the picker reads 1->N top-down.
                 Per-product summary lives at the bottom of the report. -->
            <t t-set="palletized" t-value="doc.move_line_ids.filtered('package_id')"/>
            <t t-set="loose" t-value="doc.move_line_ids.filtered(lambda ml: not ml.package_id)"/>
            <t t-set="all_pkgs_sorted" t-value="palletized.package_id.sorted(key=lambda p: int(p.name.rsplit('-PAL-', 1)[-1]) if (p.name and '-PAL-' in p.name and p.name.rsplit('-PAL-', 1)[-1].isdigit()) else 99999)"/>
            <t t-set="total_pallets" t-value="len(all_pkgs_sorted) + len(loose)"/>

            <div style="font-size:10pt; font-weight:bold; color:#0A182F; text-transform:uppercase; letter-spacing:1px; margin:8px 0 6px 0;">
                Pick Checklist (<t t-out="total_pallets"/> rows)
            </div>

            <table style="width:100%; border-collapse:collapse; margin:0 0 6px 0;" cellspacing="0">
                <thead>
                    <tr>
                        <th style="width:5%;  background:#0A182F; color:white; text-align:center; padding:6px; font-size:7pt; text-transform:uppercase; letter-spacing:0.5px;">Pick</th>
                        <th style="width:18%; background:#0A182F; color:white; text-align:left;   padding:6px; font-size:7pt; text-transform:uppercase; letter-spacing:0.5px;">Pallet ID</th>
                        <th style="width:42%; background:#0A182F; color:white; text-align:left;   padding:6px; font-size:7pt; text-transform:uppercase; letter-spacing:0.5px;">Contents (Product x qty | Lot)</th>
                        <th style="width:10%; background:#0A182F; color:white; text-align:center; padding:6px; font-size:7pt; text-transform:uppercase; letter-spacing:0.5px;">Units</th>
                        <th style="width:12%; background:#0A182F; color:white; text-align:center; padding:6px; font-size:7pt; text-transform:uppercase; letter-spacing:0.5px;">Dims (in)</th>
                        <th style="width:13%; background:#0A182F; color:white; text-align:center; padding:6px; font-size:7pt; text-transform:uppercase; letter-spacing:0.5px;">Weight (lb)</th>
                    </tr>
                </thead>
                <tbody>
                    <t t-set="row_idx" t-value="0"/>

                    <!-- one row per pallet -->
                    <t t-foreach="all_pkgs_sorted" t-as="pkg">
                        <t t-set="pkg_lines" t-value="palletized.filtered(lambda ml: ml.package_id.id == pkg.id).sorted(key=lambda ml: -ml.quantity)"/>
                        <!-- Per-pallet Units total: if all lines on this pallet share the
                             SAME product.packaging (e.g. Roll), display the
                             packaging-converted total + packaging name. Otherwise fall
                             back to stock-UoM total (and only label it when all stock
                             UoMs match too — rare mixed-product edge). -->
                        <t t-set="pkg_pkg_ids" t-value="set([ml.move_id.product_packaging_id.id for ml in pkg_lines])"/>
                        <t t-set="all_share_packaging" t-value="len(pkg_pkg_ids) == 1 and pkg_pkg_ids != {False}"/>
                        <t t-if="all_share_packaging">
                            <t t-set="the_pkg" t-value="pkg_lines[0].move_id.product_packaging_id"/>
                            <t t-set="case_count" t-value="sum((ml.quantity / the_pkg.qty) for ml in pkg_lines if the_pkg.qty)"/>
                            <t t-set="pkg_uom_label" t-value="the_pkg.name or ''"/>
                        </t>
                        <t t-else="">
                            <t t-set="case_count" t-value="sum(pkg_lines.mapped('quantity'))"/>
                            <t t-set="stock_uoms" t-value="set(pkg_lines.mapped('product_uom_id.name'))"/>
                            <t t-set="pkg_uom_label" t-value="next(iter(stock_uoms)) if len(stock_uoms) == 1 else ''"/>
                        </t>
                        <t t-set="dims" t-value="(pkg.msp_dimensions_display if 'msp_dimensions_display' in pkg._fields else '') or ''"/>
                        <t t-set="gross_lb" t-value="(pkg.msp_gross_weight_lb if 'msp_gross_weight_lb' in pkg._fields else 0) or 0"/>
                        <t t-set="display_id" t-value="(pkg.name or '').replace('WH/MO/', '').replace('WH/', '')"/>
                        <t t-set="row_idx" t-value="row_idx + 1"/>
                        <t t-set="bg" t-value="brand_zebra if (row_idx % 2 == 0) else '#ffffff'"/>
                        <tr t-att-style="'background-color:' + bg + ';'">
                            <td style="padding:6px 8px; border-bottom:1px solid #e2e8f0; text-align:center; vertical-align:middle;">
                                <span style="display:inline-block; width:14px; height:14px; border:2px solid #0A182F; border-radius:2px; background:#fff;"></span>
                            </td>
                            <td style="padding:6px 8px; border-bottom:1px solid #e2e8f0; vertical-align:middle; font-family:monospace; font-size:10pt; font-weight:bold; color:#0A182F;">
                                <t t-out="display_id"/>
                            </td>
                            <td style="padding:6px 8px; border-bottom:1px solid #e2e8f0; vertical-align:middle;">
                                <t t-foreach="pkg_lines" t-as="ml">
                                    <!-- Per-line: prefer packaging-converted (Roll/Case)
                                         if defined; fall back to raw stock UoM. -->
                                    <t t-set="line_pkg" t-value="ml.move_id.product_packaging_id"/>
                                    <t t-set="line_qty" t-value="(ml.quantity / line_pkg.qty) if (line_pkg and line_pkg.qty) else ml.quantity"/>
                                    <t t-set="line_uom" t-value="line_pkg.name if line_pkg else (ml.product_uom_id.name or '')"/>
                                    <div style="font-size:9pt; line-height:1.3;">
                                        <span style="font-family:monospace; font-weight:bold; color:#0A182F;"><t t-out="ml.product_id.name or '?'"/></span>
                                        <span style="font-family:monospace; color:#0A182F;"> x <t t-out="'{:g}'.format(line_qty)"/> <t t-out="line_uom"/></span>
                                        <span style="font-family:monospace; font-size:8pt; color:#6d28d9; margin-left:6px;">lot <t t-out="ml.lot_id.name or '-'"/></span>
                                    </div>
                                </t>
                            </td>
                            <td style="padding:6px 8px; border-bottom:1px solid #e2e8f0; text-align:center; vertical-align:middle; font-family:monospace; font-size:11pt; font-weight:bold; color:#0A182F;">
                                <t t-out="'{:g}'.format(case_count)"/> <t t-out="pkg_uom_label"/>
                            </td>
                            <td style="padding:6px 8px; border-bottom:1px solid #e2e8f0; text-align:center; vertical-align:middle; font-family:monospace; font-size:9pt; color:#334155;">
                                <t t-if="dims" t-out="dims"/>
                                <t t-if="not dims">-</t>
                            </td>
                            <td style="padding:6px 8px; border-bottom:1px solid #e2e8f0; text-align:center; vertical-align:middle; font-family:monospace; font-size:9pt; color:#334155;">
                                <t t-if="gross_lb" t-out="'{:.1f}'.format(gross_lb)"/>
                                <t t-if="not gross_lb">-</t>
                            </td>
                        </tr>
                    </t>

                    <!-- loose (unpalletized) lines, inline at the end with NO PALLET label -->
                    <t t-foreach="loose" t-as="ml">
                        <t t-set="row_idx" t-value="row_idx + 1"/>
                        <t t-set="bg" t-value="brand_zebra if (row_idx % 2 == 0) else '#ffffff'"/>
                        <t t-set="line_pkg" t-value="ml.move_id.product_packaging_id"/>
                        <t t-set="line_qty" t-value="(ml.quantity / line_pkg.qty) if (line_pkg and line_pkg.qty) else ml.quantity"/>
                        <t t-set="line_uom" t-value="line_pkg.name if line_pkg else (ml.product_uom_id.name or '')"/>
                        <tr t-att-style="'background-color:' + bg + ';'">
                            <td style="padding:6px 8px; border-bottom:1px solid #e2e8f0; text-align:center; vertical-align:middle;">
                                <span style="display:inline-block; width:14px; height:14px; border:2px solid #0A182F; border-radius:2px; background:#fff;"></span>
                            </td>
                            <td style="padding:6px 8px; border-bottom:1px solid #e2e8f0; vertical-align:middle; font-family:monospace; font-size:9pt; font-style:italic; color:#92400e;">
                                NO PALLET
                            </td>
                            <td style="padding:6px 8px; border-bottom:1px solid #e2e8f0; vertical-align:middle;">
                                <div style="font-size:9pt; line-height:1.3;">
                                    <span style="font-family:monospace; font-weight:bold; color:#0A182F;"><t t-out="ml.product_id.name or '?'"/></span>
                                    <span style="font-family:monospace; color:#0A182F;"> x <t t-out="'{:g}'.format(line_qty)"/> <t t-out="line_uom"/></span>
                                    <span style="font-family:monospace; font-size:8pt; color:#6d28d9; margin-left:6px;">lot <t t-out="ml.lot_id.name or '-'"/></span>
                                </div>
                            </td>
                            <td style="padding:6px 8px; border-bottom:1px solid #e2e8f0; text-align:center; vertical-align:middle; font-family:monospace; font-size:11pt; font-weight:bold; color:#0A182F;">
                                <t t-out="'{:g}'.format(line_qty)"/> <t t-out="line_uom"/>
                            </td>
                            <td style="padding:6px 8px; border-bottom:1px solid #e2e8f0; text-align:center; vertical-align:middle; color:#94a3b8;">-</td>
                            <td style="padding:6px 8px; border-bottom:1px solid #e2e8f0; text-align:center; vertical-align:middle; color:#94a3b8;">-</td>
                        </tr>
                    </t>
                </tbody>
            </table>

            <!-- GRAND TOTAL — pallet count uses distinct packages so mixed
                 pallets aren't double-counted; units = full move_line sum
                 with shared UoM label when all lines on the picking share
                 one UoM, otherwise just 'units'; weight is per-pallet
                 summed once. -->
            <t t-set="grand_uoms" t-value="set(doc.move_line_ids.mapped('product_uom_id.name'))"/>
            <t t-set="grand_uom_label" t-value="next(iter(grand_uoms)) if len(grand_uoms) == 1 else 'units'"/>
            <table style="width:100%; border-collapse:collapse; background:#0A182F; color:white; margin-top:10px;" cellspacing="0">
                <tr>
                    <td style="padding:10px 14px; font-size:8pt; font-weight:bold; text-transform:uppercase; letter-spacing:0.5px;">Grand Total</td>
                    <td style="padding:10px 14px; font-family:monospace; font-size:13pt; font-weight:bold; text-align:right;">
                        <t t-out="len(palletized.package_id)"/> pallets
                        | <t t-out="'{:g}'.format(sum(doc.move_line_ids.mapped('quantity')))"/> <t t-out="grand_uom_label"/>
                        | <t t-out="'{:.1f}'.format(sum((p.msp_gross_weight_lb or 0) for p in palletized.package_id if 'msp_gross_weight_lb' in p._fields))"/> lb
                    </td>
                </tr>
            </table>

            <!-- ORDER SUMMARY (BOTTOM) — per-product/lot recap. Each row
                 aggregates across all pallets (pure or mixed) plus any loose
                 lines with the same product/lot. Picker uses this for a final
                 cross-check that the full order is accounted for. Order
                 follows the Pick Checklist above (first-appearance per
                 sorted pallet) so summary row N matches the first pallet
                 of product N as the picker sees it. -->
            <t t-set="all_lines" t-value="doc.move_line_ids"/>
            <t t-set="summary_keys" t-value="[]"/>
            <t t-foreach="all_pkgs_sorted" t-as="_pkg">
                <t t-foreach="palletized.filtered(lambda ml: ml.package_id.id == _pkg.id).sorted(key=lambda ml: -ml.quantity)" t-as="_ml">
                    <t t-set="_k" t-value="(_ml.product_id.id, _ml.lot_id.id or 0)"/>
                    <t t-if="_k not in summary_keys">
                        <t t-set="summary_keys" t-value="summary_keys + [_k]"/>
                    </t>
                </t>
            </t>
            <t t-foreach="loose" t-as="_ml">
                <t t-set="_k" t-value="(_ml.product_id.id, _ml.lot_id.id or 0)"/>
                <t t-if="_k not in summary_keys">
                    <t t-set="summary_keys" t-value="summary_keys + [_k]"/>
                </t>
            </t>
            <t t-if="summary_keys">
                <div style="font-size:10pt; font-weight:bold; color:#0A182F; text-transform:uppercase; letter-spacing:1px; margin:25px 0 6px 0;">
                    Order Summary
                </div>
                <table style="width:100%; border-collapse:collapse; margin-bottom:6px;" cellspacing="0">
                    <thead>
                        <tr>
                            <th style="width:10%; background:#0A182F; color:white; text-align:left;   padding:7px; font-size:7pt; text-transform:uppercase; letter-spacing:0.5px;">MSP PN</th>
                            <th style="width:48%; background:#0A182F; color:white; text-align:left;   padding:7px; font-size:7pt; text-transform:uppercase; letter-spacing:0.5px;">Description</th>
                            <th style="width:18%; background:#0A182F; color:white; text-align:left;   padding:7px; font-size:7pt; text-transform:uppercase; letter-spacing:0.5px;">Lot</th>
                            <th style="width:12%; background:#0A182F; color:white; text-align:center; padding:7px; font-size:7pt; text-transform:uppercase; letter-spacing:0.5px;">Total Units</th>
                            <th style="width:12%; background:#0A182F; color:white; text-align:center; padding:7px; font-size:7pt; text-transform:uppercase; letter-spacing:0.5px;">On Pallets</th>
                        </tr>
                    </thead>
                    <tbody>
                        <t t-set="s_idx" t-value="0"/>
                        <t t-foreach="summary_keys" t-as="sk">
                            <t t-set="s_lines" t-value="all_lines.filtered(lambda ml: ml.product_id.id == sk[0] and (ml.lot_id.id or 0) == sk[1])"/>
                            <t t-set="s_first" t-value="s_lines[0]"/>
                            <t t-set="s_prod" t-value="s_first.product_id"/>
                            <t t-set="s_lot" t-value="s_first.lot_id"/>
                            <t t-set="s_move" t-value="s_first.move_id"/>
                            <t t-set="s_desc_src" t-value="(s_move.sale_line_id.name if s_move.sale_line_id else False) or s_prod.display_name or ''"/>
                            <t t-set="s_desc_lines" t-value="s_desc_src.splitlines() or ['']"/>
                            <!-- Packaging conversion: all lines in this group share
                                 the same product, so same packaging if defined. -->
                            <t t-set="s_pkg_pkg" t-value="s_first.move_id.product_packaging_id"/>
                            <t t-if="s_pkg_pkg and s_pkg_pkg.qty">
                                <t t-set="s_total_cases" t-value="sum((ml.quantity / s_pkg_pkg.qty) for ml in s_lines)"/>
                                <t t-set="s_uom" t-value="s_pkg_pkg.name or ''"/>
                            </t>
                            <t t-else="">
                                <t t-set="s_total_cases" t-value="sum(s_lines.mapped('quantity'))"/>
                                <t t-set="s_uom" t-value="s_first.product_uom_id.name or ''"/>
                            </t>
                            <t t-set="s_pallet_count" t-value="len(s_lines.filtered('package_id').package_id)"/>
                            <t t-set="s_idx" t-value="s_idx + 1"/>
                            <t t-set="bg" t-value="brand_zebra if (s_idx % 2 == 0) else '#ffffff'"/>
                            <tr t-att-style="'background-color:' + bg + ';'">
                                <td style="padding:8px 10px; border-bottom:1px solid #e2e8f0; vertical-align:top; font-family:monospace; font-size:10pt; font-weight:bold; color:#0A182F;">
                                    <t t-out="s_prod.name or ''"/>
                                </td>
                                <td style="padding:8px 10px; border-bottom:1px solid #e2e8f0; vertical-align:top;">
                                    <div style="font-weight:bold; font-size:9.5pt; color:#0A182F;"><t t-out="s_desc_lines[0]"/></div>
                                    <t t-if="len(s_desc_lines) > 1">
                                        <div style="color:#334155; font-size:8pt; margin-top:2px; line-height:1.3;">
                                            <t t-foreach="s_desc_lines[1:]" t-as="ln"><t t-out="ln"/><br/></t>
                                        </div>
                                    </t>
                                </td>
                                <td style="padding:8px 10px; border-bottom:1px solid #e2e8f0; vertical-align:top; font-family:monospace; font-size:10pt; font-weight:bold; color:#0A182F;">
                                    <t t-out="s_lot.name if s_lot else '-'"/>
                                </td>
                                <td style="padding:8px 10px; border-bottom:1px solid #e2e8f0; text-align:center; vertical-align:middle; font-family:monospace; font-size:13pt; font-weight:bold; color:#0A182F;">
                                    <t t-out="'{:g}'.format(s_total_cases)"/> <span style="font-size:9pt; font-weight:normal; color:#334155;"><t t-out="s_uom"/></span>
                                </td>
                                <td style="padding:8px 10px; border-bottom:1px solid #e2e8f0; text-align:center; vertical-align:middle; font-family:monospace; font-size:11pt; color:#334155;">
                                    <t t-out="s_pallet_count"/>
                                </td>
                            </tr>
                        </t>
                    </tbody>
                </table>
            </t>

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
