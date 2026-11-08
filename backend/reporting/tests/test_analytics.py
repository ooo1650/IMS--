import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from catalog.models import Category
from ims.testutils import World
from sales import services

URL = "/api/v1/reports/analytics"


class AnalyticsTests(TestCase):
    """P1 sells at 100.00 + 13% VAT = 113.00, cost 70.00 → profit 30.00 a unit."""

    def setUp(self):
        self.w = World()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)
        self.w.open_shift(self.w.reg_b, self.w.cashier_b)
        self.w.p1.category = Category.objects.create(org=self.w.org, name="Grocery")
        self.w.p1.save()
        now = timezone.now()
        self.sell(self.w.a, 2, now)                       # A, current period: 226.00
        self.sell(self.w.b, 1, now)                       # B, current period: 113.00
        self.sell(self.w.a, 1, now - timedelta(days=8))   # A, previous 7-day period: 113.00

    def sell(self, branch, qty, when, method="cash"):
        reg, cashier = (self.w.reg_a, self.w.cashier_a) if branch == self.w.a else (self.w.reg_b, self.w.cashier_b)
        return services.checkout_cash(branch=branch, register=reg, cashier=cashier,
                                      cart=self.w.cart((self.w.p1, qty)), tendered_paisa=10 ** 8,
                                      idempotency_key=str(uuid.uuid4()), method=method,
                                      payment_ref="REF" if method != "cash" else "", now=when)[0]

    def test_org_totals_compare_with_previous_period(self):
        r = self.w.client(self.w.admin).get(URL, {"days": 7})
        self.assertEqual(r.status_code, 200, r.data)
        cur, prev = r.data["current"], r.data["previous"]
        self.assertEqual((cur["revenue_paisa"], cur["transactions"], cur["items"]), (33900, 2, 3))
        self.assertEqual(cur["avg_basket_paisa"], 16950)
        self.assertEqual((cur["net_sales_paisa"], cur["gross_profit_paisa"], cur["margin_bp"]), (30000, 9000, 3000))
        self.assertEqual((prev["revenue_paisa"], prev["transactions"]), (11300, 1))

    def test_daily_series_lines_up_with_previous_period(self):
        r = self.w.client(self.w.admin).get(URL, {"days": 7})
        daily = r.data["daily"]
        self.assertEqual(len(daily), 7)
        self.assertEqual(daily[-1]["revenue_paisa"], 33900)
        self.assertEqual(daily[-1]["branches"], {"A": 22600, "B": 11300})
        self.assertEqual(daily[-1]["profit_paisa"], 9000)
        # 8 days ago is the second-to-last day of the previous 7-day window.
        self.assertEqual(sum(d["previous_revenue_paisa"] for d in daily), 11300)
        self.assertEqual(daily[-2]["previous_revenue_paisa"], 11300)

    def test_breakdowns(self):
        self.sell(self.w.a, 1, timezone.now(), method="card")
        d = self.w.client(self.w.admin).get(URL, {"days": 7}).data
        self.assertEqual({m["method"]: m["amount_paisa"] for m in d["payment_mix"]}, {"cash": 33900, "card": 11300})
        self.assertEqual(d["categories"][0]["category"], "Grocery")
        self.assertEqual(d["categories"][0]["margin_bp"], 3000)
        self.assertEqual(d["top_products"][0]["qty"], 4)
        self.assertEqual(sum(c["transactions"] for c in d["heatmap"]), 3)
        self.assertEqual([b["code"] for b in d["branches"]], ["A", "B"])
        self.assertEqual(d["branches"][0]["previous_revenue_paisa"], 11300)

    def test_org_user_can_pick_one_branch(self):
        d = self.w.client(self.w.admin).get(URL, {"days": 7, "branch": self.w.b.id}).data
        self.assertEqual(d["current"]["revenue_paisa"], 11300)
        self.assertEqual(d["branches"], [])  # no comparison table for one branch

    def test_branch_user_only_sees_active_branch(self):
        c = self.w.client(self.w.manager_a, self.w.a)
        d = c.get(URL, {"days": 7, "branch": self.w.b.id}).data  # branch param ignored
        self.assertEqual(d["current"]["revenue_paisa"], 22600)
        self.assertEqual(d["branch_codes"], ["A"])

    def test_permissions_and_bad_period(self):
        self.assertEqual(self.w.client(self.w.cashier_a, self.w.a).get(URL).status_code, 403)
        self.assertEqual(self.w.client(self.w.admin).get(URL, {"days": 12}).status_code, 400)
        self.assertEqual(self.w.client(self.w.admin).get(URL, {"branch": 99999}).status_code, 404)

    def test_returns_are_counted(self):
        sale = self.sell(self.w.a, 1, timezone.now())
        services.create_return(sale=sale, register=self.w.reg_a, cashier=self.w.manager_a,
                               items=[(sale.lines.get().id, 1)], reason="damaged")
        d = self.w.client(self.w.admin).get(URL, {"days": 7}).data
        self.assertEqual((d["current"]["returns_paisa"], d["current"]["returns_count"]), (11300, 1))
