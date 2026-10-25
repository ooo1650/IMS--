from django.core.exceptions import ValidationError
from django.test import TestCase

from catalog.models import Supplier
from ims.testutils import World
from purchasing import services
from purchasing.models import PurchaseOrder, PurchaseOrderLine


class PurchasingTests(TestCase):
    def setUp(self):
        self.w = World()
        self.po = PurchaseOrder.objects.create(branch=self.w.a, supplier=Supplier.objects.create(name="S"),
                                               created_by=self.w.manager_a)
        self.line = PurchaseOrderLine.objects.create(po=self.po, product=self.w.p1, qty_ordered=10,
                                                     unit_cost_paisa=8000)
        services.mark_ordered(self.po, self.w.manager_a)

    def test_partial_then_full_receipt(self):
        services.receive_goods(self.po, self.w.manager_a, [(self.line.id, 4, None)])
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.PARTIAL)
        # Actual invoice cost differs from ordered cost; that's what feeds the average.
        services.receive_goods(self.po, self.w.manager_a, [(self.line.id, 6, 9000)])
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.RECEIVED)
        item = self.w.item(self.w.a, self.w.p1)
        self.assertEqual(item.quantity_on_hand, 20)
        # (10*7000 + 4*8000) / 14 = 7285.71 -> 7286; then (14*7286 + 6*9000)/20 = 7800.2 -> 7800
        self.assertEqual(item.weighted_avg_cost_paisa, 7800)

    def test_cannot_over_receive(self):
        with self.assertRaises(ValidationError):
            services.receive_goods(self.po, self.w.manager_a, [(self.line.id, 11, None)])

    def test_cannot_receive_draft_or_cancelled(self):
        services.cancel_po(self.po, self.w.manager_a)
        with self.assertRaises(ValidationError):
            services.receive_goods(self.po, self.w.manager_a, [(self.line.id, 1, None)])
