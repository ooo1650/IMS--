"""Tax documents: full tax invoice (Rule 17), abbreviated invoice (Rule 18),
credit and debit notes (payment.md Step 2).

``*_context`` builds one printer-agnostic dict; ``render_html`` (browser print)
and ``render_pdf`` (export, listing evidence) both consume it. Line amounts are
shown before VAT; they are allocated so they sum exactly to the stored subtotal.
"""
import re
from fractions import Fraction
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from sales.pricing import allocate, rate_fraction

from . import bs
from .words import amount_in_words

TITLES = {
    "full": ("कर बीजक", "TAX INVOICE"),
    "abbreviated": ("संक्षिप्त कर बीजक", "ABBREVIATED TAX INVOICE"),
    "credit_note": ("क्रेडिट नोट", "CREDIT NOTE"),
    "debit_note": ("डेबिट नोट", "DEBIT NOTE"),
}
METHOD_LABELS = {"cash": "Cash", "fonepay": "FonePay QR", "card": "Card", "wallet": "Wallet", "bank": "Bank"}


def _money(p):
    return f"{p / 100:,.2f}"


def _seller(branch):
    org = branch.org
    return {"name": org.name, "name_np": org.name_np, "pan": org.pan, "address": branch.address or org.address,
            "phone": branch.phone or org.phone, "branch": f"{branch.name} ({branch.code})"}


def _stamp(print_log):
    if print_log is None:
        return {"label": "PREVIEW — NOT A TAX INVOICE", "copy_no": 0}
    if print_log.kind == "copy":
        return {"label": f"COPY OF ORIGINAL — Copy #{print_log.copy_no}", "copy_no": print_log.copy_no}
    if print_log.kind == "delivery":
        return {"label": "DELIVERY COPY", "copy_no": 0}
    return {"label": "", "copy_no": 0}


def _dt(dt, tz):
    if dt is None:
        return {"ad": "", "bs": "", "time": ""}
    local = timezone.localtime(dt, tz)
    return {"ad": local.strftime("%Y-%m-%d"), "bs": bs.bs_str(local.date()), "time": local.strftime("%H:%M")}


def invoice_context(sale, print_log=None):
    lines = list(sale.lines.all())
    tz = sale.branch.tz
    # Net-of-tax line amounts (gross before discounts), allocated to sum to subtotal.
    exact = []
    for l in lines:
        v, e, lv = (rate_fraction(l.tax_rate) / 100, rate_fraction(l.excise_rate) / 100,
                    rate_fraction(l.levy_rate) / 100 if l.levy_type else Fraction(0))
        denom = (1 + e) * (1 + v) + lv if l.tax_inclusive else Fraction(1)
        exact.append(Fraction(l.unit_price_paisa * l.qty) / denom)
    amounts = allocate(sale.subtotal_paisa, exact) if exact else []
    rows = []
    for i, (l, amt) in enumerate(zip(lines, amounts), start=1):
        rows.append({
            "sno": i, "hs_code": l.hs_code, "description": l.product_name, "qty": l.qty,
            "rate": _money(round(amt / l.qty)) if l.qty else "0.00", "amount": _money(amt),
            "vat_rate": f"{l.tax_rate.normalize()}%" if l.tax_rate else ("Exempt" if l.tax_kind == "exempt" else "0%"),
        })
    payment = sale.payments.filter(status="success").first()
    taxable_value = sale.subtotal_paisa - sale.discount_total_paisa
    vat_rates = sorted({f"{l.tax_rate.normalize()}" for l in lines if l.tax_rate > 0})
    kind = sale.invoice_type or "abbreviated"
    return {
        "doc": "invoice", "kind": kind, "title_np": TITLES[kind][0], "title_en": TITLES[kind][1],
        "number": sale.receipt_number, "fiscal_year": sale.fiscal_year.replace("-", "/"),
        "seller": _seller(sale.branch),
        "buyer": {"name": sale.buyer_name or ("Walk-in customer" if kind == "abbreviated" else ""),
                  "address": sale.buyer_address, "pan": sale.buyer_pan},
        "transaction": _dt(sale.transaction_at or sale.paid_at, tz),
        "issued": _dt(sale.issued_at or sale.paid_at, tz),
        "register": sale.register.code if sale.register_id else "",
        "cashier": sale.cashier.get_full_name() or sale.cashier.username,
        "payment_method": METHOD_LABELS.get(sale.payment_method or (payment.method if payment else ""), ""),
        "payment_ref": payment.ref if payment else "",
        "tendered": _money(payment.tendered_paisa) if payment and payment.method == "cash" else "",
        "change": _money(payment.change_paisa) if payment and payment.change_paisa else "",
        "lines": rows,
        "subtotal": _money(sale.subtotal_paisa),
        "discount": _money(sale.discount_total_paisa),
        "taxable_value": _money(taxable_value),
        "taxable_sales_vat": _money(sale.taxable_sales_vat_paisa),
        "exempt_sales": _money(sale.tax_exempted_sales_paisa + sale.zero_rated_sales_paisa),
        "vat_label": f"VAT {', '.join(vat_rates)}%" if vat_rates else "VAT",
        "vat": _money(sale.tax_total_paisa),
        "excise": _money(sale.excise_total_paisa) if sale.excise_total_paisa else "",
        "hst": _money(sale.hst_total_paisa) if sale.hst_total_paisa else "",
        "esf": _money(sale.esf_total_paisa) if sale.esf_total_paisa else "",
        "digital_discount": _money(sale.digital_vat_discount_paisa) if sale.digital_vat_discount_paisa else "",
        "round_adjust": _money(sale.round_adjust_paisa) if sale.round_adjust_paisa else "",
        "total": _money(sale.total_paisa),
        "total_paisa": sale.total_paisa,
        "in_words": amount_in_words(sale.total_paisa),
        "cancelled": sale.status == "voided" and bool(sale.receipt_number),
        "cancel_reason": sale.void_reason,
        "cancelled_at": _dt(sale.voided_at, tz),
        "offline": sale.offline, "provisional_ref": sale.provisional_ref,
        "stamp": _stamp(print_log),
        "open_drawer": bool(payment and payment.method == "cash") and (print_log is None or print_log.kind == "original"),
        "delivery_copy_available": sale.total_paisa > _delivery_limit(sale.branch.org),
    }


def _delivery_limit(org):
    from .models import get_settings

    return get_settings(org).delivery_copy_limit_paisa


def credit_note_context(cn, print_log=None):
    sale = cn.original_sale
    tz = cn.branch.tz
    rows = [{"sno": i, "description": l.original_sale_line.product_name, "qty": l.qty,
             "amount": _money(l.line_total_paisa - l.tax_paisa - l.excise_paisa - l.levy_paisa),
             "vat": _money(l.tax_paisa)} for i, l in enumerate(cn.lines.select_related("original_sale_line"), 1)]
    return {
        "doc": "credit_note", "kind": "credit_note", "title_np": TITLES["credit_note"][0],
        "title_en": TITLES["credit_note"][1], "number": cn.number, "fiscal_year": cn.fiscal_year.replace("-", "/"),
        "seller": _seller(cn.branch),
        "buyer": {"name": sale.buyer_name, "address": sale.buyer_address, "pan": sale.buyer_pan},
        "issued": _dt(cn.at, tz),
        "original": {"number": sale.receipt_number, "date_bs": sale.issued_bs,
                     "date_ad": _dt(sale.issued_at, tz)["ad"]},
        "reason": cn.reason, "lines": rows,
        "taxable_value": _money(cn.taxable_paisa), "vat": _money(cn.tax_paisa),
        "excise": _money(cn.excise_paisa) if cn.excise_paisa else "",
        "levy": _money(cn.levy_paisa) if cn.levy_paisa else "",
        "total": _money(cn.total_paisa), "in_words": amount_in_words(cn.total_paisa),
        "refund_method": METHOD_LABELS.get(cn.refund_method, cn.refund_method),
        "stamp": _stamp(print_log),
    }


def debit_note_context(dn, print_log=None):
    sale = dn.original_sale
    tz = dn.branch.tz
    return {
        "doc": "debit_note", "kind": "debit_note", "title_np": TITLES["debit_note"][0],
        "title_en": TITLES["debit_note"][1], "number": dn.number, "fiscal_year": dn.fiscal_year.replace("-", "/"),
        "seller": _seller(dn.branch),
        "buyer": {"name": sale.buyer_name, "address": sale.buyer_address, "pan": sale.buyer_pan},
        "issued": _dt(dn.at, tz),
        "original": {"number": sale.receipt_number, "date_bs": sale.issued_bs,
                     "date_ad": _dt(sale.issued_at, tz)["ad"]},
        "reason": dn.reason, "lines": [{"sno": 1, "description": dn.reason, "qty": 1,
                                        "amount": _money(dn.taxable_paisa), "vat": _money(dn.tax_paisa)}],
        "taxable_value": _money(dn.taxable_paisa), "vat": _money(dn.tax_paisa),
        "vat_label": f"VAT {dn.tax_rate.normalize()}%", "total": _money(dn.total_paisa),
        "in_words": amount_in_words(dn.total_paisa), "stamp": _stamp(print_log),
    }


def render_html(ctx) -> str:
    return render_to_string("compliance/document.html", {"d": ctx})


# --- PDF ------------------------------------------------------------------------

FONT_DIR = Path(settings.BASE_DIR) / "fonts"
_fonts_ready = False


def _register_fonts():
    global _fonts_ready
    if _fonts_ready:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    pdfmetrics.registerFont(TTFont("NotoDeva", str(FONT_DIR / "NotoSansDevanagari-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("NotoDeva-Bold", str(FONT_DIR / "NotoSansDevanagari-Bold.ttf")))
    _fonts_ready = True


_DEVA = re.compile(r"([\u0900-\u097F\u200c\u200d]+(?:[\s.,/()-]+[\u0900-\u097F\u200c\u200d]+)*)")


def mixed(text, bold=False) -> str:
    """Paragraph markup: Latin in Helvetica, Devanagari runs in Noto Sans
    Devanagari (which has no Latin glyphs)."""
    from xml.sax.saxutils import escape

    face = "NotoDeva-Bold" if bold else "NotoDeva"
    return _DEVA.sub(lambda m: f'<font name="{face}">{m.group(1)}</font>', escape(str(text)))


def render_pdf(ctx) -> bytes:
    """A4 PDF of a tax document (uses Noto Sans Devanagari for the Nepali title)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    _register_fonts()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm,
                            bottomMargin=12 * mm, title=f"{ctx['title_en']} {ctx['number']}")
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=9, leading=12)
    bold = ParagraphStyle("bb", parent=body, fontName="Helvetica-Bold")
    center = ParagraphStyle("c", parent=bold, alignment=1, fontSize=14, leading=18)
    s, out = ctx["seller"], []
    out.append(Paragraph(mixed(f"{s['name']}" + (f" / {s['name_np']}" if s.get("name_np") else ""), True), center))
    out.append(Paragraph(mixed(f"{s['address']} · PAN/VAT: {s['pan'] or '—'} · {s['branch']}"), ParagraphStyle(
        "s", parent=body, alignment=1)))
    out.append(Spacer(1, 4 * mm))
    out.append(Paragraph(mixed(f"{ctx['title_np']} — {ctx['title_en']}", True), center))
    if ctx["stamp"]["label"]:
        out.append(Paragraph(ctx["stamp"]["label"], ParagraphStyle("st", parent=center, textColor=colors.red,
                                                                   fontSize=11)))
    if ctx.get("cancelled"):
        out.append(Paragraph(mixed(f"CANCELLED on {ctx['cancelled_at']['bs']} ({ctx['cancelled_at']['ad']}): "
                                   f"{ctx['cancel_reason']}"), ParagraphStyle("cx", parent=center, textColor=colors.red,
                                                                        fontSize=11)))
    out.append(Spacer(1, 3 * mm))
    b = ctx["buyer"]
    meta = [
        [Paragraph(f"<b>No.:</b> {ctx['number']}", body), Paragraph(f"<b>Fiscal year:</b> {ctx['fiscal_year']}", body)],
        [Paragraph(f"<b>Buyer:</b> {mixed(b['name'] or '—')}", body),
         Paragraph(f"<b>Buyer PAN:</b> {b['pan'] or '—'}", body)],
        [Paragraph(f"<b>Address:</b> {mixed(b['address'] or '—')}", body),
         Paragraph(f"<b>Issue date:</b> {ctx['issued']['bs']} BS ({ctx['issued']['ad']})", body)],
    ]
    if ctx["doc"] == "invoice":
        meta.append([Paragraph(f"<b>Transaction date:</b> {ctx['transaction']['bs']} BS ({ctx['transaction']['ad']})",
                               body),
                     Paragraph(f"<b>Payment:</b> {ctx['payment_method']} {ctx['payment_ref']}", body)])
    else:
        o = ctx["original"]
        meta.append([Paragraph(f"<b>Against invoice:</b> {o['number']}", body),
                     Paragraph(f"<b>Invoice date:</b> {o['date_bs']} BS ({o['date_ad']})", body)])
        meta.append([Paragraph(f"<b>Reason:</b> {mixed(ctx['reason'])}", body), ""])
    t = Table(meta, colWidths=[90 * mm, 90 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    out += [t, Spacer(1, 3 * mm)]
    if ctx["doc"] == "invoice":
        head = ["S.No.", "HS code", "Description", "Qty", "Rate", "VAT", "Amount"]
        data = [head] + [[r["sno"], r["hs_code"], Paragraph(mixed(r["description"]), body), r["qty"], r["rate"],
                          r["vat_rate"], r["amount"]] for r in ctx["lines"]]
        widths = [12, 22, 70, 14, 22, 16, 26]
    else:
        head = ["S.No.", "Description", "Qty", "Amount", "VAT"]
        data = [head] + [[r["sno"], Paragraph(mixed(r["description"]), body), r["qty"], r["amount"], r["vat"]]
                         for r in ctx["lines"]]
        widths = [12, 100, 18, 26, 26]
    lt = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    lt.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ALIGN", (-1, 1), (-1, -1), "RIGHT"), ("ALIGN", (-2, 1), (-2, -1), "RIGHT"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
    ]))
    out += [lt, Spacer(1, 2 * mm)]
    totals = []
    if ctx["doc"] == "invoice":
        totals += [["Total", ctx["subtotal"]], ["Discount", ctx["discount"]], ["Taxable value", ctx["taxable_value"]]]
        if ctx["exempt_sales"] != "0.00":
            totals.append(["of which exempt / zero-rated", ctx["exempt_sales"]])
        totals.append([ctx["vat_label"], ctx["vat"]])
        for k, label in (("excise", "Excise"), ("hst", "Health Service Tax"), ("esf", "Education Service Fee"),
                         ("digital_discount", "Digital payment VAT discount"), ("round_adjust", "Rounding")):
            if ctx.get(k):
                totals.append([label, ctx[k]])
    else:
        totals += [["Taxable value", ctx["taxable_value"]], [ctx.get("vat_label", "VAT"), ctx["vat"]]]
    totals.append(["Grand total", ctx["total"]])
    tt = Table(totals, colWidths=[50 * mm, 32 * mm], hAlign="RIGHT")
    tt.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                            ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.black)]))
    out += [tt, Spacer(1, 2 * mm), Paragraph(f"<b>In words:</b> {mixed(ctx['in_words'])}", body),
            Spacer(1, 12 * mm)]
    sig = Table([["____________________", "____________________"], ["Prepared by", "Authorised signature"]],
                colWidths=[90 * mm, 90 * mm])
    sig.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    out.append(sig)
    doc.build(out)
    return buf.getvalue()
