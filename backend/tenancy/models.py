import zoneinfo

from django.core.exceptions import ValidationError
from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator


def validate_timezone(value):
    try:
        zoneinfo.ZoneInfo(value)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise ValidationError(f"{value!r} is not a valid IANA timezone")


class Organization(models.Model):
    """A separately registered business (its own PAN, catalog, invoices and
    reports) and the seller printed on its tax invoices (Rule 17). One owner may
    run several, e.g. a shoe shop and a clothing shop; each has its own branches."""

    GOODS, SERVICES, MIXED = "goods", "services", "mixed"

    name = models.CharField(max_length=200)
    # Short unique handle used in the console and URLs, e.g. "himalaya-shoes".
    code = models.SlugField(max_length=40, unique=True, null=True, blank=True)
    trade = models.CharField(max_length=100, blank=True, help_text="What it sells, e.g. Footwear")
    is_active = models.BooleanField(default=True)
    name_np = models.CharField(max_length=200, blank=True, help_text="Name in Nepali for the invoice face")
    pan = models.CharField(max_length=9, blank=True, help_text="Seller PAN/VAT number (9 digits)")
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    vat_registered = models.BooleanField(default=True)
    business_type = models.CharField(max_length=8, default=GOODS,
                                     choices=[(GOODS, "Goods"), (SERVICES, "Services"), (MIXED, "Mixed")])

    def __str__(self):
        return self.name


class BusinessRules(models.Model):
    """Per-business shop rules (formerly IMS_* environment variables)."""

    org = models.OneToOneField(Organization, on_delete=models.CASCADE, primary_key=True, related_name="rules")
    # Cash totals round to this many paisa (100 = nearest rupee); 1 = no rounding.
    cash_rounding_paisa = models.PositiveIntegerField(default=1)
    # Cashiers may discount up to this share of the gross (basis points) without approval.
    cashier_max_discount_bp = models.PositiveIntegerField(default=500, validators=[MaxValueValidator(10000)])
    # Returns with a total above this (paisa) need manager approval.
    return_approval_threshold_paisa = models.BigIntegerField(default=500000, validators=[MinValueValidator(0), MaxValueValidator(10 ** 13)])
    # Stocktake variances (absolute units) above this require a reason.
    stocktake_variance_threshold = models.PositiveIntegerField(default=5)
    updated_at = models.DateTimeField(auto_now=True)


def rules_for(org) -> "BusinessRules":
    """The business's rules, created with defaults on first use."""
    rules, _ = BusinessRules.objects.get_or_create(org_id=org.pk if hasattr(org, "pk") else org)
    return rules


class Branch(models.Model):
    org = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="branches")
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=16, unique=True)
    timezone = models.CharField(max_length=64, default="Asia/Kathmandu", validators=[validate_timezone])
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} — {self.name}"

    @property
    def tz(self):
        return zoneinfo.ZoneInfo(self.timezone)


class Register(models.Model):
    """A physical till. Each register owns its own gapless receipt series (§9.4)."""

    PRINTER_DRIVERS = [
        ("browser", "Browser print dialog"),
        ("network_escpos", "Network ESC/POS"),
        ("usb_bridge", "USB thermal via local bridge"),
    ]

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="registers")
    code = models.CharField(max_length=16)
    name = models.CharField(max_length=100)
    printer_driver = models.CharField(max_length=32, choices=PRINTER_DRIVERS, default="browser")
    # e.g. {"url": "http://192.168.1.50:8080/print"} for network/usb bridges.
    printer_config = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("branch", "code")]
        ordering = ["branch_id", "code"]

    def __str__(self):
        return f"{self.branch.code}-{self.code}"


class BranchScopedQuerySet(models.QuerySet):
    """Querysets for tenant-scoped tables. Views call ``for_branch`` with the
    request's active branch; only org-level reporting uses the unfiltered set."""

    branch_field = "branch"

    def for_branch(self, branch):
        if branch is None:
            return self.none()
        return self.filter(**{self.branch_field: branch})


BranchScopedManager = models.Manager.from_queryset(BranchScopedQuerySet)
