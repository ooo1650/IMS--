"""Stock mutations. Every change to quantity_on_hand goes through here so that
it always comes with a StockMovement, and rows are always locked in
product_id order (§10.4) to avoid deadlocks between concurrent writers.

All functions must be called inside ``transaction.atomic()``.
"""
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from catalog.models import Product

from .models import InventoryItem, StockMovement, StockTransfer, StockTransferLine, Stocktake, StocktakeLine


def lock_items(branch, product_ids):
    """Create missing InventoryItems, then SELECT ... FOR UPDATE in sorted order.

    Returns {product_id: InventoryItem}.
    """
    ids = sorted(set(product_ids))
    existing = set(
        InventoryItem.objects.filter(branch=branch, product_id__in=ids).values_list("product_id", flat=True)
    )
    missing = [pid for pid in ids if pid not in existing]
    if missing:
        defaults = dict(Product.objects.filter(id__in=missing).values_list("id", "cost_price_paisa"))
        InventoryItem.objects.bulk_create(
            [InventoryItem(branch=branch, product_id=pid, weighted_avg_cost_paisa=defaults.get(pid, 0)) for pid in missing],
            ignore_conflicts=True,
        )
    items = InventoryItem.objects.select_for_update().filter(branch=branch, product_id__in=ids).order_by("product_id")
    return {item.product_id: item for item in items}


def weighted_average(on_hand, current_avg, received_qty, received_cost):
    """§9.6. When on-hand is zero or negative the new cost is the received cost."""
    if received_qty <= 0:
        return current_avg
    if on_hand <= 0:
        return received_cost
    total = on_hand * current_avg + received_qty * received_cost
    # Round half up to the nearest paisa.
    return (2 * total + (on_hand + received_qty)) // (2 * (on_hand + received_qty))


def _move(item, mtype, qty, unit_cost, by, ref="", ref_type="", reason=""):
    item.quantity_on_hand += qty
    return StockMovement(
        branch_id=item.branch_id, product_id=item.product_id, type=mtype, qty=qty,
        unit_cost_paisa=unit_cost, by=by, ref=str(ref), ref_type=ref_type, reason=reason,
    )


@dataclass
class Receipt:
    product_id: int
    qty: int
    unit_cost_paisa: int


def receive_stock(branch, receipts, mtype, by, ref="", ref_type="", reason=""):
    """Stock coming in at a cost (purchase_in, transfer_in, opening stock).
    Recomputes weighted-average cost per item."""
    items = lock_items(branch, [r.product_id for r in receipts])
    movements = []
    for r in sorted(receipts, key=lambda r: r.product_id):
        item = items[r.product_id]
        item.weighted_avg_cost_paisa = weighted_average(
            item.quantity_on_hand, item.weighted_avg_cost_paisa, r.qty, r.unit_cost_paisa
        )
        movements.append(_move(item, mtype, r.qty, r.unit_cost_paisa, by, ref, ref_type, reason))
    InventoryItem.objects.bulk_update(items.values(), ["quantity_on_hand", "weighted_avg_cost_paisa"])
    StockMovement.objects.bulk_create(movements)
    return items


def adjust_stock(branch, product_id, delta, by, reason, mtype=None, ref="", ref_type="adjustment"):
    """Manual in/out adjustment at the current weighted-average cost."""
    if delta == 0:
        raise ValidationError("Adjustment quantity cannot be zero")
    if not reason:
        raise ValidationError("A reason is required for stock adjustments")
    item = lock_items(branch, [product_id])[product_id]
    mtype = mtype or (StockMovement.ADJUST_IN if delta > 0 else StockMovement.ADJUST_OUT)
    mv = _move(item, mtype, delta, item.weighted_avg_cost_paisa, by, ref, ref_type, reason)
    item.save(update_fields=["quantity_on_hand"])
    mv.save()
    return item


# --- Transfers (§9.9) ---------------------------------------------------------

def dispatch_transfer(transfer, by):
    if transfer.status != StockTransfer.DRAFT:
        raise ValidationError("Only draft transfers can be dispatched")
    lines = list(transfer.lines.all())
    if not lines:
        raise ValidationError("Transfer has no lines")
    items = lock_items(transfer.from_branch, [l.product_id for l in lines])
    movements = []
    for line in sorted(lines, key=lambda l: l.product_id):
        item = items[line.product_id]
        if item.quantity_on_hand < line.qty_sent and not item.allow_negative:
            raise ValidationError(f"Insufficient stock for product {line.product_id} at {transfer.from_branch.code}")
        line.unit_cost_paisa = item.weighted_avg_cost_paisa
        movements.append(_move(item, StockMovement.TRANSFER_OUT, -line.qty_sent, line.unit_cost_paisa, by,
                               transfer.id, "transfer"))
    InventoryItem.objects.bulk_update(items.values(), ["quantity_on_hand"])
    StockMovement.objects.bulk_create(movements)
    StockTransferLine.objects.bulk_update(lines, ["unit_cost_paisa"])
    transfer.status = StockTransfer.IN_TRANSIT
    transfer.dispatched_at = timezone.now()
    transfer.save(update_fields=["status", "dispatched_at"])


def receive_transfer(transfer, received, by):
    """``received``: {line_id: (qty_received, variance_reason)}.

    The receiving branch books the full qty_sent as transfer_in (cost travels
    with it), then writes the shortfall as adjust_out "transfer loss".
    """
    if transfer.status != StockTransfer.IN_TRANSIT:
        raise ValidationError("Only in-transit transfers can be received")
    lines = list(transfer.lines.all())
    for line in lines:
        qty, reason = received.get(line.id, (line.qty_sent, ""))
        if qty < 0 or qty > line.qty_sent:
            raise ValidationError(f"Received qty for line {line.id} must be between 0 and {line.qty_sent}")
        if qty < line.qty_sent and not reason:
            raise ValidationError(f"Line {line.id}: a variance reason is required for a short receipt")
        line.qty_received, line.variance_reason = qty, reason or ""
    receive_stock(
        transfer.to_branch,
        [Receipt(l.product_id, l.qty_sent, l.unit_cost_paisa) for l in lines],
        StockMovement.TRANSFER_IN, by, transfer.id, "transfer",
    )
    shortfalls = [l for l in lines if l.qty_received < l.qty_sent]
    if shortfalls:
        items = lock_items(transfer.to_branch, [l.product_id for l in shortfalls])
        movements = [
            _move(items[l.product_id], StockMovement.ADJUST_OUT, l.qty_received - l.qty_sent, l.unit_cost_paisa,
                  by, transfer.id, "transfer", f"transfer loss: {l.variance_reason}")
            for l in sorted(shortfalls, key=lambda l: l.product_id)
        ]
        InventoryItem.objects.bulk_update(items.values(), ["quantity_on_hand"])
        StockMovement.objects.bulk_create(movements)
    StockTransferLine.objects.bulk_update(lines, ["qty_received", "variance_reason"])
    transfer.status = StockTransfer.RECEIVED
    transfer.received_at = timezone.now()
    transfer.received_by = by
    transfer.save(update_fields=["status", "received_at", "received_by"])


def cancel_transfer(transfer, by):
    if transfer.status == StockTransfer.DRAFT:
        pass
    elif transfer.status == StockTransfer.IN_TRANSIT:
        # Compensating movement: stock returns to the sending branch at its cost.
        receive_stock(
            transfer.from_branch,
            [Receipt(l.product_id, l.qty_sent, l.unit_cost_paisa) for l in transfer.lines.all()],
            StockMovement.TRANSFER_IN, by, transfer.id, "transfer", "transfer cancelled",
        )
    else:
        raise ValidationError(f"Cannot cancel a {transfer.status} transfer")
    transfer.status = StockTransfer.CANCELLED
    transfer.save(update_fields=["status"])


# --- Stocktakes (§9.8) --------------------------------------------------------

def start_stocktake(branch, by, category=None):
    products = Product.objects.active()
    scope = {}
    if category is not None:
        products = products.filter(category_id__in=category.descendant_ids())
        scope = {"category_id": category.id, "category_name": category.name}
    stocktake = Stocktake.objects.create(branch=branch, by=by, scope=scope, status=Stocktake.COUNTING)
    on_hand = dict(
        InventoryItem.objects.filter(branch=branch, product__in=products).values_list("product_id", "quantity_on_hand")
    )
    StocktakeLine.objects.bulk_create(
        [StocktakeLine(stocktake=stocktake, product_id=pid, system_qty=on_hand.get(pid, 0))
         for pid in products.values_list("id", flat=True)]
    )
    return stocktake


def finish_stocktake(stocktake, by):
    """Writes stocktake_adjust movements for counted lines with a variance.
    Variance is measured against the snapshot taken at start (count freeze)."""
    if stocktake.status != Stocktake.COUNTING:
        raise ValidationError("Stocktake is not in counting state")
    threshold = settings.IMS["STOCKTAKE_VARIANCE_REASON_THRESHOLD"]
    counted = [l for l in stocktake.lines.select_related("product") if l.counted_qty is not None]
    for line in counted:
        line.variance = line.counted_qty - line.system_qty
        if abs(line.variance) > threshold and not line.reason:
            raise ValidationError(f"{line.product.sku} ({line.product.name}): variance {line.variance} needs a reason")
    with_variance = [l for l in counted if l.variance]
    if with_variance:
        items = lock_items(stocktake.branch, [l.product_id for l in with_variance])
        movements = [
            _move(items[l.product_id], StockMovement.STOCKTAKE_ADJUST, l.variance,
                  items[l.product_id].weighted_avg_cost_paisa, by, stocktake.id, "stocktake", l.reason)
            for l in sorted(with_variance, key=lambda l: l.product_id)
        ]
        InventoryItem.objects.bulk_update(items.values(), ["quantity_on_hand"])
        StockMovement.objects.bulk_create(movements)
    StocktakeLine.objects.bulk_update(counted, ["variance"])
    stocktake.status = Stocktake.FINISHED
    stocktake.finished_at = timezone.now()
    stocktake.save(update_fields=["status", "finished_at"])
