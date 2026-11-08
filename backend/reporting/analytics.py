"""Sales analytics for the report screens: a period (last N local days, today
included) compared with the N days before it.

* Revenue = what customers paid (invoice totals, VAT included) on paid sales.
* Net sales = line value before VAT, excise and levies.
* Gross profit = net sales - cost of goods (the weighted-average cost
  snapshotted on each sale line at checkout).
* Returns = credit notes issued in the period (shown separately, not netted).

Days and hours are each branch's local time, like the rest of the dashboard.
All money is integer paisa.
"""
from datetime import timedelta

from django.db.models import Count, F, Sum
from django.db.models.functions import ExtractHour, ExtractIsoWeekDay, TruncDate

from sales.models import CreditNote, Payment, Sale, SaleLine

from .queries import local_day_start

PERIODS = (7, 30, 90)
TOP_CATEGORIES = 7  # the rest fold into "Other"
TOP_PRODUCTS = 10

NET = F("line_total_paisa") - F("tax_amount_paisa") - F("excise_paisa") - F("levy_paisa")
COST = F("unit_cost_paisa") * F("qty")


def _window(branch, days, offset=0):
    """[start, end) for one branch: the last ``days`` local days, shifted back ``offset`` periods."""
    end = local_day_start(branch, -1 + offset * days)  # midnight after today, minus the offset
    return end - timedelta(days=days), end


def _sales(branch, start, end):
    return Sale.objects.filter(branch=branch, status=Sale.PAID, paid_at__gte=start, paid_at__lt=end)


def _lines(branch, start, end):
    return SaleLine.objects.filter(sale__branch=branch, sale__status=Sale.PAID,
                                   sale__paid_at__gte=start, sale__paid_at__lt=end)


def _totals(branches, days, offset):
    revenue = count = net = cost = items = discount = returns = returns_count = 0
    for b in branches:
        start, end = _window(b, days, offset)
        s = _sales(b, start, end).aggregate(r=Sum("total_paisa"), n=Count("id"), d=Sum("discount_total_paisa"))
        l = _lines(b, start, end).aggregate(net=Sum(NET), cost=Sum(COST), q=Sum("qty"))
        c = CreditNote.objects.filter(branch=b, at__gte=start, at__lt=end).aggregate(t=Sum("total_paisa"),
                                                                                      n=Count("id"))
        revenue += s["r"] or 0
        count += s["n"]
        discount += s["d"] or 0
        net += l["net"] or 0
        cost += l["cost"] or 0
        items += l["q"] or 0
        returns += c["t"] or 0
        returns_count += c["n"]
    profit = net - cost
    return {
        "revenue_paisa": revenue, "transactions": count, "items": items,
        "avg_basket_paisa": round(revenue / count) if count else 0,
        "net_sales_paisa": net, "cost_paisa": cost, "gross_profit_paisa": profit,
        "margin_bp": round(profit * 10000 / net) if net else None,  # basis points of net sales
        "discount_paisa": discount, "returns_paisa": returns, "returns_count": returns_count,
    }


def _daily(branches, days):
    """One row per local day: revenue, transactions and profit in total and
    revenue per branch, plus the same day-index of the previous period."""
    by_day, prev_by_index = {}, [0] * days
    any_start = None
    for b in branches:
        start, end = _window(b, days)
        any_start = any_start or start
        rows = (_sales(b, start, end).annotate(d=TruncDate("paid_at", tzinfo=b.tz)).values("d")
                .annotate(r=Sum("total_paisa"), n=Count("id")))
        for r in rows:
            day = by_day.setdefault(r["d"], {"revenue": 0, "transactions": 0, "profit": 0, "branches": {}})
            day["revenue"] += r["r"]
            day["transactions"] += r["n"]
            day["branches"][b.code] = day["branches"].get(b.code, 0) + r["r"]
        for r in (_lines(b, start, end).annotate(d=TruncDate("sale__paid_at", tzinfo=b.tz)).values("d")
                  .annotate(p=Sum(NET - COST))):
            by_day.setdefault(r["d"], {"revenue": 0, "transactions": 0, "profit": 0, "branches": {}})["profit"] += r["p"]
        pstart, pend = _window(b, days, offset=1)
        first = pstart.date()
        for r in (_sales(b, pstart, pend).annotate(d=TruncDate("paid_at", tzinfo=b.tz)).values("d")
                  .annotate(r=Sum("total_paisa"))):
            i = (r["d"] - first).days
            if 0 <= i < days:
                prev_by_index[i] += r["r"]
    first_day = any_start.date()
    out = []
    for i in range(days):
        d = first_day + timedelta(days=i)
        row = by_day.get(d, {"revenue": 0, "transactions": 0, "profit": 0, "branches": {}})
        out.append({"date": d.isoformat(), "revenue_paisa": row["revenue"], "transactions": row["transactions"],
                    "profit_paisa": row["profit"], "previous_revenue_paisa": prev_by_index[i],
                    "branches": {b.code: row["branches"].get(b.code, 0) for b in branches}})
    return out


def _heatmap(branches, days):
    """Revenue and sale count by local weekday (1=Mon..7=Sun, ISO) x hour."""
    cells = {}
    for b in branches:
        start, end = _window(b, days)
        rows = (_sales(b, start, end)
                .annotate(wd=ExtractIsoWeekDay("paid_at", tzinfo=b.tz), h=ExtractHour("paid_at", tzinfo=b.tz))
                .values("wd", "h").annotate(r=Sum("total_paisa"), n=Count("id")))
        for r in rows:
            c = cells.setdefault((r["wd"], r["h"]), [0, 0])
            c[0] += r["r"]
            c[1] += r["n"]
    return [{"weekday": wd, "hour": h, "revenue_paisa": v[0], "transactions": v[1]}
            for (wd, h), v in sorted(cells.items())]


def _payment_mix(branches, days):
    mix = {}
    for b in branches:
        start, end = _window(b, days)
        rows = (Payment.objects.filter(sale__in=_sales(b, start, end), status=Payment.SUCCESS)
                .values("method").annotate(a=Sum("amount_paisa"), n=Count("id")))
        for r in rows:
            m = mix.setdefault(r["method"], [0, 0])
            m[0] += r["a"]
            m[1] += r["n"]
    return sorted(({"method": k, "amount_paisa": v[0], "count": v[1]} for k, v in mix.items()),
                  key=lambda r: -r["amount_paisa"])


def _by_product_and_category(branches, days):
    products, categories = {}, {}
    for b in branches:
        start, end = _window(b, days)
        rows = (_lines(b, start, end)
                .values("product_id", "product__sku", "product__name", "product__category__name")
                .annotate(q=Sum("qty"), r=Sum("line_total_paisa"), net=Sum(NET), cost=Sum(COST)))
        for r in rows:
            p = products.setdefault(r["product_id"], {"product_id": r["product_id"], "sku": r["product__sku"],
                                                      "name": r["product__name"], "qty": 0, "revenue_paisa": 0,
                                                      "net_paisa": 0, "profit_paisa": 0})
            p["qty"] += r["q"]
            p["revenue_paisa"] += r["r"]
            p["net_paisa"] += r["net"]
            p["profit_paisa"] += r["net"] - r["cost"]
            name = r["product__category__name"] or "Uncategorised"
            c = categories.setdefault(name, {"category": name, "revenue_paisa": 0, "net_paisa": 0, "profit_paisa": 0})
            c["revenue_paisa"] += r["r"]
            c["net_paisa"] += r["net"]
            c["profit_paisa"] += r["net"] - r["cost"]

    def margin(row):
        row["margin_bp"] = round(row["profit_paisa"] * 10000 / row["net_paisa"]) if row["net_paisa"] else None
        return row

    top = [margin(p) for p in sorted(products.values(), key=lambda p: -p["revenue_paisa"])[:TOP_PRODUCTS]]
    cats = sorted(categories.values(), key=lambda c: -c["revenue_paisa"])
    if len(cats) > TOP_CATEGORIES + 1:
        rest = cats[TOP_CATEGORIES:]
        cats = cats[:TOP_CATEGORIES] + [{
            "category": f"Other ({len(rest)})", "other": True,
            **{k: sum(c[k] for c in rest) for k in ("revenue_paisa", "net_paisa", "profit_paisa")}}]
    return top, [margin(c) for c in cats]


def _branches(branches, days):
    if len(branches) < 2:
        return []
    out = []
    for b in branches:
        cur, prev = _totals([b], days, 0), _totals([b], days, 1)
        out.append({"code": b.code, "name": b.name, "revenue_paisa": cur["revenue_paisa"],
                    "previous_revenue_paisa": prev["revenue_paisa"], "transactions": cur["transactions"],
                    "avg_basket_paisa": cur["avg_basket_paisa"], "margin_bp": cur["margin_bp"],
                    "gross_profit_paisa": cur["gross_profit_paisa"]})
    return sorted(out, key=lambda r: -r["revenue_paisa"])


def analytics(branches, days):
    branches = list(branches)
    if not branches:
        return {"days": days, "current": _totals([], days, 0), "previous": _totals([], days, 1), "daily": [],
                "heatmap": [], "payment_mix": [], "categories": [], "top_products": [], "branches": []}
    top, categories = _by_product_and_category(branches, days)
    return {
        "days": days,
        "branch_codes": [b.code for b in branches],
        "current": _totals(branches, days, 0),
        "previous": _totals(branches, days, 1),
        "daily": _daily(branches, days),
        "heatmap": _heatmap(branches, days),
        "payment_mix": _payment_mix(branches, days),
        "categories": categories,
        "top_products": top,
        "branches": _branches(branches, days),
    }
