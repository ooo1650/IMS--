from django.conf import settings
from django.db import models

from compliance.appendonly import AppendOnlyError, AppendOnlyModel  # noqa: F401

from tenancy.models import BranchScopedQuerySet

from .pricing import AMOUNT, NONE, PERCENT

DISCOUNT_TYPES = [(NONE, "none"), (AMOUNT, "amount"), (PERCENT, "percent")]


class Customer(models.Model):
    """Minimal reference entity (§11). branch=NULL means the whole business."""

    org = models.ForeignKey("tenancy.Organization", on_delete=models.PROTECT, related_name="customers")
    branch = models.ForeignKey("tenancy.Branch", null=True, blank=True, on_delete=models.PROTECT)

    def save(self, *args, **kwargs):
        if self.branch_id is not None and not self.org_id:
            from tenancy.models import Branch

            self.org_id = Branch.objects.filter(pk=self.branch_id).values_list("org_id", flat=True).first()
        super().save(*args, **kwargs)
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=40, db_index=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    tax_id = models.CharField(max_length=40, blank=True, help_text="PAN/VAT number")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.phone})"


class CashierShift(models.Model):
    OPEN, CLOSED = "open", "closed"

    branch = models.ForeignKey("tenancy.Branch", on_delete=models.PROTECT)
    register = models.ForeignKey("tenancy.Register", on_delete=models.PROTECT)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=8, choices=[(OPEN, OPEN), (CLOSED, CLOSED)], default=OPEN)
    opened_at = models.DateTimeField(auto_now_add=True)
    opening_cash_paisa = models.BigIntegerField()
    closed_at = models.DateTimeField(null=True, blank=True)
    counted_cash_paisa = models.BigIntegerField(null=True, blank=True)
    expected_cash_paisa = models.BigIntegerField(null=True, blank=True)
    variance_paisa = models.BigIntegerField(null=True, blank=True)
    closed_offline = models.BooleanField(default=False)

    objects = BranchScopedQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["register"], condition=models.Q(status="open"), name="one_open_shift_per_register"
            )
        ]


PAYMENT_METHODS = [("cash", "Cash"), ("fonepay", "FonePay QR"), ("card", "Card"), ("wallet", "Wallet"),
                   ("bank", "Bank transfer")]
DIGITAL_METHODS = {"fonepay", "card", "wallet", "bank"}


class Sale(AppendOnlyModel):
    OPEN, PAID, VOIDED, PAYMENT_ORPHAN = "open", "paid", "voided", "payment_orphan"
    STATUSES = [(s, s) for s in (OPEN, PAID, VOIDED, PAYMENT_ORPHAN)]
    FULL, ABBREVIATED = "full", "abbreviated"

    # After issue only cancellation and review bookkeeping may change.
    MUTABLE_AFTER_ISSUE = ("status", "voided_at", "voided_by", "void_reason", "conflict_resolution",
                           "conflict_resolved_by", "conflict_resolved_at", "needs_review", "stock_conflict")

    branch = models.ForeignKey("tenancy.Branch", on_delete=models.PROTECT)
    register = models.ForeignKey("tenancy.Register", null=True, on_delete=models.PROTECT)
    shift = models.ForeignKey(CashierShift, null=True, blank=True, on_delete=models.PROTECT, related_name="sales")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales")
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT)
    status = models.CharField(max_length=16, choices=STATUSES, default=OPEN)

    # Rule 17/18: the invoice type and the buyer exactly as printed.
    invoice_type = models.CharField(max_length=12, blank=True,
                                    choices=[(FULL, "Tax invoice"), (ABBREVIATED, "Abbreviated tax invoice")])
    buyer_name = models.CharField(max_length=200, blank=True)
    buyer_address = models.CharField(max_length=255, blank=True)
    buyer_pan = models.CharField(max_length=9, blank=True)
    payment_method = models.CharField(max_length=10, blank=True, choices=PAYMENT_METHODS)

    subtotal_paisa = models.BigIntegerField(default=0)
    discount_total_paisa = models.BigIntegerField(default=0)
    tax_total_paisa = models.BigIntegerField(default=0)
    excise_total_paisa = models.BigIntegerField(default=0)
    hst_total_paisa = models.BigIntegerField(default=0)
    esf_total_paisa = models.BigIntegerField(default=0)
    digital_vat_discount_paisa = models.BigIntegerField(default=0)
    round_adjust_paisa = models.BigIntegerField(default=0)
    total_paisa = models.BigIntegerField(default=0)
    # CBMS buckets
    taxable_sales_vat_paisa = models.BigIntegerField(default=0)
    excisable_amount_paisa = models.BigIntegerField(default=0)
    taxable_sales_hst_paisa = models.BigIntegerField(default=0)
    amount_for_esf_paisa = models.BigIntegerField(default=0)
    tax_exempted_sales_paisa = models.BigIntegerField(default=0)
    zero_rated_sales_paisa = models.BigIntegerField(default=0)

    sale_discount_type = models.CharField(max_length=8, choices=DISCOUNT_TYPES, default=NONE)
    sale_discount_value = models.BigIntegerField(default=0, help_text="paisa, or basis points for percent")
    sale_discount_reason = models.CharField(max_length=255, blank=True)
    discount_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    tax_allocation_method = models.CharField(max_length=40, blank=True)

    opened_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    # Rule 17: transaction date and issue date are separate fields, each in AD and BS.
    transaction_at = models.DateTimeField(null=True, blank=True)
    transaction_bs = models.CharField(max_length=10, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_bs = models.CharField(max_length=10, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    void_reason = models.CharField(max_length=255, blank=True)
    offline = models.BooleanField(default=False)
    # Client-side timestamp for offline sales (when it actually happened).
    offline_created_at = models.DateTimeField(null=True, blank=True)
    # Branch-series mode: offline registers print a provisional slip; the tax
    # invoice number is issued by the server on sync.
    provisional_ref = models.CharField(max_length=40, blank=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    payment_ref = models.CharField(max_length=64, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    stock_conflict = models.BooleanField(default=False)
    conflict_resolution = models.CharField(max_length=16, blank=True, choices=[("accepted", "accepted"),
                                                                               ("refunded", "refunded")])
    conflict_resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                             on_delete=models.PROTECT, related_name="+")
    conflict_resolved_at = models.DateTimeField(null=True, blank=True)
    # Flagged for manager review after sync (e.g. offline manager-approved discount).
    needs_review = models.BooleanField(default=False)

    fiscal_year = models.CharField(max_length=9, blank=True)
    # "" for the per-branch series, else the register code.
    series = models.CharField(max_length=16, blank=True)
    receipt_seq = models.PositiveIntegerField(null=True, blank=True)
    receipt_number = models.CharField(max_length=64, blank=True, db_index=True)

    objects = BranchScopedQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["branch", "opened_at"]),
            models.Index(fields=["branch", "paid_at"]),
            models.Index(fields=["branch", "issued_at"]),
            models.Index(fields=["status", "expires_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "fiscal_year", "series", "receipt_seq"],
                condition=models.Q(receipt_seq__isnull=False),
                name="uniq_invoice_number_in_series",
            )
        ]

    def is_issued(self):
        return bool(self.receipt_number)

    @property
    def is_cancelled(self):
        return self.status == self.VOIDED and bool(self.receipt_number)


class SaleLine(AppendOnlyModel):
    MUTABLE_AFTER_ISSUE = ()

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    product_name = models.CharField(max_length=255)
    hs_code = models.CharField(max_length=16, blank=True)
    qty = models.IntegerField()
    unit_price_paisa = models.BigIntegerField()
    discount_type = models.CharField(max_length=8, choices=DISCOUNT_TYPES, default=NONE)
    discount_value = models.BigIntegerField(default=0, help_text="paisa, or basis points for percent")
    discount_reason = models.CharField(max_length=255, blank=True)
    line_discount_paisa = models.BigIntegerField(default=0)
    sale_discount_share_paisa = models.BigIntegerField(default=0)
    line_total_paisa = models.BigIntegerField()
    tax_category_code = models.CharField(max_length=16, blank=True)
    tax_kind = models.CharField(max_length=10, default="standard")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2)
    tax_inclusive = models.BooleanField()
    tax_amount_paisa = models.BigIntegerField()
    excise_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    excise_paisa = models.BigIntegerField(default=0)
    levy_type = models.CharField(max_length=4, blank=True)
    levy_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    levy_paisa = models.BigIntegerField(default=0)
    unit_cost_paisa = models.BigIntegerField(default=0, help_text="weighted-average snapshot for COGS")

    def is_issued(self):
        return Sale.objects.filter(pk=self.sale_id).exclude(receipt_number="").exists()


class Payment(AppendOnlyModel):
    CASH, FONEPAY, CARD, WALLET, BANK = "cash", "fonepay", "card", "wallet", "bank"
    INITIATED, SUCCESS, FAILED, EXPIRED = "initiated", "success", "failed", "expired"

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=16, choices=PAYMENT_METHODS)
    amount_paisa = models.BigIntegerField()
    tendered_paisa = models.BigIntegerField(default=0)
    change_paisa = models.BigIntegerField(default=0)
    ref = models.CharField(max_length=64, blank=True)
    fonepay_prn = models.CharField(max_length=64, null=True, blank=True, unique=True)
    fonepay_trx_id = models.CharField(max_length=64, blank=True)
    fonepay_paid_paisa = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, default=SUCCESS, choices=[(s, s) for s in (INITIATED, SUCCESS, FAILED,
                                                                                        EXPIRED)])
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    def is_issued(self):
        return Sale.objects.filter(pk=self.sale_id).exclude(receipt_number="").exists()


class Receipt(models.Model):
    """Print bookkeeping for an issued invoice (print once; reprints are copies)."""

    sale = models.OneToOneField(Sale, on_delete=models.PROTECT, related_name="receipt")
    number = models.CharField(max_length=64)
    format = models.CharField(max_length=16, default="thermal80")
    printed_at = models.DateTimeField(null=True, blank=True)
    print_count = models.PositiveIntegerField(default=0)


class PrintLog(AppendOnlyModel):
    """Every print of a tax document. The first 'original' print is the only one
    not stamped 'Copy of Original' (payment.md Step 2)."""

    ORIGINAL, COPY, DELIVERY = "original", "copy", "delivery"

    doc_type = models.CharField(max_length=12)  # invoice | credit_note | debit_note
    doc_id = models.PositiveIntegerField()
    document_number = models.CharField(max_length=64)
    kind = models.CharField(max_length=10, choices=[(ORIGINAL, ORIGINAL), (COPY, COPY), (DELIVERY, DELIVERY)])
    copy_no = models.PositiveIntegerField(default=0)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["doc_type", "doc_id"])]


class DocumentSequence(models.Model):
    """Gapless numbering for every tax document (§9.4, payment.md Step 2).
    register_key = 0 is the per-branch series; otherwise the register id.
    Allocated under SELECT ... FOR UPDATE in the document's transaction — never
    a DB sequence, which would burn numbers on rollback."""

    INVOICE, CREDIT_NOTE, DEBIT_NOTE, PURCHASE_RETURN = "invoice", "credit_note", "debit_note", "purchase_return"

    branch = models.ForeignKey("tenancy.Branch", on_delete=models.PROTECT)
    register_key = models.PositiveIntegerField(default=0)
    fiscal_year = models.CharField(max_length=9)
    doc_type = models.CharField(max_length=16)
    next_number = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = [("branch", "register_key", "fiscal_year", "doc_type")]


class CreditNote(AppendOnlyModel):
    branch = models.ForeignKey("tenancy.Branch", on_delete=models.PROTECT)
    register = models.ForeignKey("tenancy.Register", on_delete=models.PROTECT)
    original_sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="credit_notes")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    shift = models.ForeignKey(CashierShift, null=True, blank=True, on_delete=models.PROTECT,
                              related_name="credit_notes")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    total_paisa = models.BigIntegerField()
    taxable_paisa = models.BigIntegerField(default=0, help_text="value before VAT/excise/levies")
    tax_paisa = models.BigIntegerField(default=0)
    excise_paisa = models.BigIntegerField(default=0)
    levy_paisa = models.BigIntegerField(default=0)
    # CBMS buckets for /api/billreturn
    taxable_sales_vat_paisa = models.BigIntegerField(default=0)
    tax_exempted_sales_paisa = models.BigIntegerField(default=0)
    refund_method = models.CharField(max_length=16, default="cash")
    fiscal_year = models.CharField(max_length=9)
    series = models.CharField(max_length=16, blank=True)
    seq = models.PositiveIntegerField()
    number = models.CharField(max_length=64)
    at = models.DateTimeField(auto_now_add=True)
    issued_bs = models.CharField(max_length=10, blank=True)
    reason = models.CharField(max_length=255)

    objects = BranchScopedQuerySet.as_manager()

    class Meta:
        unique_together = [("branch", "fiscal_year", "series", "seq")]


class CreditNoteLine(AppendOnlyModel):
    credit_note = models.ForeignKey(CreditNote, on_delete=models.CASCADE, related_name="lines")
    original_sale_line = models.ForeignKey(SaleLine, on_delete=models.PROTECT, related_name="returns")
    qty = models.PositiveIntegerField()
    unit_price_paisa = models.BigIntegerField()
    line_total_paisa = models.BigIntegerField()
    tax_paisa = models.BigIntegerField(default=0)
    excise_paisa = models.BigIntegerField(default=0)
    levy_paisa = models.BigIntegerField(default=0)


class DebitNote(AppendOnlyModel):
    """Increases the amount of an issued invoice (undercharge). Corrections are
    only ever made by credit or debit notes linked to the original invoice."""

    branch = models.ForeignKey("tenancy.Branch", on_delete=models.PROTECT)
    original_sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="debit_notes")
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    taxable_paisa = models.BigIntegerField()
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2)
    tax_paisa = models.BigIntegerField()
    total_paisa = models.BigIntegerField()
    fiscal_year = models.CharField(max_length=9)
    seq = models.PositiveIntegerField()
    number = models.CharField(max_length=64)
    at = models.DateTimeField(auto_now_add=True)
    issued_bs = models.CharField(max_length=10, blank=True)
    reason = models.CharField(max_length=255)

    objects = BranchScopedQuerySet.as_manager()

    class Meta:
        unique_together = [("branch", "fiscal_year", "seq")]


class HeldCart(models.Model):
    """Hold / recall (§12). The cart is stored as submitted by the POS."""

    branch = models.ForeignKey("tenancy.Branch", on_delete=models.CASCADE)
    register = models.ForeignKey("tenancy.Register", on_delete=models.CASCADE)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    label = models.CharField(max_length=100, blank=True)
    cart = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = BranchScopedQuerySet.as_manager()


class FonepayMismatch(models.Model):
    """Daily reconciliation findings (§14)."""

    found_at = models.DateTimeField(auto_now_add=True)
    branch = models.ForeignKey("tenancy.Branch", null=True, on_delete=models.PROTECT)
    prn = models.CharField(max_length=64)
    kind = models.CharField(max_length=32)  # missing_local, missing_remote, amount_mismatch, status_mismatch
    detail = models.JSONField(default=dict)
    resolved = models.BooleanField(default=False)


class MockFonepayTransaction(models.Model):
    """Backing store for the mock FonePay gateway (dev/test only)."""

    prn = models.CharField(max_length=64, unique=True)
    amount_paisa = models.BigIntegerField()
    paid_paisa = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, default="pending")
    trx_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class FonepaySettings(models.Model):
    """Server-wide FonePay switch and API address (one row per organization).
    Per-branch credentials are in FonepayMerchant."""

    DISABLED, MOCK, LIVE = "disabled", "mock", "http"
    MODES = [(DISABLED, "Off"), (MOCK, "Demo (simulated payments)"), (LIVE, "Live")]

    org = models.OneToOneField("tenancy.Organization", on_delete=models.CASCADE, primary_key=True,
                               related_name="fonepay_settings")
    mode = models.CharField(max_length=8, choices=MODES, default=DISABLED)
    base_url = models.URLField(blank=True, help_text="FonePay merchant API address (https)")
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")


class FonepayMerchant(models.Model):
    """FonePay merchant account for one branch. Each branch settles into its
    own merchant account, so QR payments use that branch's credentials.
    The password and signing secret are stored encrypted (compliance.crypto)
    and are never sent back to the browser."""

    branch = models.OneToOneField("tenancy.Branch", on_delete=models.CASCADE, primary_key=True,
                                  related_name="fonepay_merchant")
    enabled = models.BooleanField(default=False)
    merchant_code = models.CharField(max_length=64, blank=True)
    username = models.CharField(max_length=128, blank=True)
    password_enc = models.TextField(blank=True)
    secret_enc = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    def __str__(self):
        return f"FonePay {self.merchant_code or '-'} @ branch {self.branch_id}"

    @property
    def password(self):
        from compliance import crypto

        return crypto.decrypt(self.password_enc)

    @password.setter
    def password(self, value):
        from compliance import crypto

        self.password_enc = crypto.encrypt(value)

    @property
    def secret(self):
        from compliance import crypto

        return crypto.decrypt(self.secret_enc)

    @secret.setter
    def secret(self, value):
        from compliance import crypto

        self.secret_enc = crypto.encrypt(value)

    def missing(self):
        """Fields still needed before live (http) FonePay can be switched on."""
        need = {"merchant code": self.merchant_code, "username": self.username,
                "password": self.password_enc, "secret key": self.secret_enc}
        return [name for name, value in need.items() if not value]

    def is_complete(self):
        return not self.missing()
