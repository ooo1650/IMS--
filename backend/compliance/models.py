"""Configurable tax rules, IRD/CBMS settings and the CBMS outbox (payment.md
Steps 3, 4, 8). Rates, thresholds and templates live here — a law change is a
settings update, not a release."""
from datetime import date
from decimal import Decimal

from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone

from . import crypto

CRORE = 100 * 10 ** 7    # paisa
LAKH = 100 * 10 ** 5     # paisa


class TaxCategory(models.Model):
    STANDARD, REDUCED, ZERO, EXEMPT = "standard", "reduced", "zero", "exempt"
    KINDS = [(STANDARD, "Standard rate"), (REDUCED, "Reduced rate"), (ZERO, "Zero-rated (keeps input credit)"),
             (EXEMPT, "Exempt (no input credit)")]
    LEVIES = [("", "None"), ("hst", "Health Service Tax"), ("esf", "Education Service Fee")]

    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=10, choices=KINDS)
    levy_type = models.CharField(max_length=4, choices=LEVIES, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "tax categories"

    def __str__(self):
        return f"{self.code} — {self.name}"

    def rate_on(self, d: date | None = None):
        """The rate row in force on ``d`` (effective-dated, newest first)."""
        d = d or timezone.localdate()
        rates = getattr(self, "_rates_cache", None)
        if rates is None:
            rates = self._rates_cache = list(self.rates.order_by("-effective_from"))
        for r in rates:
            if r.effective_from <= d:
                return r
        return None


class TaxCategoryRate(models.Model):
    category = models.ForeignKey(TaxCategory, on_delete=models.CASCADE, related_name="rates")
    effective_from = models.DateField()
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"), validators=[MinValueValidator(0), MaxValueValidator(100)])
    levy_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"), validators=[MinValueValidator(0), MaxValueValidator(100)])
    note = models.CharField(max_length=200, blank=True, help_text="e.g. Finance Act 2083 §…")

    class Meta:
        unique_together = [("category", "effective_from")]
        ordering = ["category", "-effective_from"]


class ComplianceSettings(models.Model):
    """One row per organization. Defaults follow payment.md as of FY 2083/84."""

    BRANCH, REGISTER = "branch", "register"

    org = models.OneToOneField("tenancy.Organization", on_delete=models.CASCADE, related_name="compliance")
    # Invoice numbering: payment.md Step 2 asks for a per-branch, per-FY series.
    invoice_series = models.CharField(max_length=8, default=BRANCH,
                                      choices=[(BRANCH, "Per branch + fiscal year"),
                                               (REGISTER, "Per register + fiscal year")])
    abbreviated_invoice_limit_paisa = models.BigIntegerField(default=10_000 * 100, validators=[MinValueValidator(0), MaxValueValidator(10 ** 13)])
    delivery_copy_limit_paisa = models.BigIntegerField(default=10_000 * 100, validators=[MinValueValidator(0), MaxValueValidator(10 ** 13)])
    require_buyer_pan_over_limit = models.BooleanField(default=False)

    # Digital-payment VAT discount — OFF until IRD publishes the procedure.
    digital_vat_discount_enabled = models.BooleanField(default=False)
    digital_vat_discount_bp = models.PositiveIntegerField(default=1000, validators=[MaxValueValidator(10000)], help_text="share of VAT, basis points")
    digital_vat_discount_methods = models.JSONField(default=list, blank=True)

    # CBMS
    cbms_enabled = models.BooleanField(default=False)
    cbms_base_url = models.CharField(max_length=200, default="https://cbapi.ird.gov.np",
                                     help_text='Use "mock://" for the built-in test double')
    cbms_username = models.CharField(max_length=100, blank=True)
    cbms_password_enc = models.TextField(blank=True)
    cbms_realtime_window_minutes = models.PositiveIntegerField(default=10, validators=[MinValueValidator(1), MaxValueValidator(1440)])
    cbms_paused_reason = models.CharField(max_length=255, blank=True)
    cbms_threshold_paisa = models.BigIntegerField(default=10 * CRORE, validators=[MinValueValidator(0), MaxValueValidator(10 ** 13)], help_text="warn: CBMS line (budget)")
    cbms_mandatory_threshold_paisa = models.BigIntegerField(default=20 * CRORE, validators=[MinValueValidator(0), MaxValueValidator(10 ** 13)], help_text="CBMS notice 2083/01/04")

    # VAT registration thresholds (for non-registered sellers' turnover alert)
    vat_threshold_goods_paisa = models.BigIntegerField(default=50 * LAKH, validators=[MinValueValidator(0), MaxValueValidator(10 ** 13)])
    vat_threshold_services_paisa = models.BigIntegerField(default=30 * LAKH, validators=[MinValueValidator(0), MaxValueValidator(10 ** 13)])
    excess_stock_fine_bp = models.PositiveIntegerField(default=5000, validators=[MaxValueValidator(10000)], help_text="fine on excess stock, basis points")

    updated_at = models.DateTimeField(auto_now=True)

    @property
    def cbms_password(self):
        return crypto.decrypt(self.cbms_password_enc)

    @cbms_password.setter
    def cbms_password(self, value):
        self.cbms_password_enc = crypto.encrypt(value)


def get_settings(org) -> ComplianceSettings:
    """The business's tax settings (PAN, invoice series, CBMS login...). Every
    caller names the business; there is no "first organization" fallback, so a
    sale can never be numbered or reported under another business's settings."""
    if org is None:
        raise RuntimeError("get_settings() needs the business (org)")
    obj, _ = ComplianceSettings.objects.get_or_create(org=org)
    return obj


class CbmsPost(models.Model):
    """Outbox: written in the same transaction as the bill/credit note, posted
    asynchronously by ``manage.py cbms_sync`` with retries (payment.md Step 4)."""

    BILL, RETURN = "bill", "billreturn"
    PENDING, SYNCED, FAILED = "pending", "synced", "failed"

    key = models.CharField(max_length=64, unique=True)  # e.g. "bill:123", "billreturn:cn:45"
    doc_type = models.CharField(max_length=12, choices=[(BILL, BILL), (RETURN, RETURN)])
    branch = models.ForeignKey("tenancy.Branch", on_delete=models.PROTECT)
    sale = models.ForeignKey("sales.Sale", null=True, blank=True, on_delete=models.PROTECT, related_name="cbms_posts")
    credit_note = models.ForeignKey("sales.CreditNote", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="cbms_posts")
    document_number = models.CharField(max_length=64)
    issued_at = models.DateTimeField()
    payload = models.JSONField()  # without credentials
    status = models.CharField(max_length=8, default=PENDING,
                              choices=[(PENDING, PENDING), (SYNCED, SYNCED), (FAILED, FAILED)])
    attempts = models.PositiveIntegerField(default=0)
    last_code = models.CharField(max_length=8, blank=True)
    last_error = models.TextField(blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    isrealtime = models.BooleanField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"])]
        ordering = ["-created_at"]


class MockCbmsRecord(models.Model):
    """What the ``mock://`` CBMS double received (dev/test/listing evidence)."""

    endpoint = models.CharField(max_length=16)
    document_number = models.CharField(max_length=64)
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
