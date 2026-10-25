import zoneinfo

from django.core.exceptions import ValidationError
from django.db import models


def validate_timezone(value):
    try:
        zoneinfo.ZoneInfo(value)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise ValidationError(f"{value!r} is not a valid IANA timezone")


class Organization(models.Model):
    """The seller printed on every tax invoice (Rule 17)."""

    GOODS, SERVICES, MIXED = "goods", "services", "mixed"

    name = models.CharField(max_length=200)
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
