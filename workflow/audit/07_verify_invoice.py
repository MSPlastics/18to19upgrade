"""Phase 8 - create invoice from the SO and verify the draft.

Calls sale.order._create_invoices to generate an invoice (regular - delivered qty),
then verifies the draft has correct lines, UoM, and totals.

Does NOT post the invoice (leaves it as draft).
"""
from __future__ import annotations
import datetime as _dt
from pathlib import Path
import _common as C

s = C.staging
state = C.state

SO_ID = state["so_id"]
SO_NAME = state["so_name"]
PROD_ID = state["target_product_id"]
PROD_NAME = state["target_product_name"]
QTY = state["target_qty"]
UOM_NAME = state["target_uom_name"]
UOM_ID = state["target_uom_id"]
PARTNER_ID = state["target_partner_id"]
REPORT = Path(state["report_path"])

# Look at SO state first
so = s.read_one("sale.order", SO_ID, ["name","state","invoice_status","amount_total","amount_untaxed","invoice_ids","order_line"])
C.log(f"=== Phase 8: invoice for {SO_NAME} ===")
C.log(f"  SO state={so['state']} invoice_status={so['invoice_status']} amount_total={so['amount_total']} invoice_ids={so['invoice_ids']}")

# Read SO lines for price reference
sol = s.call("sale.order.line","read",[so["order_line"]],
    {"fields":["id","product_id","product_uom_qty","qty_delivered","qty_invoiced",
               "product_uom_id","price_unit","price_subtotal","price_total"]})
for l in sol:
    C.log(f"  SO line: product={l['product_id']}, qty_ordered={l['product_uom_qty']}, qty_delivered={l['qty_delivered']}, qty_invoiced={l['qty_invoiced']}, uom={l['product_uom_id']}, price={l['price_unit']}")

# Create invoice (idempotent: skip if already exists)
invoice_ids = so["invoice_ids"]
if invoice_ids:
    C.log(f"  invoice already exists: {invoice_ids}")
else:
    C.log("  calling sale.order._create_invoices(...)")
    try:
        # _create_invoices returns account.move recordset
        result = s.call("sale.order", "_create_invoices", [[SO_ID]], {"final": True})
        C.log(f"    result: {result}")
    except Exception as e:
        C.log(f"    _create_invoices failed (private?): {e}")
        # Try action_create_invoice (the wizard public path)
        try:
            wiz_id = s.call("sale.advance.payment.inv", "create",
                            [{"advance_payment_method": "delivered", "sale_order_ids": [(6, 0, [SO_ID])]}])
            C.log(f"    created sale.advance.payment.inv id={wiz_id}, calling create_invoices...")
            s.call_void("sale.advance.payment.inv", "create_invoices", [[wiz_id]])
        except Exception as e2:
            raise SystemExit(f"both invoice creation paths failed: {e} ; {e2}")

# Re-read SO to get invoice_ids
so2 = s.read_one("sale.order", SO_ID, ["invoice_ids","invoice_status","amount_total"])
invoice_ids = so2["invoice_ids"]
C.log(f"  after create: invoice_ids={invoice_ids}, invoice_status={so2['invoice_status']}")

if not invoice_ids:
    raise SystemExit("no invoices created")

# Read invoice
inv = s.read_one("account.move", invoice_ids[0],
    ["name","state","move_type","invoice_date","amount_total","amount_untaxed",
     "partner_id","invoice_line_ids","invoice_origin"])
C.log(f"  invoice {inv['name']} state={inv['state']} move_type={inv['move_type']} amount={inv['amount_total']} partner={inv['partner_id']} origin={inv['invoice_origin']}")

inv_lines = s.call("account.move.line","read",[inv["invoice_line_ids"]],
    {"fields":["id","product_id","quantity","product_uom_id","price_unit","price_subtotal","display_type"]})
# Filter out section/note lines
prod_lines = [l for l in inv_lines if l["display_type"] in (False, None, "product")]
C.log(f"  {len(inv_lines)} invoice line(s) ({len(prod_lines)} product line(s)):")
for l in prod_lines:
    C.log(f"    product={l['product_id']} qty={l['quantity']} uom={l['product_uom_id']} price={l['price_unit']} subtotal={l['price_subtotal']}")

checks = []
checks.append(("Invoice created", bool(invoice_ids), f"got {len(invoice_ids)}"))
checks.append(("Invoice state == draft", inv["state"] == "draft", f"actual {inv['state']}"))
checks.append(("Invoice move_type == out_invoice", inv["move_type"] == "out_invoice", f"actual {inv['move_type']}"))
# Invoice partner may be a child contact of the customer (e.g. AP/billing contact), not the parent.
inv_partner = s.read_one("res.partner", inv["partner_id"][0], ["id","name","parent_id","commercial_partner_id"]) if inv["partner_id"] else None
inv_commercial = inv_partner["commercial_partner_id"][0] if inv_partner and inv_partner.get("commercial_partner_id") else None
checks.append((f"Invoice partner is or is a contact under customer id {PARTNER_ID}",
               inv["partner_id"] and (inv["partner_id"][0] == PARTNER_ID or inv_commercial == PARTNER_ID),
               f"actual partner={inv['partner_id']}, commercial={inv_commercial}"))
checks.append((f"Invoice origin == {SO_NAME}", inv["invoice_origin"] == SO_NAME, f"actual {inv['invoice_origin']}"))
checks.append(("Exactly 1 product line", len(prod_lines) == 1, f"got {len(prod_lines)}"))
if prod_lines:
    pl = prod_lines[0]
    checks.append((f"Line product == {PROD_NAME} (id {PROD_ID})",
                   pl["product_id"] and pl["product_id"][0] == PROD_ID,
                   f"actual {pl['product_id']}"))
    checks.append((f"Line qty == {QTY}", abs(pl["quantity"] - QTY) < 0.001, f"actual {pl['quantity']}"))
    checks.append((f"Line UoM == {UOM_NAME}",
                   pl["product_uom_id"] and pl["product_uom_id"][0] == UOM_ID,
                   f"actual {pl['product_uom_id']}"))
    # Price check vs SO line
    if sol:
        expected_price = sol[0]["price_unit"]
        checks.append((f"Line price_unit matches SO line price ({expected_price})",
                       abs(pl["price_unit"] - expected_price) < 0.001,
                       f"actual {pl['price_unit']}"))

ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
all_pass = all(ok for _, ok, _ in checks)
status = "PASS" if all_pass else "FAIL"

lines = [
    f"\n### {ts} - Phase 8: Invoice draft - **{status}**",
    "",
    f"- SO {SO_NAME}: invoice_status=`{so2['invoice_status']}`, amount_total={so['amount_total']}",
    f"- Invoice **{inv['name']}** state=`{inv['state']}`, type=`{inv['move_type']}`, partner=`{inv['partner_id'][1] if inv['partner_id'] else '-'}`, origin=`{inv['invoice_origin']}`, total={inv['amount_total']} (untaxed={inv['amount_untaxed']})",
    f"- {len(prod_lines)} product line(s):",
]
for l in prod_lines:
    lines.append(f"  - product=`{l['product_id'][1] if l['product_id'] else '-'}` qty=**{l['quantity']} {l['product_uom_id'][1] if l['product_uom_id'] else '-'}** price={l['price_unit']} subtotal={l['price_subtotal']}")
lines.append("")
lines.append("Note: invoice left in draft state per audit policy. Posting requires explicit instruction.")
lines.append("")
lines.append("Checks:")
for label, ok, detail in checks:
    mark = "OK" if ok else "FAIL"
    lines.append(f"- [{mark}] {label} - {detail}")

text = REPORT.read_text(encoding="utf-8")
text = text.replace("| 8 - Invoice | _pending_ | | |",
                    f"| 8 - Invoice | {'PASS' if all_pass else 'FAIL'} | {'' if all_pass else 'see Phase 8 block'} | {'' if all_pass else 'blocker'} |")
text += "\n".join(lines) + "\n"
REPORT.write_text(text, encoding="utf-8")
state["invoice_id"] = invoice_ids[0]
state["invoice_name"] = inv["name"]
C.log(f"appended Phase 8 block ({status})")
print(f"\n  {status}")
