"""Checkout and post-sale flows (§10; payment.md Steps 2–4). The rules that matter:

* stock rows are locked sorted by product_id, then the document sequence row;
* one transaction per commit — stock, invoice and CBMS outbox are atomic;
* FonePay holds no locks during the external wait; commit happens on a
  confirmed status and is idempotent;
* "paid but out of stock" records the sale with stock_conflict (§10.6);
* issued invoices are append-only; corrections are credit/debit notes;
* every invoice is full (Rule 17) or abbreviated (Rule 18) — abbreviated is
  refused above the limit and replaced by full when the buyer gives a PAN.
"""
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.permissions import verify_approver
from admin_extras.models import log_action
from catalog.models import Product, effective_prices, tax_profile
from compliance import bs, cbms
from compliance.models import get_settings
from inventory.models import InventoryItem, StockMovement
from inventory.services import lock_items

from . import fonepay
from .models import (
    DIGITAL_METHODS, CashierShift, CreditNote, CreditNoteLine, DebitNote, DocumentSequence, Payment, PrintLog,
    Receipt, Sale, SaleLine,
)
from .numbering import allocate, claim_offline, fiscal_year_for, local_today, series_mode
from .pricing import NONE, LineInput, allocate as allocate_shares, compute_totals, rate_fraction, round_half_up


class CheckoutError(ValidationError):
    pass


class StockUnavailable(CheckoutError):
    def __init__(self, lines):
        self.lines = lines
        super().__init__("Insufficient stock: " + ", ".join(f"{l['sku']} (have {l['available']}, need {l['qty']})"
                                                            for l in lines))


@dataclass
class CartLine:
    product_id: int
    qty: int
    discount_type: str = NONE
    discount_value: int = 0
    discount_reason: str = ""
    # Offline sync only: the price/tax the register actually charged.
    unit_price_paisa: int | None = None
    tax_rate: Decimal | None = None
    tax_inclusive: bool | None = None


@dataclass
class Cart:
    lines: list[CartLine]
    sale_discount_type: str = NONE
    sale_discount_value: int = 0
    sale_discount_reason: str = ""
    customer_id: int | None = None
    buyer_name: str = ""
    buyer_address: str = ""
    buyer_pan: str = ""
    approver_username: str = ""
    approver_password: str = ""
    extra: dict = field(default_factory=dict)


def cash_rounding():
    return settings.IMS.get("CASH_ROUNDING_PAISA", 1)


def digital_discount_bp(method):
    """Digital-payment VAT discount (payment.md Step 3) — OFF by default."""
    cfg = get_settings()
    if not cfg.digital_vat_discount_enabled or method not in DIGITAL_METHODS:
        return 0
    methods = cfg.digital_vat_discount_methods or sorted(DIGITAL_METHODS)
    return cfg.digital_vat_discount_bp if method in methods else 0


def price_cart(branch, cart: Cart, rounding=1, use_client_prices=False, on_date=None, digital_bp=0):
    ids = [l.product_id for l in cart.lines]
    products = Product.objects.select_related("tax_category").in_bulk(ids)
    missing = set(ids) - set(products)
    if missing:
        raise CheckoutError(f"Unknown products: {sorted(missing)}")
    prices = effective_prices(branch, ids)
    on_date = on_date or local_today(branch)
    inputs, price_drift = [], False
    for l in cart.lines:
        p = products[l.product_id]
        if not use_client_prices and (p.archived_at or not p.is_active):
            raise CheckoutError(f"{p.sku} is archived and cannot be sold")
        rate, kind, levy_type, levy_rate, _ = tax_profile(p, on_date)
        price, incl = prices[p.id], p.tax_inclusive
        if use_client_prices and l.unit_price_paisa is not None:
            if (l.unit_price_paisa, Decimal(str(l.tax_rate)), l.tax_inclusive) != (price, rate, incl):
                price_drift = True
            price, rate, incl = l.unit_price_paisa, Decimal(str(l.tax_rate)), bool(l.tax_inclusive)
        inputs.append(LineInput(price, l.qty, rate, incl, l.discount_type, l.discount_value, kind,
                                p.excise_rate, levy_type, levy_rate))
    try:
        totals = compute_totals(inputs, cart.sale_discount_type, cart.sale_discount_value, rounding, digital_bp)
    except ValueError as e:
        raise CheckoutError(str(e))
    return products, inputs, totals, price_drift


def decide_invoice_type(cart: Cart, total_paisa):
    """Rule 17/18: abbreviated only up to the limit and only without a buyer PAN."""
    cfg = get_settings()
    pan = (cart.buyer_pan or "").strip()
    if pan and not (pan.isdigit() and len(pan) == 9):
        raise CheckoutError("Buyer PAN must be 9 digits")
    if pan:
        if not cart.buyer_name.strip():
            raise CheckoutError("A full tax invoice needs the buyer's name")
        return Sale.FULL
    if total_paisa > cfg.abbreviated_invoice_limit_paisa:
        if not (cart.buyer_name.strip() and cart.buyer_address.strip()):
            raise CheckoutError(
                f"Bills over Rs {cfg.abbreviated_invoice_limit_paisa / 100:,.0f} need a full tax invoice: "
                "enter the buyer's name and address (and PAN if registered)")
        if cfg.require_buyer_pan_over_limit:
            raise CheckoutError("Bills over the limit need the buyer's PAN")
        return Sale.FULL
    return Sale.FULL if cart.buyer_name.strip() and cart.buyer_address.strip() else Sale.ABBREVIATED


def check_discount_authority(branch, cashier, totals, cart, offline=False):
    """Discounts above the cashier limit need discount.approve — the cashier's
    own, or a manager typing credentials at the register."""
    gross = sum(l.gross_paisa for l in totals.lines)
    given = sum(l.line_discount_paisa + l.sale_discount_share_paisa for l in totals.lines)
    limit_bp = settings.IMS.get("CASHIER_MAX_DISCOUNT_BP", 500)
    if not given or given * 10000 <= gross * limit_bp or cashier.has_branch_perm("discount.approve", branch):
        return None
    if offline:
        return "offline"  # approved locally with a cached manager session; flagged for review
    from rest_framework.exceptions import PermissionDenied

    try:
        return verify_approver(branch, cart.approver_username, cart.approver_password, "discount.approve")
    except PermissionDenied as e:
        raise CheckoutError(f"Discount over {limit_bp / 100:.1f}% needs manager approval: {e.detail}")


def open_shift_for(register):
    return CashierShift.objects.filter(register=register, status=CashierShift.OPEN).first()


def _write_lines(sale, cart, products, inputs, totals):
    SaleLine.objects.bulk_create([
        SaleLine(
            sale=sale, product_id=cl.product_id, product_name=products[cl.product_id].name,
            hs_code=products[cl.product_id].hs_code, qty=cl.qty,
            unit_price_paisa=inp.unit_price_paisa, discount_type=cl.discount_type,
            discount_value=cl.discount_value, discount_reason=cl.discount_reason,
            line_discount_paisa=lr.line_discount_paisa, sale_discount_share_paisa=lr.sale_discount_share_paisa,
            line_total_paisa=lr.line_total_paisa,
            tax_category_code=(products[cl.product_id].tax_category.code
                               if products[cl.product_id].tax_category_id else ""),
            tax_kind=inp.tax_kind, tax_rate=inp.tax_rate, tax_inclusive=inp.tax_inclusive,
            tax_amount_paisa=lr.tax_amount_paisa, excise_rate=inp.excise_rate, excise_paisa=lr.excise_paisa,
            levy_type=inp.levy_type, levy_rate=inp.levy_rate, levy_paisa=lr.levy_paisa,
        )
        for cl, inp, lr in zip(cart.lines, inputs, totals.lines)
    ])


def _decrement_stock(sale, by, strict):
    """Lock (sorted), check, decrement, write sale_out, snapshot unit cost.

    strict=True (online cash): raise StockUnavailable. strict=False (FonePay
    commit, offline sync): record anyway and flag stock_conflict.
    """
    lines = list(sale.lines.select_related("product"))
    needed = {}
    for l in lines:
        needed[l.product_id] = needed.get(l.product_id, 0) + l.qty
    items = lock_items(sale.branch, needed.keys())
    short = [
        {"product_id": pid, "sku": next(l.product.sku for l in lines if l.product_id == pid),
         "available": items[pid].quantity_on_hand, "qty": qty}
        for pid, qty in sorted(needed.items())
        if items[pid].quantity_on_hand < qty and not items[pid].allow_negative
    ]
    if short and strict:
        raise StockUnavailable(short)
    movements = []
    for l in sorted(lines, key=lambda l: l.product_id):
        item = items[l.product_id]
        l.unit_cost_paisa = item.weighted_avg_cost_paisa
        item.quantity_on_hand -= l.qty
        movements.append(StockMovement(
            branch_id=sale.branch_id, product_id=l.product_id, type=StockMovement.SALE_OUT, qty=-l.qty,
            unit_cost_paisa=l.unit_cost_paisa, ref=str(sale.id), ref_type="sale", by=by,
        ))
    InventoryItem.objects.bulk_update(items.values(), ["quantity_on_hand"])
    StockMovement.objects.bulk_create(movements)
    SaleLine.objects.bulk_update(lines, ["unit_cost_paisa"])
    return bool(short)


def _issue(sale, invoice_type, now=None, transaction_at=None, offline_seq=None, offline_fy=None):
    """Assign the invoice number and dates, create print bookkeeping and the
    CBMS outbox row. Called inside the sale's transaction, after stock."""
    now = now or timezone.now()
    tz = sale.branch.tz
    issue_date = bs.local_date(now, tz)
    if offline_seq is not None:
        fy = offline_fy or fiscal_year_for(issue_date)
        n, series, number = claim_offline(sale.branch, sale.register, fy, offline_seq)
    else:
        fy = fiscal_year_for(issue_date)
        n, series, number = allocate(DocumentSequence.INVOICE, sale.branch, sale.register, fy)
    transaction_at = transaction_at or now
    sale.fiscal_year, sale.series, sale.receipt_seq, sale.receipt_number = fy, series, n, number
    sale.invoice_type = invoice_type
    sale.issued_at, sale.issued_bs = now, bs.bs_str(issue_date)
    sale.transaction_at = transaction_at
    sale.transaction_bs = bs.bs_str(bs.local_date(transaction_at, tz))
    sale.save()
    Receipt.objects.create(sale=sale, number=number)
    cbms.enqueue_bill(sale)



def _idempotent(key, fn):
    existing = Sale.objects.filter(idempotency_key=key).first()
    if existing:
        return existing, False
    try:
        return fn(), True
    except IntegrityError:
        # A concurrent retry with the same key won the race.
        existing = Sale.objects.filter(idempotency_key=key).first()
        if existing:
            return existing, False
        raise


def _sale_fields(branch, register, cashier, cart, totals, key, method):
    return dict(
        branch=branch, register=register, cashier=cashier, customer_id=cart.customer_id,
        buyer_name=cart.buyer_name.strip(), buyer_address=cart.buyer_address.strip(),
        buyer_pan=cart.buyer_pan.strip(), payment_method=method,
        subtotal_paisa=totals.subtotal_paisa, discount_total_paisa=totals.discount_total_paisa,
        tax_total_paisa=totals.tax_total_paisa, excise_total_paisa=totals.excise_total_paisa,
        hst_total_paisa=totals.hst_total_paisa, esf_total_paisa=totals.esf_total_paisa,
        digital_vat_discount_paisa=totals.digital_vat_discount_paisa,
        round_adjust_paisa=totals.round_adjust_paisa, total_paisa=totals.total_paisa,
        taxable_sales_vat_paisa=totals.taxable_sales_vat_paisa, excisable_amount_paisa=totals.excisable_amount_paisa,
        taxable_sales_hst_paisa=totals.taxable_sales_hst_paisa, amount_for_esf_paisa=totals.amount_for_esf_paisa,
        tax_exempted_sales_paisa=totals.tax_exempted_sales_paisa,
        zero_rated_sales_paisa=totals.zero_rated_sales_paisa,
        sale_discount_type=cart.sale_discount_type, sale_discount_value=cart.sale_discount_value,
        sale_discount_reason=cart.sale_discount_reason, tax_allocation_method=totals.allocation_method,
        idempotency_key=key,
    )


# --- 10.1 Online cash / card / wallet / bank ----------------------------------

def checkout_cash(*, branch, register, cashier, cart: Cart, tendered_paisa, idempotency_key, request=None,
                  method=Payment.CASH, payment_ref="", now=None):
    """Immediate-settlement checkout. Cash gives change; card/wallet/bank are
    recorded with their reference (settled outside the system)."""
    if not idempotency_key:
        raise CheckoutError("idempotency_key is required")
    if method == Payment.FONEPAY:
        raise CheckoutError("Use the FonePay QR flow for FonePay")

    def run():
        shift = open_shift_for(register)
        if shift is None:
            raise CheckoutError("Open a cashier shift on this register first")
        with transaction.atomic():
            rounding = cash_rounding() if method == Payment.CASH else 1
            products, inputs, totals, _ = price_cart(branch, cart, rounding, digital_bp=digital_discount_bp(method))
            invoice_type = decide_invoice_type(cart, totals.total_paisa)
            approver = check_discount_authority(branch, cashier, totals, cart)
            tendered = tendered_paisa if method == Payment.CASH else totals.total_paisa
            if tendered < totals.total_paisa:
                raise CheckoutError("Tendered cash is less than the total")
            if method != Payment.CASH and not payment_ref.strip():
                raise CheckoutError(f"Enter the {method} reference (slip / transaction id)")
            sale = Sale.objects.create(
                **_sale_fields(branch, register, cashier, cart, totals, idempotency_key, method),
                shift=shift, status=Sale.PAID, paid_at=now or timezone.now(),
                discount_approved_by=approver if approver and approver != "offline" else None,
            )
            _write_lines(sale, cart, products, inputs, totals)
            _decrement_stock(sale, cashier, strict=True)
            Payment.objects.create(
                sale=sale, method=method, amount_paisa=sale.total_paisa, tendered_paisa=tendered,
                change_paisa=tendered - sale.total_paisa, status=Payment.SUCCESS, ref=payment_ref.strip(),
            )
            _issue(sale, invoice_type, now=now)
            log_action(cashier, "sale.paid", sale, branch, request, total=sale.total_paisa, method=method,
                       invoice=sale.receipt_number, invoice_type=invoice_type)
            return sale

    return _idempotent(idempotency_key, run)


# --- 10.2 FonePay QR -----------------------------------------------------------

def initiate_fonepay(*, branch, register, cashier, cart: Cart, idempotency_key, request=None):
    """Validate and freeze the cart, create an open Sale + initiated Payment, ask
    FonePay for a QR. No stock is locked or decremented here."""

    def run():
        shift = open_shift_for(register)
        if shift is None:
            raise CheckoutError("Open a cashier shift on this register first")
        with transaction.atomic():
            products, inputs, totals, _ = price_cart(branch, cart, rounding=1,
                                                     digital_bp=digital_discount_bp(Payment.FONEPAY))
            decide_invoice_type(cart, totals.total_paisa)  # fail fast before the customer pays
            approver = check_discount_authority(branch, cashier, totals, cart)
            on_hand = {i.product_id: i for i in InventoryItem.objects.filter(
                branch=branch, product_id__in=[l.product_id for l in cart.lines])}
            short = [products[l.product_id].sku for l in cart.lines
                     if (i := on_hand.get(l.product_id)) is None
                     or (i.quantity_on_hand < l.qty and not i.allow_negative)]
            if short:
                raise CheckoutError(f"Insufficient stock: {', '.join(short)}")
            expires = timezone.now() + timedelta(minutes=settings.IMS["FONEPAY_EXPIRY_MINUTES"])
            sale = Sale.objects.create(
                **_sale_fields(branch, register, cashier, cart, totals, idempotency_key, Payment.FONEPAY),
                shift=shift, status=Sale.OPEN, expires_at=expires,
                discount_approved_by=approver if approver and approver != "offline" else None,
            )
            _write_lines(sale, cart, products, inputs, totals)
            prn = fonepay.new_prn()
            Payment.objects.create(
                sale=sale, method=Payment.FONEPAY, amount_paisa=sale.total_paisa, fonepay_prn=prn,
                status=Payment.INITIATED, expires_at=expires,
            )
            sale.payment_ref = prn
            sale.save(update_fields=["payment_ref"])
        # External call outside the transaction; if it fails the open sale just expires.
        qr = fonepay.get_gateway().create_qr(prn, sale.total_paisa, f"{branch.code}-{sale.id}")
        log_action(cashier, "sale.fonepay_initiated", sale, branch, request, prn=prn, total=sale.total_paisa)
        sale._qr_payload = qr.qr_payload
        return sale

    return _idempotent(idempotency_key, run)


def _cart_of(sale):
    return Cart(lines=[], buyer_name=sale.buyer_name, buyer_address=sale.buyer_address, buyer_pan=sale.buyer_pan)


def commit_fonepay(sale_id, status: fonepay.StatusResult, by=None):
    """Apply a confirmed FonePay status. Idempotent: repeated callbacks/polls
    after success are no-ops."""
    with transaction.atomic():
        sale = Sale.objects.select_for_update(of=("self",)).select_related("branch", "register").get(pk=sale_id)
        payment = Payment.objects.select_for_update().get(sale=sale, method=Payment.FONEPAY)
        if payment.status == Payment.SUCCESS or sale.status != Sale.OPEN:
            return sale
        if status.status == fonepay.PENDING:
            return sale
        if status.status in (fonepay.FAILED, fonepay.EXPIRED):
            payment.status = Payment.FAILED if status.status == fonepay.FAILED else Payment.EXPIRED
            payment.save(update_fields=["status"])
            sale.status, sale.voided_at, sale.void_reason = Sale.VOIDED, timezone.now(), f"fonepay {status.status}"
            sale.save(update_fields=["status", "voided_at", "void_reason"])
            log_action(by, f"sale.fonepay_{status.status}", sale, sale.branch)
            return sale

        payment.status = Payment.SUCCESS
        payment.fonepay_paid_paisa = status.paid_paisa
        payment.fonepay_trx_id = status.trx_id
        payment.ref = status.trx_id
        payment.save(update_fields=["status", "fonepay_paid_paisa", "fonepay_trx_id", "ref"])
        if status.paid_paisa != sale.total_paisa:
            # Under/overpayment is a payment error, not a stock error.
            sale.status = Sale.PAYMENT_ORPHAN
            sale.save(update_fields=["status"])
            log_action(by, "sale.payment_orphan", sale, sale.branch, paid=status.paid_paisa, total=sale.total_paisa)
            return sale
        sale.stock_conflict = _decrement_stock(sale, sale.cashier, strict=False)
        sale.status, sale.paid_at = Sale.PAID, timezone.now()
        sale.save(update_fields=["stock_conflict", "status", "paid_at"])
        _issue(sale, decide_invoice_type(_cart_of(sale), sale.total_paisa), transaction_at=sale.opened_at)
        log_action(by, "sale.paid", sale, sale.branch, total=sale.total_paisa, method="fonepay",
                   stock_conflict=sale.stock_conflict, invoice=sale.receipt_number)
        return sale


def refresh_fonepay_status(sale, by=None):
    """Polling path (and the fallback for missed callbacks)."""
    if sale.status != Sale.OPEN:
        return sale
    payment = sale.payments.filter(method=Payment.FONEPAY).first()
    if payment is None:
        return sale
    return commit_fonepay(sale.id, fonepay.get_gateway().check_status(payment.fonepay_prn), by)


def expire_open_sales(now=None):
    """§10.5 — run every minute from cron (``manage.py expire_open_sales``)."""
    now = now or timezone.now()
    done = []
    for sale in Sale.objects.filter(status=Sale.OPEN, expires_at__lt=now):
        sale = refresh_fonepay_status(sale)  # one final poll
        if sale.status == Sale.OPEN:
            sale = commit_fonepay(sale.id, fonepay.StatusResult(fonepay.EXPIRED))
        done.append(sale)
    return done


# --- 10.3 Offline cash sync ----------------------------------------------------

def sync_offline_sale(*, branch, register, cashier, cart: Cart, tendered_paisa, idempotency_key, receipt_seq,
                      fiscal_year, created_at, client_total_paisa=None, offline_discount_approved=False,
                      provisional_ref="", request=None):
    """Record a sale the register already completed offline. Never rejected for
    stock — the customer left with the goods (§10.6).

    Register-series mode: the till's local number becomes the invoice number.
    Branch-series mode: the till printed a provisional slip; the tax invoice is
    issued now (issue date = today, transaction date = when it happened)."""

    def run():
        with transaction.atomic():
            tx_date = bs.local_date(created_at, branch.tz) if created_at else None
            products, inputs, totals, price_drift = price_cart(branch, cart, cash_rounding(), use_client_prices=True,
                                                               on_date=tx_date)
            try:
                invoice_type = decide_invoice_type(cart, totals.total_paisa)
            except CheckoutError:
                # The customer has gone; issue full with what we have and flag it.
                invoice_type, needs_buyer = Sale.FULL, True
            else:
                needs_buyer = False
            approver = check_discount_authority(branch, cashier, totals, cart, offline=True)
            needs_review = price_drift or approver == "offline" or offline_discount_approved or needs_buyer
            if client_total_paisa is not None and client_total_paisa != totals.total_paisa:
                needs_review = True
            sale = Sale.objects.create(
                **_sale_fields(branch, register, cashier, cart, totals, idempotency_key, Payment.CASH),
                shift=open_shift_for(register), status=Sale.PAID, paid_at=created_at or timezone.now(),
                offline=True, offline_created_at=created_at, needs_review=needs_review,
                provisional_ref=provisional_ref,
            )
            _write_lines(sale, cart, products, inputs, totals)
            sale.stock_conflict = _decrement_stock(sale, cashier, strict=False)
            sale.save(update_fields=["stock_conflict"])
            Payment.objects.create(
                sale=sale, method=Payment.CASH, amount_paisa=sale.total_paisa,
                tendered_paisa=max(tendered_paisa, sale.total_paisa),
                change_paisa=max(tendered_paisa - sale.total_paisa, 0), status=Payment.SUCCESS,
            )
            register_mode = series_mode() == "register"
            _issue(sale, invoice_type, transaction_at=created_at,
                   offline_seq=receipt_seq if register_mode else None,
                   offline_fy=fiscal_year if register_mode else None)
            log_action(cashier, "sale.offline_synced", sale, branch, request, total=sale.total_paisa,
                       client_total=client_total_paisa, stock_conflict=sale.stock_conflict,
                       provisional_ref=provisional_ref, invoice=sale.receipt_number)
            return sale

    return _idempotent(idempotency_key, run)


# --- Cancel, conflicts, returns, debit notes ----------------------------------

def void_sale(sale, by, reason, request=None):
    """Cancel an issued invoice. It keeps its number and stays visible and
    printable (stamped CANCELLED); a reason is mandatory."""
    if not reason or not reason.strip():
        raise ValidationError("A cancellation reason is required")
    with transaction.atomic():
        sale = Sale.objects.select_for_update(of=("self",)).select_related("branch").get(pk=sale.pk)
        if sale.status != Sale.PAID:
            raise ValidationError(f"Only paid sales can be cancelled (this one is {sale.status})")
        if sale.credit_notes.exists() or sale.debit_notes.exists():
            raise ValidationError("This invoice has credit/debit notes against it; correct it with another note "
                                  "instead of cancelling")
        lines = list(sale.lines.all())
        items = lock_items(sale.branch, [l.product_id for l in lines])
        movements = []
        for l in sorted(lines, key=lambda l: l.product_id):
            items[l.product_id].quantity_on_hand += l.qty
            movements.append(StockMovement(
                branch_id=sale.branch_id, product_id=l.product_id, type=StockMovement.ADJUST_IN, qty=l.qty,
                unit_cost_paisa=items[l.product_id].weighted_avg_cost_paisa, ref=str(sale.id),
                ref_type="sale_void", by=by, reason=reason,
            ))
        InventoryItem.objects.bulk_update(items.values(), ["quantity_on_hand"])
        StockMovement.objects.bulk_create(movements)
        sale.status, sale.voided_at, sale.voided_by, sale.void_reason = Sale.VOIDED, timezone.now(), by, reason
        sale.save(update_fields=["status", "voided_at", "voided_by", "void_reason"])
        cbms.enqueue_cancellation(sale, reason)
        log_action(by, "sale.cancelled", sale, sale.branch, request, reason=reason, invoice=sale.receipt_number)
        return sale


def resolve_conflict(sale, by, resolution, reason="", request=None):
    if not sale.stock_conflict or sale.conflict_resolution:
        raise ValidationError("Sale has no unresolved stock conflict")
    if resolution not in ("accepted", "refunded"):
        raise ValidationError("resolution must be 'accepted' or 'refunded'")
    with transaction.atomic():
        if resolution == "refunded":
            sale = void_sale(sale, by, reason or "stock conflict refund", request)
        sale.conflict_resolution, sale.conflict_resolved_by = resolution, by
        sale.conflict_resolved_at = timezone.now()
        sale.save(update_fields=["conflict_resolution", "conflict_resolved_by", "conflict_resolved_at"])
        log_action(by, "sale.conflict_resolved", sale, sale.branch, request, resolution=resolution)
    return sale


def returnable_qty(sale_line):
    returned = sum(r.qty for r in sale_line.returns.all())
    return sale_line.qty - returned


def _pro_rata(total, part, whole):
    return round_half_up(Fraction(total * part, whole))


def _slice(total, already, qty, whole):
    """Cumulative pro-rata share so several partial returns sum exactly to total."""
    return _pro_rata(total, already + qty, whole) - _pro_rata(total, already, whole)


def create_return(*, sale, register, cashier, items, reason, refund_method="cash", approver_username="",
                  approver_password="", request=None):
    """Credit note (§9.5). ``items``: [(sale_line_id, qty)]. Partial returns allowed.

    Each component (paid amount, VAT, excise, levy) is refunded pro rata,
    cumulatively, so returning a line in several goes refunds it exactly.
    """
    if not reason or not reason.strip():
        raise ValidationError("A return reason is required")
    if sale.status != Sale.PAID:
        raise ValidationError("Only paid (not cancelled) invoices can be returned")
    with transaction.atomic():
        sale = Sale.objects.select_for_update(of=("self",)).select_related("branch").get(pk=sale.pk)
        lines = {l.id: l for l in sale.lines.prefetch_related("returns")}
        # A digital-payment VAT discount reduced the amount paid; spread it over
        # lines by VAT so refunds never exceed what was paid.
        ordered = list(lines.values())
        digital = dict(zip([l.id for l in ordered],
                           allocate_shares(sale.digital_vat_discount_paisa, [l.tax_amount_paisa for l in ordered])))
        planned = []
        for line_id, qty in items:
            line = lines.get(int(line_id))
            if line is None:
                raise ValidationError(f"Line {line_id} is not on this sale")
            if qty <= 0 or qty > returnable_qty(line):
                raise ValidationError(f"Line {line_id}: can return at most {returnable_qty(line)}")
            already = line.qty - returnable_qty(line)
            paid = line.line_total_paisa - digital.get(line.id, 0)
            planned.append((line, qty, {
                "total": _slice(paid, already, qty, line.qty),
                "tax": _slice(line.tax_amount_paisa, already, qty, line.qty),
                "excise": _slice(line.excise_paisa, already, qty, line.qty),
                "levy": _slice(line.levy_paisa, already, qty, line.qty),
            }))
        total = sum(p["total"] for _, _, p in planned)
        approver = None
        threshold = settings.IMS["RETURN_APPROVAL_THRESHOLD_PAISA"]
        if total > threshold and not cashier.has_branch_perm("returns.approve", sale.branch):
            from rest_framework.exceptions import PermissionDenied

            try:
                approver = verify_approver(sale.branch, approver_username, approver_password, "returns.approve")
            except PermissionDenied as e:
                raise ValidationError(f"Returns over NPR {threshold / 100:,.2f} need manager approval: {e.detail}")
        elif total > threshold:
            approver = cashier

        items_locked = lock_items(sale.branch, [l.product_id for l, _, _ in planned])
        today = local_today(sale.branch)
        fy = fiscal_year_for(today)
        seq, series, number = allocate(DocumentSequence.CREDIT_NOTE, sale.branch, register, fy)
        tax = sum(p["tax"] for _, _, p in planned)
        excise = sum(p["excise"] for _, _, p in planned)
        levy = sum(p["levy"] for _, _, p in planned)
        taxable_vat = sum(p["total"] - p["tax"] - p["levy"] for l, _, p in planned if l.tax_rate > 0)
        exempt = sum(p["total"] - p["levy"] for l, _, p in planned if l.tax_rate == 0 and l.tax_kind == "exempt")
        cn = CreditNote.objects.create(
            branch=sale.branch, register=register, original_sale=sale, cashier=cashier,
            shift=open_shift_for(register), approved_by=approver, total_paisa=total,
            taxable_paisa=total - tax - excise - levy, tax_paisa=tax, excise_paisa=excise, levy_paisa=levy,
            taxable_sales_vat_paisa=taxable_vat, tax_exempted_sales_paisa=exempt,
            refund_method=refund_method, fiscal_year=fy, series=series, seq=seq, number=number,
            issued_bs=bs.bs_str(today), reason=reason.strip(),
        )
        movements = []
        for line, qty, p in sorted(planned, key=lambda p: p[0].product_id):
            CreditNoteLine.objects.create(credit_note=cn, original_sale_line=line, qty=qty,
                                          unit_price_paisa=line.unit_price_paisa, line_total_paisa=p["total"],
                                          tax_paisa=p["tax"], excise_paisa=p["excise"], levy_paisa=p["levy"])
            item = items_locked[line.product_id]
            item.quantity_on_hand += qty
            # Back at the current weighted-average cost (§9.6).
            movements.append(StockMovement(
                branch_id=sale.branch_id, product_id=line.product_id, type=StockMovement.RETURN_IN, qty=qty,
                unit_cost_paisa=item.weighted_avg_cost_paisa, ref=str(cn.id), ref_type="credit_note", by=cashier,
                reason=reason,
            ))
        InventoryItem.objects.bulk_update(items_locked.values(), ["quantity_on_hand"])
        StockMovement.objects.bulk_create(movements)
        cbms.enqueue_return(cn)
        log_action(cashier, "sale.returned", cn, sale.branch, request, sale=sale.id, total=total, number=number,
                   approved_by=approver.id if approver else None)
        return cn


def create_debit_note(*, sale, by, taxable_paisa, reason, tax_rate=None, request=None):
    """Debit note: an additional charge on an issued invoice (undercharge)."""
    if not reason or not reason.strip():
        raise ValidationError("A reason is required")
    if taxable_paisa <= 0:
        raise ValidationError("Debit note amount must be positive")
    if sale.status != Sale.PAID or not sale.receipt_number:
        raise ValidationError("Debit notes can only be raised against issued, paid invoices")
    if tax_rate is None:
        rates = {l.tax_rate for l in sale.lines.all()}
        tax_rate = max(rates) if rates else Decimal("0")
    tax_rate = Decimal(str(tax_rate))
    with transaction.atomic():
        today = local_today(sale.branch)
        fy = fiscal_year_for(today)
        seq, _, number = allocate(DocumentSequence.DEBIT_NOTE, sale.branch, None, fy)
        tax = round_half_up(Fraction(taxable_paisa) * rate_fraction(tax_rate) / 100)
        dn = DebitNote.objects.create(
            branch=sale.branch, original_sale=sale, by=by, taxable_paisa=taxable_paisa, tax_rate=tax_rate,
            tax_paisa=tax, total_paisa=taxable_paisa + tax, fiscal_year=fy, seq=seq, number=number,
            issued_bs=bs.bs_str(today), reason=reason.strip(),
        )
        log_action(by, "sale.debit_note", dn, sale.branch, request, sale=sale.id, total=dn.total_paisa,
                   number=number)
        return dn


# --- Printing -------------------------------------------------------------------

def record_print(doc_type, doc, by, kind=PrintLog.ORIGINAL):
    """Print once: the first print is the original; every later print is a
    'Copy of Original' with its copy number. Delivery copies are separate."""
    with transaction.atomic():
        number = getattr(doc, "receipt_number", None) or doc.number
        # Lock the document row so two simultaneous first prints can't both be "original".
        type(doc).objects.select_for_update().filter(pk=doc.pk).exists()
        prior = PrintLog.objects.filter(doc_type=doc_type, doc_id=doc.pk)
        has_original = prior.filter(kind=PrintLog.ORIGINAL).exists()
        if kind != PrintLog.DELIVERY:
            kind = PrintLog.COPY if has_original else PrintLog.ORIGINAL
        copy_no = prior.filter(kind=PrintLog.COPY).count() + 1 if kind == PrintLog.COPY else 0
        log = PrintLog.objects.create(doc_type=doc_type, doc_id=doc.pk, document_number=number, kind=kind,
                                      copy_no=copy_no, by=by)
        if doc_type == "invoice":
            r, _ = Receipt.objects.get_or_create(sale=doc, defaults={"number": number})
            r.print_count += 1
            r.printed_at = timezone.now()
            r.save(update_fields=["print_count", "printed_at"])
        log_action(by, f"print.{kind}", doc, getattr(doc, "branch", None), document=number, copy_no=copy_no)
        return log


# --- Shifts --------------------------------------------------------------------

def open_shift(branch, register, cashier, opening_cash_paisa, request=None):
    if opening_cash_paisa < 0:
        raise ValidationError("Opening cash cannot be negative")
    if open_shift_for(register):
        raise ValidationError("This register already has an open shift")
    try:
        shift = CashierShift.objects.create(branch=branch, register=register, cashier=cashier,
                                            opening_cash_paisa=opening_cash_paisa)
    except IntegrityError:
        raise ValidationError("This register already has an open shift")
    log_action(cashier, "shift.opened", shift, branch, request, opening=opening_cash_paisa)
    return shift


def expected_cash(shift):
    from django.db.models import Sum

    cash_in = Payment.objects.filter(
        sale__shift=shift, sale__status=Sale.PAID, method=Payment.CASH, status=Payment.SUCCESS
    ).aggregate(s=Sum("amount_paisa"))["s"] or 0
    refunds = CreditNote.objects.filter(shift=shift, refund_method="cash").aggregate(s=Sum("total_paisa"))["s"] or 0
    return shift.opening_cash_paisa + cash_in - refunds


def close_shift(shift, by, counted_cash_paisa, offline=False, request=None):
    if shift.status != CashierShift.OPEN:
        raise ValidationError("Shift is already closed")
    shift.expected_cash_paisa = expected_cash(shift)
    shift.counted_cash_paisa = counted_cash_paisa
    shift.variance_paisa = counted_cash_paisa - shift.expected_cash_paisa
    shift.status, shift.closed_at, shift.closed_offline = CashierShift.CLOSED, timezone.now(), offline
    shift.save()
    log_action(by, "shift.closed", shift, shift.branch, request, expected=shift.expected_cash_paisa,
               counted=counted_cash_paisa, variance=shift.variance_paisa)
    return shift
