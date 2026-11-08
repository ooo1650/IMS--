"""Dashboard queries (§17). Plain aggregates over the (branch, created_at)
indexes. "Today" is the branch's local day (§18). If a tile gets slow, add a
nightly summary table rather than a cache."""
from datetime import datetime, time, timedelta

from django.db.models import Count, ExpressionWrapper, F, IntegerField, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from inventory.models import InventoryItem
from purchasing.models import PurchaseOrder
from sales.models import FonepayMismatch, Sale, SaleLine


def local_day_start(branch, days_ago=0):
    tz = branch.tz
    today = timezone.localtime(timezone.now(), tz).date() - timedelta(days=days_ago)
    return datetime.combine(today, time.min, tzinfo=tz)


def _sales_since(branch, since):
    agg = Sale.objects.filter(branch=branch, status=Sale.PAID, paid_at__gte=since).aggregate(
        total=Sum("total_paisa"), count=Count("id"))
    return {"total_paisa": agg["total"] or 0, "count": agg["count"]}


def stock_summary(items):
    """Stock health of an InventoryItem queryset; shared by dashboards and the inventory screen."""
    value = items.filter(quantity_on_hand__gt=0).aggregate(
        v=Sum(ExpressionWrapper(F("quantity_on_hand") * F("weighted_avg_cost_paisa"), output_field=IntegerField()))
    )["v"] or 0
    return {
        "inventory_value_paisa": value,
        "low_stock": items.filter(quantity_on_hand__lte=F("reorder_point"), reorder_point__gt=0).count(),
        "out_of_stock": items.filter(quantity_on_hand__lte=0).count(),
        "negative_stock": items.filter(quantity_on_hand__lt=0).count(),
    }


def branch_summary(branch):
    today = local_day_start(branch)
    week = local_day_start(branch, today.weekday())
    month = today.replace(day=1)
    return {
        "branch_id": branch.id, "code": branch.code, "name": branch.name, "timezone": branch.timezone,
        "today": _sales_since(branch, today),
        "week": _sales_since(branch, week),
        "month": _sales_since(branch, month),
        **stock_summary(InventoryItem.objects.filter(branch=branch)),
        "stock_conflicts": Sale.objects.filter(branch=branch, stock_conflict=True, conflict_resolution="").count(),
        "needs_review": Sale.objects.filter(branch=branch).filter(
            Q(needs_review=True) | Q(status=Sale.PAYMENT_ORPHAN)).count(),
        "pending_pos": PurchaseOrder.objects.filter(
            branch=branch, status__in=[PurchaseOrder.ORDERED, PurchaseOrder.PARTIAL]).count(),
    }


def daily_trend(branches, days=14):
    """[{date, <branch code>: total_paisa, ...}] bucketed by each branch's local date."""
    series = {}
    for b in branches:
        since = local_day_start(b, days - 1)
        rows = (Sale.objects.filter(branch=b, status=Sale.PAID, paid_at__gte=since)
                .annotate(d=TruncDate("paid_at", tzinfo=b.tz)).values("d").annotate(t=Sum("total_paisa")))
        for r in rows:
            series.setdefault(r["d"].isoformat(), {})[b.code] = r["t"]
    start = timezone.localdate() - timedelta(days=days - 1)
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        out.append({"date": d, **{b.code: series.get(d, {}).get(b.code, 0) for b in branches}})
    return out


def top_products(branches, days=30, limit=10):
    since = timezone.now() - timedelta(days=days)
    return list(
        SaleLine.objects.filter(sale__branch__in=branches, sale__status=Sale.PAID, sale__paid_at__gte=since)
        .values("product_id", "product__sku", "product__name")
        .annotate(qty=Sum("qty"), revenue_paisa=Sum("line_total_paisa"))
        .order_by("-revenue_paisa")[:limit]
    )


def slow_movers(branches, days=30, limit=20):
    """In stock but not sold in ``days``."""
    since = timezone.now() - timedelta(days=days)
    sold = SaleLine.objects.filter(sale__branch__in=branches, sale__status=Sale.PAID, sale__paid_at__gte=since)
    sold_pairs = set(sold.values_list("sale__branch_id", "product_id").distinct())
    items = (InventoryItem.objects.filter(branch__in=branches, quantity_on_hand__gt=0)
             .select_related("product", "branch").order_by("-quantity_on_hand"))
    out = []
    for it in items:
        if (it.branch_id, it.product_id) in sold_pairs:
            continue
        out.append({"branch": it.branch.code, "sku": it.product.sku, "name": it.product.name,
                    "on_hand": it.quantity_on_hand,
                    "value_paisa": it.quantity_on_hand * it.weighted_avg_cost_paisa})
        if len(out) >= limit:
            break
    return out


def low_stock_alerts(branches, limit=50):
    items = (InventoryItem.objects.filter(branch__in=branches)
             .filter(Q(quantity_on_hand__lte=F("reorder_point"), reorder_point__gt=0) | Q(quantity_on_hand__lte=0))
             .select_related("product", "branch").order_by("quantity_on_hand")[:limit])
    return [{"branch": i.branch.code, "product_id": i.product_id, "sku": i.product.sku, "name": i.product.name,
             "on_hand": i.quantity_on_hand, "reorder_point": i.reorder_point, "reorder_qty": i.reorder_qty}
            for i in items]


def stock_conflicts(branches, limit=50):
    return list(
        Sale.objects.filter(branch__in=branches, stock_conflict=True, conflict_resolution="")
        .values("id", "branch__code", "receipt_number", "total_paisa", "paid_at", "offline")
        .order_by("-paid_at")[:limit]
    )


def fonepay_mismatches(org, limit=50):
    return list(FonepayMismatch.objects.filter(resolved=False, branch__org=org)
                .values("id", "branch__code", "prn", "kind", "detail", "found_at").order_by("-found_at")[:limit])

