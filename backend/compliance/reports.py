"""Statutory and control reports (payment.md Step 5).

Every report returns the same shape so one exporter handles Excel/XML/PDF/CSV:
    {"title", "subtitle", "columns": [(key, label, kind)], "rows": [...], "totals": {...}, "notes": [...]}
kind: "text" | "int" | "money" (paisa) | "date".

Periods are BS fiscal months; "today" boundaries use each branch's timezone.
"""
from datetime import date, timedelta

from django.db.models import Q, Sum
from django.utils import timezone

from admin_extras.models import AuditLog
from inventory.models import InventoryItem, StockMovement
from purchasing.models import GoodsReceipt, PurchaseReturn
from sales.models import CreditNote, DebitNote, Sale

from . import bs
from .models import CbmsPost, get_settings


class Period:
    """A BS fiscal year, optionally narrowed to one fiscal month (1 = Shrawan)."""

    def __init__(self, fy: str | None = None, month: int | None = None):
        self.fy = fy or bs.fiscal_year_label(timezone.localdate())
        self.month = int(month) if month else None
        if self.month:
            y, m = bs.fy_month(self.fy, self.month)
            self.first, self.last = bs.bs_month_bounds(y, m)
            self.label = f"{bs.month_name(m)} {y} (FY {self.fy.replace('-', '/')})"
        else:
            self.first, self.last = bs.fy_bounds(self.fy)
            self.label = f"FY {self.fy.replace('-', '/')}"

    def range(self, tz):
        return bs.aware_range(self.first, self.last, tz)

    @property
    def subtitle(self):
        return f"{self.label} · {bs.bs_str(self.first)} – {bs.bs_str(self.last)} BS ({self.first} – {self.last})"


def _branch_filter(qs, branches, field="branch"):
    return qs.filter(**{f"{field}__in": branches})


def _in_period(qs, period, branches, field, branch_field="branch"):
    """Filter ``qs`` to rows whose ``field`` falls in the period, per branch tz."""
    cond = Q()
    for b in branches:
        start, end = period.range(b.tz)
        cond |= Q(**{branch_field: b, f"{field}__gte": start, f"{field}__lt": end})
    return qs.filter(cond) if branches else qs.none()


def _sum(rows, keys):
    return {k: sum(r[k] or 0 for r in rows) for k in keys}


# --- Sales book (Schedule-9) ---------------------------------------------------

def sales_book(period, branches):
    sales = _in_period(Sale.objects.exclude(receipt_number=""), period, branches, "issued_at").select_related(
        "branch").order_by("issued_at", "receipt_number")
    rows = []
    for s in sales:
        live = s.status != Sale.VOIDED
        z = (lambda v: v if live else 0)
        rows.append({
            "date_bs": s.issued_bs, "invoice": s.receipt_number, "branch": s.branch.code,
            "buyer": s.buyer_name, "buyer_pan": s.buyer_pan,
            "total": z(s.total_paisa), "exempt": z(s.tax_exempted_sales_paisa),
            "export": z(s.zero_rated_sales_paisa), "taxable": z(s.taxable_sales_vat_paisa),
            "vat": z(s.tax_total_paisa), "excise": z(s.excise_total_paisa),
            "hst": z(s.hst_total_paisa), "esf": z(s.esf_total_paisa),
            "status": "CANCELLED" if not live else s.invoice_type,
        })
    cols = [("date_bs", "Date (BS)", "text"), ("invoice", "Invoice no.", "text"), ("branch", "Branch", "text"),
            ("buyer", "Buyer", "text"), ("buyer_pan", "Buyer PAN", "text"), ("total", "Total sales", "money"),
            ("exempt", "Non-taxable sales", "money"), ("export", "Export / zero-rated", "money"),
            ("taxable", "Taxable amount", "money"), ("vat", "VAT", "money"), ("excise", "Excise", "money"),
            ("hst", "HST", "money"), ("esf", "ESF", "money"), ("status", "Type / status", "text")]
    return {"title": "Sales book (Schedule-9)", "subtitle": period.subtitle, "columns": cols, "rows": rows,
            "totals": _sum(rows, ["total", "exempt", "export", "taxable", "vat", "excise", "hst", "esf"]),
            "notes": ["Cancelled invoices are listed with zero amounts.",
                      "Credit and debit notes are in the sales return register."]}


# --- Purchase book (Schedule-8) ------------------------------------------------

def purchase_book(period, branches):
    grs = _in_period(GoodsReceipt.objects.all(), period, branches, "received_at", "po__branch").select_related(
        "po__supplier", "po__branch").order_by("received_at")
    rows = [{
        "date_bs": g.supplier_invoice_bs or g.received_bs, "invoice": g.supplier_invoice_ref,
        "branch": g.po.branch.code, "supplier": g.po.supplier.name, "supplier_pan": g.po.supplier.pan,
        "total": g.taxable_paisa + g.exempt_paisa + g.vat_paisa, "exempt": g.exempt_paisa,
        "imports": g.taxable_paisa if g.is_import else 0, "taxable": 0 if g.is_import else g.taxable_paisa,
        "vat": g.vat_paisa, "claimable": g.claimable_vat_paisa, "gr": f"GR-{g.id}",
    } for g in grs]
    cols = [("date_bs", "Date (BS)", "text"), ("invoice", "Supplier invoice no.", "text"), ("branch", "Branch", "text"),
            ("supplier", "Supplier", "text"), ("supplier_pan", "Supplier PAN", "text"),
            ("total", "Total purchase", "money"), ("exempt", "Non-taxable", "money"),
            ("imports", "Taxable imports", "money"), ("taxable", "Taxable purchase", "money"),
            ("vat", "VAT", "money"), ("claimable", "Claimable VAT", "money"), ("gr", "Goods receipt", "text")]
    missing = [r["gr"] for r in rows if not r["supplier_pan"] or not r["invoice"]]
    return {"title": "Purchase book (Schedule-8)", "subtitle": period.subtitle, "columns": cols, "rows": rows,
            "totals": _sum(rows, ["total", "exempt", "imports", "taxable", "vat", "claimable"]),
            "notes": ([f"Missing supplier PAN or invoice no.: {', '.join(missing)}"] if missing else [])
            + ["Claimable VAT excludes blocked input credit (petrol, alcohol/beverages, entertainment, 60% vehicles)."]}


# --- Return registers ---------------------------------------------------------

def sales_return_register(period, branches):
    cns = _in_period(CreditNote.objects.all(), period, branches, "at").select_related(
        "original_sale", "branch").order_by("at")
    dns = _in_period(DebitNote.objects.all(), period, branches, "at").select_related(
        "original_sale", "branch").order_by("at")
    rows = [{"date_bs": c.issued_bs, "number": c.number, "type": "Credit note", "branch": c.branch.code,
             "invoice": c.original_sale.receipt_number, "buyer": c.original_sale.buyer_name,
             "buyer_pan": c.original_sale.buyer_pan, "taxable": -c.taxable_paisa, "vat": -c.tax_paisa,
             "total": -c.total_paisa, "reason": c.reason} for c in cns]
    rows += [{"date_bs": d.issued_bs, "number": d.number, "type": "Debit note", "branch": d.branch.code,
              "invoice": d.original_sale.receipt_number, "buyer": d.original_sale.buyer_name,
              "buyer_pan": d.original_sale.buyer_pan, "taxable": d.taxable_paisa, "vat": d.tax_paisa,
              "total": d.total_paisa, "reason": d.reason} for d in dns]
    rows.sort(key=lambda r: (r["date_bs"], r["number"]))
    cols = [("date_bs", "Date (BS)", "text"), ("number", "Note no.", "text"), ("type", "Type", "text"),
            ("branch", "Branch", "text"), ("invoice", "Original invoice", "text"), ("buyer", "Buyer", "text"),
            ("buyer_pan", "Buyer PAN", "text"), ("taxable", "Taxable", "money"), ("vat", "VAT", "money"),
            ("total", "Total", "money"), ("reason", "Reason", "text")]
    return {"title": "Sales return register (credit/debit notes)", "subtitle": period.subtitle, "columns": cols,
            "rows": rows, "totals": _sum(rows, ["taxable", "vat", "total"]),
            "notes": ["Credit notes are negative; debit notes positive."]}


def purchase_return_register(period, branches):
    prs = _in_period(PurchaseReturn.objects.all(), period, branches, "at").select_related(
        "supplier", "branch").order_by("at")
    rows = [{"date_bs": p.issued_bs, "number": p.number, "branch": p.branch.code, "supplier": p.supplier.name,
             "supplier_pan": p.supplier.pan, "supplier_note": p.supplier_note_ref, "taxable": p.taxable_paisa,
             "vat": p.vat_paisa, "claimable": p.claimable_vat_paisa, "reason": p.reason} for p in prs]
    cols = [("date_bs", "Date (BS)", "text"), ("number", "Return no.", "text"), ("branch", "Branch", "text"),
            ("supplier", "Supplier", "text"), ("supplier_pan", "Supplier PAN", "text"),
            ("supplier_note", "Supplier credit note", "text"), ("taxable", "Taxable", "money"),
            ("vat", "VAT", "money"), ("claimable", "Input VAT reversed", "money"), ("reason", "Reason", "text")]
    return {"title": "Purchase return register", "subtitle": period.subtitle, "columns": cols, "rows": rows,
            "totals": _sum(rows, ["taxable", "vat", "claimable"]), "notes": []}


# --- VAT return working ---------------------------------------------------------

def vat_return(period, branches):
    """Monthly VAT return working. Due within 25 days of the BS month end;
    a nil return is still required when there were no transactions."""
    sb = sales_book(period, branches)["totals"]
    sr = sales_return_register(period, branches)
    pb = purchase_book(period, branches)["totals"]
    pr = purchase_return_register(period, branches)["totals"]
    cn = sum(r["vat"] for r in sr["rows"] if r["type"] == "Credit note")
    dn = sum(r["vat"] for r in sr["rows"] if r["type"] == "Debit note")
    cn_taxable = sum(r["taxable"] for r in sr["rows"] if r["type"] == "Credit note")
    dn_taxable = sum(r["taxable"] for r in sr["rows"] if r["type"] == "Debit note")
    output_vat = sb["vat"] + cn + dn
    input_vat = pb["claimable"] - pr["claimable"]
    net = output_vat - input_vat
    due = period.last + timedelta(days=25)
    nil = not (sb["total"] or sr["rows"] or pb["total"] or pr["vat"])
    rows = [
        {"line": "Taxable sales", "amount": sb["taxable"] + cn_taxable + dn_taxable},
        {"line": "Exempt sales", "amount": sb["exempt"]},
        {"line": "Export / zero-rated sales", "amount": sb["export"]},
        {"line": "Output VAT on sales", "amount": sb["vat"]},
        {"line": "Less: VAT on credit notes", "amount": -cn},  # cn is negative in the register
        {"line": "Add: VAT on debit notes", "amount": dn},
        {"line": "Net output VAT", "amount": output_vat},
        {"line": "Taxable purchases", "amount": pb["taxable"]},
        {"line": "Taxable imports", "amount": pb["imports"]},
        {"line": "Exempt purchases", "amount": pb["exempt"]},
        {"line": "Input VAT on purchases (claimable)", "amount": pb["claimable"]},
        {"line": "Less: input VAT on purchase returns", "amount": pr["claimable"]},
        {"line": "Net input VAT", "amount": input_vat},
        {"line": "VAT payable" if net >= 0 else "VAT credit carried forward", "amount": abs(net)},
    ]
    return {"title": "VAT return working", "subtitle": period.subtitle,
            "columns": [("line", "Line", "text"), ("amount", "Amount", "money")], "rows": rows, "totals": {},
            "summary": {"output_vat": output_vat, "input_vat": input_vat, "net": net, "nil_return": nil,
                        "due_date_ad": due.isoformat(), "due_date_bs": bs.bs_str(due)},
            "notes": [f"Due by {bs.bs_str(due)} BS ({due}) — 25 days after the month end.",
                      "NIL return required: no transactions this period." if nil else
                      "File through the IRD taxpayer portal; this is a working, not the return itself.",
                      "Annex 5 / Annex 13 formats are pending IRD confirmation (docs/ird-questions.md)."]}


# --- Stock vs purchase-book reconciliation -------------------------------------

SUPPORTED_TYPES = {StockMovement.PURCHASE_IN, StockMovement.TRANSFER_IN, StockMovement.RETURN_IN,
                   StockMovement.SALE_OUT, StockMovement.TRANSFER_OUT, StockMovement.PURCHASE_RETURN_OUT,
                   StockMovement.ADJUST_OUT}


def stock_reconciliation(period, branches):
    """Stock the books can explain (purchases, transfers, sales, returns) vs
    stock on hand. Increases with no purchase record (manual 'found' stock,
    positive stocktake variances) are 'excess stock' — IRD may fine 50% of its
    market value. Cancelled-invoice restocks count as supported."""
    cfg = get_settings()
    rows = []
    for b in branches:
        mv = (StockMovement.objects.filter(branch=b).values("product_id", "type", "ref_type")
              .annotate(q=Sum("qty")))
        book, unsupported = {}, {}
        for m in mv:
            pid, q = m["product_id"], m["q"] or 0
            explained = (m["type"] in SUPPORTED_TYPES or (m["type"] == StockMovement.ADJUST_IN
                                                           and m["ref_type"] == "sale_void")
                         or (m["type"] == StockMovement.STOCKTAKE_ADJUST and q < 0))
            if explained:
                book[pid] = book.get(pid, 0) + q
            else:
                unsupported[pid] = unsupported.get(pid, 0) + q
        for item in InventoryItem.objects.filter(branch=b).select_related("product"):
            excess = max(0, item.quantity_on_hand - book.get(item.product_id, 0))
            if not excess and not unsupported.get(item.product_id):
                continue
            value = excess * item.product.sale_price_paisa
            rows.append({"branch": b.code, "sku": item.product.sku, "product": item.product.name,
                         "on_hand": item.quantity_on_hand, "book_qty": book.get(item.product_id, 0),
                         "unsupported_in": unsupported.get(item.product_id, 0), "excess": excess,
                         "market_value": value, "potential_fine": value * cfg.excess_stock_fine_bp // 10000})
    cols = [("branch", "Branch", "text"), ("sku", "SKU", "text"), ("product", "Product", "text"),
            ("on_hand", "On hand", "int"), ("book_qty", "Supported by books", "int"),
            ("unsupported_in", "Unsupported increases", "int"), ("excess", "Excess qty", "int"),
            ("market_value", "Market value of excess", "money"), ("potential_fine", "Potential fine", "money")]
    return {"title": "Stock vs purchase-book reconciliation", "subtitle": f"As of {timezone.localdate()} "
            f"({bs.bs_str(timezone.localdate())} BS)", "columns": cols, "rows": rows,
            "totals": _sum(rows, ["excess", "market_value", "potential_fine"]),
            "notes": ["Excess stock = on hand minus what purchases/transfers/sales/returns explain.",
                      f"Potential fine at {cfg.excess_stock_fine_bp / 100:.0f}% of market value (sale price). "
                      "Record a purchase or supplier invoice for found stock, or recount."]}


# --- Control reports ------------------------------------------------------------

def cancelled_bills(period, branches):
    sales = _in_period(Sale.objects.filter(status=Sale.VOIDED).exclude(receipt_number=""), period, branches,
                       "issued_at").select_related("voided_by", "branch")
    rows = [{"invoice": s.receipt_number, "branch": s.branch.code, "issued_bs": s.issued_bs,
             "cancelled_bs": bs.bs_str(bs.local_date(s.voided_at, s.branch.tz)), "total": s.total_paisa,
             "by": s.voided_by.username if s.voided_by else "system", "reason": s.void_reason} for s in sales]
    cols = [("invoice", "Invoice no.", "text"), ("branch", "Branch", "text"), ("issued_bs", "Issued (BS)", "text"),
            ("cancelled_bs", "Cancelled (BS)", "text"), ("total", "Amount", "money"), ("by", "Cancelled by", "text"),
            ("reason", "Reason", "text")]
    return {"title": "Cancelled bills", "subtitle": period.subtitle, "columns": cols, "rows": rows,
            "totals": _sum(rows, ["total"]), "notes": []}


def cbms_sync_report(period, branches):
    posts = _in_period(CbmsPost.objects.all(), period, branches, "issued_at").order_by("issued_at")
    rows = [{"document": p.document_number, "type": p.doc_type, "branch": p.branch.code,
             "issued": timezone.localtime(p.issued_at).strftime("%Y-%m-%d %H:%M"), "status": p.status,
             "code": p.last_code, "attempts": p.attempts, "realtime": "yes" if p.isrealtime else
             ("no" if p.isrealtime is False else ""), "error": p.last_error} for p in posts]
    cols = [("document", "Document", "text"), ("type", "Endpoint", "text"), ("branch", "Branch", "text"),
            ("issued", "Issued", "text"), ("status", "Status", "text"), ("code", "Last code", "text"),
            ("attempts", "Attempts", "int"), ("realtime", "Real-time", "text"), ("error", "Error", "text")]
    return {"title": "CBMS sync report", "subtitle": period.subtitle, "columns": cols, "rows": rows, "totals": {},
            "notes": []}


def audit_trail(period, branches, org_wide=False):
    qs = AuditLog.objects.select_related("user", "branch")
    cond = Q()
    for b in branches:
        start, end = period.range(b.tz)
        cond |= Q(branch=b, at__gte=start, at__lt=end)
    if org_wide and branches:
        start, end = period.range(branches[0].tz)
        cond |= Q(branch__isnull=True, at__gte=start, at__lt=end)
    rows = [{"at": timezone.localtime(a.at).strftime("%Y-%m-%d %H:%M:%S"), "at_bs": a.at_bs,
             "user": a.user.username if a.user else "system", "branch": a.branch.code if a.branch else "",
             "action": a.action, "object": f"{a.object_type} {a.object_id}",
             "changes": "; ".join(f"{k}: {v[0]!s} → {v[1]!s}" for k, v in list(a.changes.items())[:12]),
             "data": ", ".join(f"{k}={v}" for k, v in a.data.items())}
            for a in qs.filter(cond).order_by("at")[:20000]]
    cols = [("at", "When (AD)", "text"), ("at_bs", "Date (BS)", "text"), ("user", "User", "text"),
            ("branch", "Branch", "text"), ("action", "Action", "text"), ("object", "Object", "text"),
            ("changes", "Before → after", "text"), ("data", "Details", "text")]
    return {"title": "Audit trail", "subtitle": period.subtitle, "columns": cols, "rows": rows, "totals": {},
            "notes": []}


# --- Turnover meter --------------------------------------------------------------

def turnover(branches=None):
    """FY-to-date turnover (ex-VAT, net of returns) vs CBMS and VAT thresholds."""
    from tenancy.models import Branch

    branches = branches or list(Branch.objects.all())
    period = Period()
    sb = sales_book(period, branches)["totals"]
    sr = sales_return_register(period, branches)["totals"]
    value = (sb["total"] - sb["vat"]) + (sr["total"] - sr["vat"])
    cfg = get_settings()
    org = cfg.org
    vat_threshold = (cfg.vat_threshold_goods_paisa if org.business_type == "goods"
                     else cfg.vat_threshold_services_paisa)
    alerts = []
    for name, limit in (("CBMS line (Rs 10 crore, per budget)", cfg.cbms_threshold_paisa),
                        ("CBMS mandatory (Rs 20 crore, notice 2083/01/04)", cfg.cbms_mandatory_threshold_paisa)):
        if value >= limit:
            alerts.append({"level": "bad", "message": f"Turnover is over the {name}. CBMS sync must be on."})
        elif value >= limit * 0.8:
            alerts.append({"level": "warn", "message": f"Turnover is at {value * 100 // limit}% of the {name}."})
    if not org.vat_registered and value >= vat_threshold * 0.8:
        alerts.append({"level": "bad" if value >= vat_threshold else "warn",
                       "message": f"Turnover is at {value * 100 // vat_threshold}% of the VAT registration threshold."})
    return {"fiscal_year": period.fy, "turnover_paisa": value, "cbms_threshold_paisa": cfg.cbms_threshold_paisa,
            "cbms_mandatory_threshold_paisa": cfg.cbms_mandatory_threshold_paisa,
            "vat_threshold_paisa": vat_threshold, "vat_registered": org.vat_registered,
            "cbms_enabled": cfg.cbms_enabled, "alerts": alerts}


REPORTS = {
    "sales-book": sales_book, "purchase-book": purchase_book, "sales-returns": sales_return_register,
    "purchase-returns": purchase_return_register, "vat-return": vat_return,
    "stock-reconciliation": stock_reconciliation, "cancelled-bills": cancelled_bills,
    "cbms-sync": cbms_sync_report, "audit-trail": audit_trail,
}
ACCOUNTANT_PACK = ["sales-book", "purchase-book", "sales-returns", "purchase-returns", "vat-return", "cancelled-bills"]


def fiscal_years(n=4):
    current = bs.fiscal_year_label(timezone.localdate())
    start = int(current.split("-")[0])
    years = [f"{y}-{(y + 1) % 100:02d}" for y in range(start, start - n, -1)]
    months = [{"no": i, "name": bs.month_name(bs.fy_month(current, i)[1])} for i in range(1, 13)]
    today = bs.to_bs(timezone.localdate())
    current_month = (today.month - bs.SHRAWAN) % 12 + 1
    return {"current": current, "current_month": current_month, "years": years, "months": months,
            "today_bs": bs.bs_str(timezone.localdate()), "today_ad": date.today().isoformat()}
