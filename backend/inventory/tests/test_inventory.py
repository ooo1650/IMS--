from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import SimpleTestCase, TestCase

from ims.testutils import World
from inventory import services
from inventory.models import StockMovement, StockTransfer, StockTransferLine
from inventory.services import Receipt, weighted_average


class WeightedAverageTests(SimpleTestCase):
    def test_formula(self):
        # 10 @ 70.00 + 10 @ 80.00 -> 75.00
        self.assertEqual(weighted_average(10, 7000, 10, 8000), 7500)

    def test_rounds_half_up_to_paisa(self):
        self.assertEqual(weighted_average(2, 100, 1, 101), 100)  # 100.33
        self.assertEqual(weighted_average(1, 100, 1, 101), 101)  # 100.5 -> 101

    def test_zero_or_negative_on_hand_takes_received_cost(self):
        self.assertEqual(weighted_average(0, 7000, 5, 9000), 9000)
        self.assertEqual(weighted_average(-3, 7000, 5, 9000), 9000)


class StockTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_receive_recomputes_average(self):
        with transaction.atomic():
            services.receive_stock(self.w.a, [Receipt(self.w.p1.id, 10, 9000)], StockMovement.PURCHASE_IN, self.w.admin)
        item = self.w.item(self.w.a, self.w.p1)
        self.assertEqual((item.quantity_on_hand, item.weighted_avg_cost_paisa), (20, 8000))

    def test_adjust_needs_reason(self):
        with self.assertRaises(ValidationError):
            services.adjust_stock(self.w.a, self.w.p1.id, -1, self.w.clerk_a, "")
        services.adjust_stock(self.w.a, self.w.p1.id, -2, self.w.clerk_a, "damaged")
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 8)
        self.assertEqual(StockMovement.objects.filter(type="adjust_out").get().qty, -2)

    def test_cached_on_hand_matches_movements(self):
        services.adjust_stock(self.w.a, self.w.p1.id, 5, self.w.clerk_a, "found")
        total = sum(StockMovement.objects.filter(branch=self.w.a, product=self.w.p1).values_list("qty", flat=True))
        self.assertEqual(total, self.w.on_hand(self.w.a, self.w.p1))


class TransferTests(TestCase):
    def setUp(self):
        self.w = World()
        # Give branch B a different cost so the travelling cost matters.
        StockMovement.objects.all()
        self.t = StockTransfer.objects.create(from_branch=self.w.a, to_branch=self.w.b, by=self.w.manager_a)
        self.line = StockTransferLine.objects.create(transfer=self.t, product=self.w.p1, qty_sent=4)

    def test_full_flow_with_partial_receipt(self):
        with transaction.atomic():
            services.dispatch_transfer(self.t, self.w.manager_a)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 6)
        self.assertEqual(self.w.on_hand(self.w.b, self.w.p1), 10)  # in transit: neither branch
        with self.assertRaises(ValidationError):
            services.receive_transfer(self.t, {self.line.id: (3, "")}, self.w.admin)  # short needs reason
        with transaction.atomic():
            services.receive_transfer(self.t, {self.line.id: (3, "one broken")}, self.w.admin)
        self.assertEqual(self.w.on_hand(self.w.b, self.w.p1), 13)
        loss = StockMovement.objects.get(branch=self.w.b, type="adjust_out")
        self.assertEqual(loss.qty, -1)
        self.assertIn("transfer loss", loss.reason)
        self.t.refresh_from_db()
        self.assertEqual(self.t.status, StockTransfer.RECEIVED)

    def test_cost_travels_with_stock(self):
        with transaction.atomic():
            services.dispatch_transfer(self.t, self.w.manager_a)
            services.receive_transfer(self.t, {}, self.w.admin)
        mv_in = StockMovement.objects.get(branch=self.w.b, type="transfer_in")
        self.assertEqual(mv_in.unit_cost_paisa, 7000)

    def test_cannot_dispatch_more_than_on_hand(self):
        self.line.qty_sent = 50
        self.line.save()
        with self.assertRaises(ValidationError):
            services.dispatch_transfer(self.t, self.w.manager_a)

    def test_cancel_in_transit_compensates(self):
        with transaction.atomic():
            services.dispatch_transfer(self.t, self.w.manager_a)
            services.cancel_transfer(self.t, self.w.manager_a)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 10)


class StocktakeTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_finish_writes_variance_adjustments(self):
        with transaction.atomic():
            st = services.start_stocktake(self.w.a, self.w.clerk_a)
        lines = {l.product_id: l for l in st.lines.all()}
        lines[self.w.p1.id].counted_qty = 8
        lines[self.w.p1.id].save()
        lines[self.w.p2.id].counted_qty = 10
        lines[self.w.p2.id].save()
        with transaction.atomic():
            services.finish_stocktake(st, self.w.clerk_a)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 8)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p3), 10)  # uncounted lines untouched
        self.assertEqual(StockMovement.objects.filter(type="stocktake_adjust").count(), 1)

    def test_big_variance_needs_reason(self):
        with transaction.atomic():
            st = services.start_stocktake(self.w.a, self.w.clerk_a)
        line = st.lines.get(product=self.w.p1)
        line.counted_qty = 0
        line.save()
        with self.assertRaises(ValidationError):
            services.finish_stocktake(st, self.w.clerk_a)


class InventorySummaryApiTests(TestCase):
    def setUp(self):
        self.w = World()
        services.adjust_stock(self.w.a, self.w.p1.id, -10, self.w.admin, "count")  # P1 -> 0
        item = self.w.item(self.w.a, self.w.p2)
        item.reorder_point = 10  # P2 at 10 -> low
        item.save()

    def test_summary_counts_this_branch(self):
        r = self.w.client(self.w.clerk_a, self.w.a).get("/api/v1/inventory/summary/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.data["items"], r.data["low_stock"], r.data["out_of_stock"], r.data["negative_stock"]), (3, 1, 1, 0))
        self.assertEqual(r.data["inventory_value_paisa"], 10 * 4000 + 10 * 1500)

    def test_out_filter(self):
        r = self.w.client(self.w.clerk_a, self.w.a).get("/api/v1/inventory/?out=1")
        self.assertEqual([i["sku"] for i in r.data["results"]], ["P1"])

    def test_summary_needs_inventory_view(self):
        r = self.w.client(self.w.cashier_a, self.w.a).get("/api/v1/inventory/summary/")
        self.assertEqual(r.status_code, 403)
