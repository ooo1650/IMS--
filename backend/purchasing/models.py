from django.conf import settings
from django.db import models

from compliance.appendonly import AppendOnlyModel
from tenancy.models import BranchScopedQuerySet


class PurchaseOrder(models.Model):
    """Tracks what's ordered and received — not what's owed (no payables, §3)."""

    DRAFT, ORDERED, PARTIAL, RECEIVED, CANCELLED = "draft", "ordered", "partial", "received", "cancelled"
    STATUSES = [(s, s) for s in (DRAFT, ORDERED, PARTIAL, RECEIVED, CANCELLED)]

    branch = models.ForeignKey("tenancy.Branch", on_delete=models.PROTECT)
    supplier = models.ForeignKey("catalog.Supplier", on_delete=models.PROTECT)
    status = models.CharField(max_length=16, choices=STATUSES, default=DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    ordered_at = models.DateTimeField(null=True, blank=True)
    expected_at = models.DateField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)

    objects = BranchScopedQuerySet.as_manager()


class PurchaseOrderLine(models.Model):
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    qty_ordered = models.PositiveIntegerField()
    qty_received = models.PositiveIntegerField(default=0)
    unit_cost_paisa = models.BigIntegerField(help_text="ordered cost (excl. VAT)")


class GoodsReceipt(AppendOnlyModel):
    """A supplier invoice received against a PO — one row in the purchase book
    (Schedule-8). Append-only once recorded."""

    po = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="receipts")
    received_at = models.DateTimeField(auto_now_add=True)
    received_bs = models.CharField(max_length=10, blank=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    supplier_invoice_ref = models.CharField(max_length=64, blank=True)
    supplier_invoice_date = models.DateField(null=True, blank=True)
    supplier_invoice_bs = models.CharField(max_length=10, blank=True)
    is_import = models.BooleanField(default=False)
    taxable_paisa = models.BigIntegerField(default=0)
    exempt_paisa = models.BigIntegerField(default=0)
    vat_paisa = models.BigIntegerField(default=0)
    claimable_vat_paisa = models.BigIntegerField(default=0, help_text="VAT less blocked input credit")


class GoodsReceiptLine(AppendOnlyModel):
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    po_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="receipt_lines")
    qty = models.PositiveIntegerField()
    # Actual cost from the supplier invoice (excl. VAT); this updates the weighted average.
    unit_cost_paisa = models.BigIntegerField()
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_kind = models.CharField(max_length=10, default="standard")
    vat_paisa = models.BigIntegerField(default=0)
    input_credit_blocked_pct = models.PositiveSmallIntegerField(default=0)


class PurchaseReturn(AppendOnlyModel):
    """Goods sent back to a supplier (purchase return register). Stock goes out
    at its current average cost; input VAT is reversed in the VAT working."""

    branch = models.ForeignKey("tenancy.Branch", on_delete=models.PROTECT)
    supplier = models.ForeignKey("catalog.Supplier", on_delete=models.PROTECT)
    goods_receipt = models.ForeignKey(GoodsReceipt, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="returns")
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    fiscal_year = models.CharField(max_length=9)
    seq = models.PositiveIntegerField()
    number = models.CharField(max_length=64)
    at = models.DateTimeField(auto_now_add=True)
    issued_bs = models.CharField(max_length=10, blank=True)
    supplier_note_ref = models.CharField(max_length=64, blank=True, help_text="supplier's credit note no.")
    reason = models.CharField(max_length=255)
    taxable_paisa = models.BigIntegerField(default=0)
    vat_paisa = models.BigIntegerField(default=0)
    claimable_vat_paisa = models.BigIntegerField(default=0)

    objects = BranchScopedQuerySet.as_manager()

    class Meta:
        unique_together = [("branch", "fiscal_year", "seq")]


class PurchaseReturnLine(AppendOnlyModel):
    purchase_return = models.ForeignKey(PurchaseReturn, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    qty = models.PositiveIntegerField()
    unit_cost_paisa = models.BigIntegerField()
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    vat_paisa = models.BigIntegerField(default=0)
