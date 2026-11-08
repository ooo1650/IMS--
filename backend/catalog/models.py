from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Category(models.Model):
    org = models.ForeignKey("tenancy.Organization", on_delete=models.PROTECT, related_name="categories")
    name = models.CharField(max_length=200)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name

    def descendant_ids(self):
        ids, frontier = {self.id}, [self.id]
        while frontier:
            frontier = list(Category.objects.filter(parent_id__in=frontier).values_list("id", flat=True))
            ids.update(frontier)
        return ids


class Supplier(models.Model):
    org = models.ForeignKey("tenancy.Organization", on_delete=models.PROTECT, related_name="suppliers")
    name = models.CharField(max_length=200)
    pan = models.CharField(max_length=9, blank=True, help_text="Supplier PAN/VAT (purchase book)")
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ActiveProductQuerySet(models.QuerySet):
    def active(self):
        return self.filter(archived_at__isnull=True, is_active=True)


class Product(models.Model):
    """Soft-deleted via ``archived_at``; never hard-deleted (§9.1).

    Money fields are integer paisa. ``cost_price_paisa`` is only the purchase
    default — real cost lives in InventoryItem.weighted_avg_cost_paisa.
    """

    org = models.ForeignKey("tenancy.Organization", on_delete=models.PROTECT, related_name="products")
    sku = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.PROTECT)
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.PROTECT)
    unit = models.CharField(max_length=16, default="pcs")
    cost_price_paisa = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])
    sale_price_paisa = models.BigIntegerField(validators=[MinValueValidator(0)])
    # Percent, e.g. 13.00 for Nepal VAT. A rate, not money, so Decimal is fine.
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("13.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    tax_inclusive = models.BooleanField(default=True)
    # Configurable tax rules (payment.md Step 3). tax_rate above is a cache of the
    # category's current VAT rate; checkout resolves the rate in force on the day.
    tax_category = models.ForeignKey("compliance.TaxCategory", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="products")
    hs_code = models.CharField(max_length=16, blank=True, help_text="Harmonized System code for tax invoices")
    excise_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"),
                                      validators=[MinValueValidator(0), MaxValueValidator(500)])
    # Input VAT on purchases of this item that cannot be claimed (petrol,
    # alcohol/beverages, entertainment = 100; vehicles = 60).
    input_credit_blocked_pct = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    # Content hash naming the photo files under MEDIA_ROOT/products/ (catalog/images.py); "" = no photo.
    image_key = models.CharField(max_length=64, blank=True, default="")
    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ActiveProductQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        # SKUs are unique within a business; two businesses may reuse one.
        constraints = [models.UniqueConstraint(fields=["org", "sku"], name="uniq_product_sku_per_org")]

    def __str__(self):
        return f"{self.sku} {self.name}"


class Barcode(models.Model):
    TYPES = [("ean13", "EAN-13"), ("ean8", "EAN-8"), ("upca", "UPC-A"), ("code128", "Code 128"), ("other", "Other")]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="barcodes")
    # Copied from the product so a barcode is unique within a business (a scan
    # finds one product), while two businesses may stock the same item.
    org = models.ForeignKey("tenancy.Organization", on_delete=models.CASCADE, related_name="+")
    code = models.CharField(max_length=64)
    type = models.CharField(max_length=16, choices=TYPES, default="ean13")
    # Scanning this barcode rings up pack_qty units (e.g. a case of 24).
    pack_qty = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["org", "code"], name="uniq_barcode_per_org")]

    def save(self, *args, **kwargs):
        if self.product_id and not self.org_id:
            self.org_id = Product.objects.filter(pk=self.product_id).values_list("org_id", flat=True).first()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class BranchPrice(models.Model):
    branch = models.ForeignKey("tenancy.Branch", on_delete=models.CASCADE, related_name="prices")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="branch_prices")
    sale_price_paisa = models.BigIntegerField(validators=[MinValueValidator(0)])

    class Meta:
        unique_together = [("branch", "product")]


def effective_prices(branch, product_ids):
    """{product_id: sale_price_paisa} honouring BranchPrice overrides."""
    prices = dict(Product.objects.filter(id__in=product_ids).values_list("id", "sale_price_paisa"))
    overrides = BranchPrice.objects.filter(branch=branch, product_id__in=product_ids)
    prices.update({o.product_id: o.sale_price_paisa for o in overrides})
    return prices


def tax_profile(product, on_date=None):
    """(vat_rate, kind, levy_type, levy_rate, category_code) in force on ``on_date``.
    Products without a category fall back to their cached tax_rate."""
    cat = product.tax_category
    if cat is not None:
        r = cat.rate_on(on_date)
        if r is not None:
            return r.vat_rate, cat.kind, cat.levy_type, r.levy_rate, cat.code
    kind = "standard" if product.tax_rate > 0 else "exempt"
    return product.tax_rate, kind, "", Decimal("0"), ""
