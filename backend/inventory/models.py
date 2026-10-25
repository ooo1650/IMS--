from django.conf import settings
from django.db import models
from django.db.models import Q

from tenancy.models import BranchScopedQuerySet


class InventoryItem(models.Model):
    branch = models.ForeignKey("tenancy.Branch", on_delete=models.PROTECT, related_name="inventory")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="inventory")
    # Cached; reconciled from StockMovement (see reconcile_stock command).
    quantity_on_hand = models.IntegerField(default=0)
    reorder_point = models.IntegerField(default=0)
    reorder_qty = models.IntegerField(default=0)
    weighted_avg_cost_paisa = models.BigIntegerField(default=0)
    allow_negative = models.BooleanField(default=False)

    objects = BranchScopedQuerySet.as_manager()

    class Meta:
        unique_together = [("branch", "product")]
        indexes = [models.Index(fields=["branch", "product"])]

    @property
    def is_low(self):
        return self.quantity_on_hand <= self.reorder_point


class StockMovement(models.Model):
    PURCHASE_IN = "purchase_in"
    SALE_OUT = "sale_out"
    RETURN_IN = "return_in"
    ADJUST_IN = "adjust_in"
    ADJUST_OUT = "adjust_out"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    STOCKTAKE_ADJUST = "stocktake_adjust"
    PURCHASE_RETURN_OUT = "purchase_return_out"
    TYPES = [(t, t) for t in (
        PURCHASE_IN, SALE_OUT, RETURN_IN, ADJUST_IN, ADJUST_OUT, TRANSFER_OUT, TRANSFER_IN, STOCKTAKE_ADJUST,
        PURCHASE_RETURN_OUT,
    )]

    branch = models.ForeignKey("tenancy.Branch", on_delete=models.PROTECT)
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    type = models.CharField(max_length=24, choices=TYPES)
    # Signed: positive adds stock, negative removes it.
    qty = models.IntegerField()
    unit_cost_paisa = models.BigIntegerField(default=0)
    ref = models.CharField(max_length=64, blank=True)
    ref_type = models.CharField(max_length=32, blank=True)
    at = models.DateTimeField(auto_now_add=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    reason = models.CharField(max_length=255, blank=True)

    objects = BranchScopedQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["branch", "product"]),
            models.Index(fields=["branch", "at"]),
        ]


class TransferQuerySet(models.QuerySet):
    def visible_to(self, branch):
        """A transfer is visible to both ends (§6)."""
        if branch is None:
            return self.none()
        return self.filter(Q(from_branch=branch) | Q(to_branch=branch))


class StockTransfer(models.Model):
    DRAFT, IN_TRANSIT, RECEIVED, CANCELLED = "draft", "in_transit", "received", "cancelled"
    STATUSES = [(s, s) for s in (DRAFT, IN_TRANSIT, RECEIVED, CANCELLED)]

    from_branch = models.ForeignKey("tenancy.Branch", on_delete=models.PROTECT, related_name="transfers_out")
    to_branch = models.ForeignKey("tenancy.Branch", on_delete=models.PROTECT, related_name="transfers_in")
    status = models.CharField(max_length=16, choices=STATUSES, default=DRAFT)
    started_at = models.DateTimeField(auto_now_add=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    dispatched_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    note = models.TextField(blank=True)

    objects = TransferQuerySet.as_manager()


class StockTransferLine(models.Model):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    qty_sent = models.PositiveIntegerField()
    qty_received = models.PositiveIntegerField(null=True, blank=True)
    # Weighted-average cost at dispatch; cost travels with the stock.
    unit_cost_paisa = models.BigIntegerField(default=0)
    variance_reason = models.CharField(max_length=255, blank=True)


class Stocktake(models.Model):
    DRAFT, COUNTING, FINISHED = "draft", "counting", "finished"
    STATUSES = [(s, s) for s in (DRAFT, COUNTING, FINISHED)]

    branch = models.ForeignKey("tenancy.Branch", on_delete=models.PROTECT)
    status = models.CharField(max_length=16, choices=STATUSES, default=DRAFT)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    # Partial stocktake: {"category_id": 3} or {} for full.
    scope = models.JSONField(default=dict, blank=True)

    objects = BranchScopedQuerySet.as_manager()


class StocktakeLine(models.Model):
    stocktake = models.ForeignKey(Stocktake, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    system_qty = models.IntegerField()
    counted_qty = models.IntegerField(null=True, blank=True)
    variance = models.IntegerField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = [("stocktake", "product")]
